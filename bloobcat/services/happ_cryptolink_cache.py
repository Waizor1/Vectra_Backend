"""DB-кэш crypt5 happ://-линков, вычисленных из raw subscriptionUrl.

Стратегия: ``crypto.happ.su`` (публичный API Happ, crypt5) → при сбое падаем
на панельный ``/api/system/tools/happ/encrypt`` (crypt4). Шифрованный линк
кэшируется в самой строке (Users / UserDevice / FamilyMembers) на TTL,
заданный в ``RemnaWaveSettings.happ_crypto_cache_ttl_hours``, чтобы не
дёргать публичный API на каждый запрос подписки.

Cache-ключ — сама строка БД: у каждой сущности, владеющей подпиской в
RemnaWave, свои колонки ``happ_cryptolink_v5`` + ``happ_cryptolink_v5_at``.
Между разными строками шифрованные ссылки не шарим — raw subscriptionUrl у
них тоже разный.

Инвалидация: код, переназначающий ``remnawave_uuid``, должен сам обнулить
``happ_cryptolink_v5`` и ``happ_cryptolink_v5_at`` в той же транзакции; TTL
служит запасным slot'ом, если какая-то ветка инвалидацию не сделала.

Save() выполняется через ``update_fields=[...]``: у ``Users`` обычный save
триггерит RemnaWave sync и пересчёт расписаний, нам это не нужно.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from bloobcat.logger import get_logger
from bloobcat.settings import remnawave_settings

logger = get_logger("happ_cryptolink_cache")


def _cache_ttl() -> timedelta:
    hours = getattr(remnawave_settings, "happ_crypto_cache_ttl_hours", 24) or 24
    return timedelta(hours=hours)


async def get_or_refresh_cryptolink(
    record,
    raw_url: str,
    encrypt_fn: Callable[[str], Awaitable[str]],
) -> str:
    """Вернуть закэшированный crypt5-линк ``record`` или обновить через ``encrypt_fn``.

    ``record`` — любой экземпляр модели с полями ``happ_cryptolink_v5`` и
    ``happ_cryptolink_v5_at`` и асинхронным ``save(update_fields=...)``
    (Users, UserDevice, FamilyMembers).
    """
    now = datetime.now(timezone.utc)
    cached = getattr(record, "happ_cryptolink_v5", None)
    cached_at = getattr(record, "happ_cryptolink_v5_at", None)
    ttl = _cache_ttl()
    if cached and cached_at:
        # TIMESTAMPTZ в проде tz-aware, в SQLite-тестах может быть tz-naive —
        # трактуем naive как UTC.
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        if now - cached_at < ttl:
            return cached

    encrypted = await encrypt_fn(raw_url)
    record.happ_cryptolink_v5 = encrypted
    record.happ_cryptolink_v5_at = now
    try:
        await record.save(
            update_fields=["happ_cryptolink_v5", "happ_cryptolink_v5_at"]
        )
    except Exception as exc:
        # Сохранение кэша — best-effort: serialization-ошибка не должна
        # лишать пользователя ссылки на подписку.
        logger.warning(
            f"Failed to persist happ_cryptolink_v5 cache on {type(record).__name__}: {exc}"
        )
    return encrypted
