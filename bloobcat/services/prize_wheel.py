import random
from typing import Optional, Dict, Any

from tortoise.expressions import F

from bloobcat.db.prize_wheel import (
    PrizeWheelHistory,
    PrizeWheelConfig,
)
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.bot.bot import bot as tg_bot
from bloobcat.bot.notifications.admin import write_to
from bloobcat.bot.notifications.prize_wheel import notify_prize_won


logger = get_logger("prize_wheel_service")


class PrizeWheelService:
    """Сервис для работы с колесом призов"""

    @staticmethod
    async def get_user_attempts(user_id: int) -> int:
        user = await Users.get(id=user_id)
        # В текущем проекте поле попыток отсутствует — возвращаем 0 по умолчанию
        # После добавления поля в Users (prize_wheel_attempts) можно читать из него
        return int(getattr(user, "prize_wheel_attempts", 0) or 0)

    @staticmethod
    async def spin_wheel(user_id: int, bot=None) -> Optional[Dict[str, Any]]:
        """Крутит колесо призов для пользователя.
        Требует наличия хотя бы 1 попытки у пользователя (будет добавлено миграцией).
        """
        # Атомарное списание попытки
        updated = await Users.filter(id=user_id, prize_wheel_attempts__gt=0).update(
            prize_wheel_attempts=F("prize_wheel_attempts") - 1
        )
        if updated == 0:
            logger.info(f"[PRIZE_WHEEL] spin_denied_no_attempts user={user_id}")
            return None

        prizes = await PrizeWheelConfig.get_active_prizes()
        if not prizes:
            logger.error("Нет активных призов в конфигурации")
            return None

        selected_prize = PrizeWheelService._select_prize_by_probability(prizes)
        if not selected_prize:
            logger.error("Не удалось выбрать приз")
            return None

        history_entry = await PrizeWheelHistory.create(
            user_id=user_id,
            prize_type=selected_prize.prize_type,
            prize_name=selected_prize.prize_name,
            prize_value=selected_prize.prize_value,
        )

        await PrizeWheelService._process_prize(user_id, selected_prize, history_entry)

        # Отправляем уведомления в канал логов и админам (как в AzizVPN)
        if bot is not None:
            try:
                await notify_prize_won(
                    user_id=user_id,
                    prize_type=str(selected_prize.prize_type),
                    prize_name=selected_prize.prize_name,
                    prize_value=selected_prize.prize_value,
                    history_id=history_entry.id,
                    requires_admin=bool(getattr(selected_prize, "requires_admin", False)),
                    bot=bot,
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомлений о призе: {e}")

        return {
            "prize_type": selected_prize.prize_type,
            "prize_name": selected_prize.prize_name,
            "prize_value": selected_prize.prize_value,
            "requires_admin": bool(getattr(selected_prize, "requires_admin", False)),
            "history_id": history_entry.id,
        }

    @staticmethod
    def _select_prize_by_probability(prizes: list) -> Optional[PrizeWheelConfig]:
        # Берем только не-пустышки (NOTHING отсутствует как фикс. тип; пустышка = остаток)
        configured_non_empty = [p for p in prizes]
        weights = [(p, max(0.0, float(p.probability))) for p in configured_non_empty]
        sum_non_empty = sum(w for _, w in weights)

        # Остаток уходит в пустышку
        empty_prob = max(0.0, 1.0 - sum_non_empty)

        class _EmptyPrize:
            prize_type = "nothing"
            prize_name = "Пустышка"
            prize_value = "Ничего"
            requires_admin = False

            def __init__(self, probability: float):
                self.probability = probability

        if empty_prob > 0:
            weights.append((_EmptyPrize(empty_prob), empty_prob))

        total_probability = sum(w for _, w in weights)
        if total_probability <= 0:
            return prizes[0] if prizes else None

        normalized = [(p, w / total_probability) for p, w in weights]
        rnd = random.random()
        cum = 0.0
        for prize, prob in normalized:
            cum += prob
            if rnd <= cum:
                return prize
        return (configured_non_empty[0] if configured_non_empty else (prizes[0] if prizes else None))

    @staticmethod
    async def _process_prize(user_id: int, prize: PrizeWheelConfig, history_entry: PrizeWheelHistory) -> None:
        try:
            user = await Users.get(id=user_id)
            ptype = str(prize.prize_type)

            if ptype == "subscription":
                # Если приз требует участия админа — не начисляем сразу, ждём подтверждения
                if bool(getattr(prize, "requires_admin", False)):
                    logger.info(
                        f"Пользователь {user_id} выиграл подписку (требуется админ), отложенная выдача"
                    )
                    # Ничего не помечаем; выдача произойдёт в handle_prize_confirmation
                    return
                # В prize_value ожидаем число дней, например '15' -> 15 дней
                try:
                    days = int(str(prize.prize_value).strip())
                    await user.extend_subscription(days)
                    await history_entry.mark_as_claimed()
                    logger.info(f"Пользователь {user_id} получил {days} дней подписки")
                    return
                except Exception as e:
                    logger.error(f"Невалидное значение дней подписки '{prize.prize_value}': {e}")

            if ptype == "extra_spin":
                user.prize_wheel_attempts = int(getattr(user, "prize_wheel_attempts", 0) or 0) + 1
                await user.save()
                await history_entry.mark_as_claimed()
                logger.info(f"Пользователь {user_id} получил дополнительную попытку (+1)")

            elif ptype == "nothing":
                await history_entry.mark_as_claimed()
                logger.info(f"Пользователь {user_id} выиграл пустышку")

            elif ptype == "material_prize" or getattr(prize, "requires_admin", False):
                logger.info(
                    f"Пользователь {user_id} выиграл {prize.prize_name} — требуется участие админа"
                )

            else:
                await history_entry.mark_as_claimed()
                logger.info(
                    f"Пользователь {user_id} получил приз {prize.prize_name} ({ptype}) — авто-завершение"
                )
        except Exception as e:
            logger.error(
                f"Ошибка при обработке приза {getattr(prize, 'prize_type', '?')} для пользователя {user_id}: {e}"
            )

    @staticmethod
    async def get_user_history(user_id: int, limit: int = 10) -> list:
        history = (
            await PrizeWheelHistory.filter(user_id=user_id)
            .order_by("-created_at")
            .limit(limit)
        )
        return [
            {
                "prize_name": entry.prize_name,
                "prize_value": entry.prize_value,
                "is_claimed": entry.is_claimed,
                "is_rejected": getattr(entry, "is_rejected", False),
                "created_at": entry.created_at.isoformat(),
            }
            for entry in history
        ]

    @staticmethod
    async def get_prizes_config() -> list:
        prizes = await PrizeWheelConfig.get_active_prizes()
        return [
            {
                "type": prize.prize_type,
                "name": prize.prize_name,
                "value": prize.prize_value,
                "probability": prize.probability,
                "requires_admin": prize.requires_admin,
            }
            for prize in prizes
        ]

    @staticmethod
    async def initialize_default_prizes() -> None:
        default_prizes = [
            {
                "prize_type": "subscription",
                "prize_name": "Подписка (7 дней)",
                "prize_value": "7",
                "probability": 0.05,
                "requires_admin": False,
            },
            {
                "prize_type": "subscription",
                "prize_name": "Подписка (14 дней)",
                "prize_value": "14",
                "probability": 0.03,
                "requires_admin": False,
            },
            {
                "prize_type": "extra_spin",
                "prize_name": "Еще одна попытка",
                "prize_value": "1",
                "probability": 0.2,
                "requires_admin": False,
            },
        ]

        for prize_data in default_prizes:
            await PrizeWheelConfig.get_or_create(
                prize_type=prize_data["prize_type"], defaults=prize_data
            )

        # На всякий случай — деактивируем явно заданную пустышку, если кто-то ее добавил вручную
        # NOTHING как тип не хранится — пустышка считается остатком

        logger.info("Инициализированы призы по умолчанию для колеса призов")


