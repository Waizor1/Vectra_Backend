"""Сидинг базовых сегментных акций для раздела «Подписка».

Что делает:
- Подключается к рабочей БД через `bloobcat.clients.TORTOISE_ORM`.
- Создаёт 5 живых кампаний-демо (или обновляет существующие по `slug`).
- Каждая кампания привязана к своему сегменту аудитории, чтобы оператор
  сразу увидел, как переключается баннер для разных пользователей.

Как использовать (из корня Vectra_Backend):
    .venv/bin/python -m scripts.seed_segment_campaigns           # default 7 days
    .venv/bin/python -m scripts.seed_segment_campaigns --days 14 # своё окно
    .venv/bin/python -m scripts.seed_segment_campaigns --dry-run # preview
    .venv/bin/python -m scripts.seed_segment_campaigns --reset   # удалить и пересоздать

Всё работает идемпотентно: повторный запуск только обновляет окно/копирайт,
не создаёт дубликаты.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tortoise import Tortoise  # noqa: E402

from bloobcat.clients import TORTOISE_ORM  # noqa: E402
from bloobcat.logger import get_logger  # noqa: E402

logger = get_logger("scripts.seed_segment_campaigns")


def _build_default_campaigns(window_days: int) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    starts_at = now - timedelta(minutes=5)
    ends_at = now + timedelta(days=max(1, int(window_days)))

    short_window_ends = now + timedelta(days=max(1, min(3, int(window_days))))
    long_window_ends = now + timedelta(days=max(int(window_days), 14))

    return [
        {
            "slug": "welcome-first-buy-25",
            "title": "Первая подписка −25%",
            "subtitle": "Для тех, кто оформляет тариф впервые",
            "description": (
                "Скидка автоматически применится к 3, 6 и 12 месяцам. "
                "После активации цена пересчитается в калькуляторе."
            ),
            "segment": "no_purchase_yet",
            "discount_percent": 25,
            "applies_to_months": [3, 6, 12],
            "accent": "cyan",
            "cta_label": "Активировать скидку",
            "cta_target": "tariff_12m",
            "starts_at": starts_at,
            "ends_at": ends_at,
            "priority": 80,
            "is_active": True,
        },
        {
            "slug": "trial-finish-20",
            "title": "Закрепи тариф со скидкой −20%",
            "subtitle": "Пробный заканчивается — продли с выгодой",
            "description": (
                "Пока активен пробный период, можно оформить полную "
                "подписку с фиксированной скидкой."
            ),
            "segment": "trial_active",
            "discount_percent": 20,
            "applies_to_months": [3, 6, 12],
            "accent": "violet",
            "cta_label": "Перейти к тарифам",
            "cta_target": "builder",
            "starts_at": starts_at,
            "ends_at": ends_at,
            "priority": 70,
            "is_active": True,
        },
        {
            "slug": "lapsed-comeback-30",
            "title": "Возвращайся: −30% на 6 и 12 месяцев",
            "subtitle": "Вернуть подписку быстрее и дешевле",
            "description": (
                "Скидка действует только для тех, чья подписка истекла "
                "более недели назад."
            ),
            "segment": "lapsed",
            "discount_percent": 30,
            "applies_to_months": [6, 12],
            "accent": "gold",
            "cta_label": "Вернуть подписку",
            "cta_target": "tariff_12m",
            "starts_at": starts_at,
            "ends_at": long_window_ends,
            "priority": 90,
            "is_active": True,
        },
        {
            "slug": "loyalty-yearly-15",
            "title": "Постоянным клиентам −15% на 12 месяцев",
            "subtitle": "Спасибо, что остаётесь с нами",
            "description": (
                "Скидка добавляется к стандартной экономии годового тарифа."
            ),
            "segment": "loyal_renewer",
            "discount_percent": 15,
            "applies_to_months": [12],
            "accent": "blue",
            "cta_label": "Продлить со скидкой",
            "cta_target": "tariff_12m",
            "starts_at": starts_at,
            "ends_at": long_window_ends,
            "priority": 60,
            "is_active": True,
        },
        {
            "slug": "spring-everyone-10",
            "title": "Весеннее предложение −10%",
            "subtitle": "Для всех — на любой тариф",
            "description": (
                "Базовая страховочная акция: показывается, если к "
                "пользователю не подходит ни одна именная кампания."
            ),
            "segment": "everyone",
            "discount_percent": 10,
            "applies_to_months": [],
            "accent": "green",
            "cta_label": "Воспользоваться",
            "cta_target": "builder",
            "starts_at": starts_at,
            "ends_at": short_window_ends,
            "priority": 5,
            "is_active": True,
        },
    ]


async def _seed(window_days: int, dry_run: bool, reset: bool) -> None:
    from bloobcat.db.segment_campaigns import SegmentCampaign

    payloads = _build_default_campaigns(window_days)

    if reset and not dry_run:
        slugs = [p["slug"] for p in payloads]
        deleted = await SegmentCampaign.filter(slug__in=slugs).delete()
        logger.info("Reset: removed {} pre-existing seed campaigns", deleted)

    for payload in payloads:
        slug = payload["slug"]
        existing = await SegmentCampaign.get_or_none(slug=slug)
        verb = "would update" if (dry_run and existing) else (
            "would create" if dry_run else ("updated" if existing else "created")
        )
        if dry_run:
            logger.info(
                "[dry-run] {} '{}' — segment={}, -{}%, ends_at={}",
                verb,
                slug,
                payload["segment"],
                payload["discount_percent"],
                payload["ends_at"].isoformat(),
            )
            continue

        if existing:
            for field, value in payload.items():
                setattr(existing, field, value)
            await existing.save()
        else:
            await SegmentCampaign.create(**payload)

        logger.info(
            "{} '{}' — segment={}, -{}%, ends_at={}",
            verb,
            slug,
            payload["segment"],
            payload["discount_percent"],
            payload["ends_at"].isoformat(),
        )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed default segment campaigns")
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Базовая длительность окна акции в днях (по умолчанию 7).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, что будет сделано, без записи в БД.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Перед сидингом удалить существующие записи с теми же slug. "
            "Полезно для локальной отладки, опасно на проде."
        ),
    )
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> None:
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        await _seed(args.days, args.dry_run, args.reset)
    finally:
        await Tortoise.close_connections()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
