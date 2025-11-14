from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Iterable, Optional

from .schemas import ActiveSubscription, CaptainUserProfile


class InMemoryUserRepository:
    """Простейшее in-memory хранилище пользователей."""

    def __init__(self, seed: Iterable[CaptainUserProfile]):
        self._storage: Dict[int, CaptainUserProfile] = {
            profile.telegram_id: profile for profile in seed
        }

    async def get_by_telegram_id(self, telegram_id: int) -> Optional[CaptainUserProfile]:
        return self._storage.get(telegram_id)


def _build_default_dataset() -> list[CaptainUserProfile]:
    now = datetime.utcnow()
    return [
        CaptainUserProfile(
            telegram_id=101010101,
            first_name="Captain",
            last_name="Demo",
            username="captain_demo",
            email="captain.demo@example.com",
            phone="+1234567890",
            country="US",
            status="active",
            active_subscriptions=[
                ActiveSubscription(
                    name="BloobCat Premium",
                    status="active",
                    started_at=now - timedelta(days=90),
                    expires_at=now + timedelta(days=30),
                )
            ],
            balance=42.50,
            registered_at=now - timedelta(days=365),
            last_login=now - timedelta(hours=5),
        ),
        CaptainUserProfile(
            telegram_id=303030303,
            first_name="Jane",
            last_name="Doe",
            username="jane_guard",
            email="jane@example.com",
            phone="+987654321",
            country="DE",
            status="trial",
            active_subscriptions=[
                ActiveSubscription(
                    name="Starter Pack",
                    status="trial",
                    started_at=now - timedelta(days=5),
                    expires_at=now + timedelta(days=5),
                )
            ],
            balance=5.0,
            registered_at=now - timedelta(days=180),
            last_login=now - timedelta(days=1, hours=2),
        ),
    ]


user_repository = InMemoryUserRepository(_build_default_dataset())
