import asyncio

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bloobcat.bot.routes.admin.functions import IsAdmin
from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.routes.remnawave.lte_utils import set_lte_squad_status
from bloobcat.settings import remnawave_settings

logger = get_logger("admin_miglte")
router = Router()


@router.message(Command("miglte"), IsAdmin())
async def miglte(message: Message):
    """
    Проставляет LTE internal squad всем пользователям с остатком LTE (>0).
    Команда: /miglte
    Опционально: /miglte recreate — пересоздавать пользователей, отсутствующих в панели.
    """
    parts = (message.text or "").strip().split()
    recreate_missing = any(
        p.lower() in ("recreate", "--recreate", "--recreate-missing") for p in parts[1:]
    )

    lte_uuid = remnawave_settings.lte_internal_squad_uuid
    if not lte_uuid:
        await message.answer(
            "❌ Не задан REMNAWAVE_LTE_INTERNAL_SQUAD_UUID.\n"
            "Добавьте переменную окружения и перезапустите сервис."
        )
        return

    await message.answer(
        "Запускаю миграцию LTE squad…\n"
        f"lte_internal_squad_uuid: <code>{lte_uuid}</code>\n"
        f"Режим пересоздания отсутствующих: <b>{'включен' if recreate_missing else 'выключен'}</b>\n"
        "Условие: остаток LTE > 0.\n"
        "Это может занять некоторое время.",
        parse_mode="HTML",
    )

    active_tariffs = await ActiveTariffs.filter(lte_gb_total__gt=0).prefetch_related("user")
    targets: dict[str, Users] = {}
    for tariff in active_tariffs:
        user = tariff.user
        if not user or not user.remnawave_uuid:
            continue
        remaining_gb = float(tariff.lte_gb_total or 0) - float(tariff.lte_gb_used or 0)
        if remaining_gb <= 0:
            continue
        targets[str(user.remnawave_uuid)] = user

    total = len(targets)
    if total == 0:
        await message.answer("Нет пользователей с остатком LTE — нечего мигрировать.")
        return

    client = RemnaWaveClient(
        remnawave_settings.url, remnawave_settings.token.get_secret_value()
    )

    ok = 0
    skipped = 0
    recreated = 0
    failed = 0
    errors_preview: list[str] = []

    sem = asyncio.Semaphore(10)

    async def process_one(user_uuid: str, user: Users):
        nonlocal ok, skipped, recreated, failed
        async with sem:
            try:
                changed = await set_lte_squad_status(
                    user_uuid, enable=True, client=client
                )
                if changed:
                    ok += 1
                else:
                    skipped += 1
                return
            except Exception as e:
                err_text = str(e)
                if recreate_missing and any(
                    token in err_text
                    for token in ["User not found", "A039", "A063", "Update user error"]
                ):
                    try:
                        recreated_ok = await user.recreate_remnawave_user()
                        if recreated_ok and user.remnawave_uuid:
                            changed = await set_lte_squad_status(
                                str(user.remnawave_uuid),
                                enable=True,
                                client=client,
                            )
                            if changed:
                                ok += 1
                            else:
                                skipped += 1
                            recreated += 1
                            return
                    except Exception as e2:
                        err_text = f"{err_text} | recreate_failed: {e2}"

                failed += 1
                if len(errors_preview) < 10:
                    errors_preview.append(f"{user.id}: {err_text}")

    try:
        await asyncio.gather(
            *(process_one(uuid, user) for uuid, user in targets.items())
        )
    finally:
        await client.close()

    logger.info(
        "miglte finished: total=%s ok=%s skipped=%s recreated=%s failed=%s",
        total,
        ok,
        skipped,
        recreated,
        failed,
    )

    text = (
        "✅ Миграция LTE squad завершена.\n"
        f"Всего пользователей: <b>{total}</b>\n"
        f"Добавлено: <b>{ok}</b>\n"
        f"Уже был в LTE скваде: <b>{skipped}</b>\n"
        f"Из них пересоздано в панели: <b>{recreated}</b>\n"
        f"Ошибок: <b>{failed}</b>"
    )
    await message.answer(text, parse_mode="HTML")

    if errors_preview:
        preview_text = "⚠️ Примеры ошибок (до 10):\n\n" + "\n".join(
            f"<code>{line}</code>" for line in errors_preview
        )
        await message.answer(preview_text, parse_mode="HTML")
