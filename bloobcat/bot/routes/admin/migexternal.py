import asyncio

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bloobcat.bot.routes.admin.functions import IsAdmin
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.settings import remnawave_settings

logger = get_logger("admin_migexternal")
router = Router()


@router.message(Command("migexternal"), IsAdmin())
async def migexternal(message: Message):
    """
    Проставляет externalSquadUuid всем пользователям, которые уже созданы в RemnaWave.
    Команда: /migexternal
    Опционально: /migexternal recreate — пересоздавать пользователей, отсутствующих в панели, и повторять обновление.
    """
    parts = (message.text or "").strip().split()
    recreate_missing = any(p.lower() in ("recreate", "--recreate", "--recreate-missing") for p in parts[1:])

    external_uuid = remnawave_settings.default_external_squad_uuid
    if not external_uuid:
        await message.answer(
            "❌ Не задан REMNAWAVE_DEFAULT_EXTERNAL_SQUAD_UUID.\n"
            "Добавьте переменную окружения и перезапустите сервис."
        )
        return

    await message.answer(
        "Запускаю миграцию external squad…\n"
        f"externalSquadUuid: <code>{external_uuid}</code>\n"
        f"Режим пересоздания отсутствующих: <b>{'включен' if recreate_missing else 'выключен'}</b>\n"
        "Это может занять некоторое время.",
        parse_mode="HTML",
    )

    users = await Users.all()
    targets = [u for u in users if u.remnawave_uuid]
    total = len(targets)
    if total == 0:
        await message.answer("Нет пользователей с RemnaWave UUID — нечего мигрировать.")
        return

    client = RemnaWaveClient(
        remnawave_settings.url, remnawave_settings.token.get_secret_value()
    )

    ok = 0
    recreated = 0
    failed = 0
    errors_preview: list[str] = []

    # Ограничиваем параллелизм, чтобы не положить панель
    sem = asyncio.Semaphore(10)

    async def process_one(user: Users):
        nonlocal ok, recreated, failed
        async with sem:
            try:
                await client.users.update_user(
                    str(user.remnawave_uuid),
                    externalSquadUuid=external_uuid,
                )
                ok += 1
                return
            except Exception as e:
                err_text = str(e)
                # Если пользователя нет в панели — по умолчанию НЕ пересоздаем.
                # Включается только опцией /migexternal recreate
                if recreate_missing and any(
                    token in err_text
                    for token in ["User not found", "A039", "A063", "Update user error"]
                ):
                    try:
                        recreated_ok = await user.recreate_remnawave_user()
                        if recreated_ok and user.remnawave_uuid:
                            await client.users.update_user(
                                str(user.remnawave_uuid),
                                externalSquadUuid=external_uuid,
                            )
                            ok += 1
                            recreated += 1
                            return
                    except Exception as e2:
                        err_text = f"{err_text} | recreate_failed: {e2}"

                failed += 1
                if len(errors_preview) < 10:
                    errors_preview.append(f"{user.id}: {err_text}")

    try:
        await asyncio.gather(*(process_one(u) for u in targets))
    finally:
        await client.close()

    logger.info(
        "migexternal finished: total=%s ok=%s recreated=%s failed=%s",
        total,
        ok,
        recreated,
        failed,
    )

    text = (
        "✅ Миграция external squad завершена.\n"
        f"Всего пользователей: <b>{total}</b>\n"
        f"Успешно: <b>{ok}</b>\n"
        f"Из них пересоздано в панели: <b>{recreated}</b>\n"
        f"Ошибок: <b>{failed}</b>"
    )
    await message.answer(text, parse_mode="HTML")

    if errors_preview:
        preview_text = "⚠️ Примеры ошибок (до 10):\n\n" + "\n".join(
            f"<code>{line}</code>" for line in errors_preview
        )
        await message.answer(preview_text, parse_mode="HTML")


