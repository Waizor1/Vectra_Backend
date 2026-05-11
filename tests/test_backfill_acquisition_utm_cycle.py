"""Cycle guards in the backfill_acquisition_utm script.

The runtime _apply_* paths block A→A and the schema does too, but the
backfill script never validated either case before. With a self-loop
the script tries to copy the user's own utm onto themselves; with a
two-node cycle (A↔B) the utm tag pings back and forth across passes.

These tests exercise the in-script guards directly by stubbing the
Users/get_or_none/save calls and walking the inner loop manually,
without touching a real database.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Iterable, List

import pytest


@pytest.mark.asyncio
async def test_backfill_skips_self_referral(monkeypatch):
    mod = importlib.import_module("scripts.backfill_acquisition_utm")

    # User points back at themselves — must be skipped without raising.
    invitee = SimpleNamespace(id=1, referred_by=1, utm=None, save=_AsyncRaiser("save"))

    class FakeUsers:
        @classmethod
        def filter(cls, *args, **kwargs):
            class QS:
                def offset(self_inner, _o):
                    return self_inner

                def limit(self_inner, _l):
                    return self_inner

                async def all(self_inner):
                    nonlocal_state["served"] = nonlocal_state["served"] + 1
                    return [invitee] if nonlocal_state["served"] == 1 else []

            return QS()

        @classmethod
        async def get_or_none(cls, id):
            raise AssertionError(
                "self-loop must be guarded BEFORE referrer lookup"
            )

    nonlocal_state = {"served": 0}
    monkeypatch.setattr(mod, "Users", FakeUsers)
    warnings = _capture_warnings(monkeypatch, mod)

    total = await mod._run(apply=True, max_passes=2, batch_size=10)
    assert total == 0
    assert any("self-referral" in msg for msg in warnings)


@pytest.mark.asyncio
async def test_backfill_skips_two_node_cycle(monkeypatch):
    mod = importlib.import_module("scripts.backfill_acquisition_utm")

    # A=1 referred by B=2; B=2 referred by A=1; both have empty utm.
    invitee = SimpleNamespace(id=1, referred_by=2, utm=None, save=_AsyncRaiser("save"))
    referrer = SimpleNamespace(id=2, referred_by=1, utm="qr_rt_launch")

    class FakeUsers:
        @classmethod
        def filter(cls, *args, **kwargs):
            class QS:
                def offset(self_inner, _o):
                    return self_inner

                def limit(self_inner, _l):
                    return self_inner

                async def all(self_inner):
                    served["count"] += 1
                    return [invitee] if served["count"] == 1 else []

            return QS()

        @classmethod
        async def get_or_none(cls, id):
            if id == 2:
                return referrer
            return None

    served = {"count": 0}
    monkeypatch.setattr(mod, "Users", FakeUsers)
    warnings = _capture_warnings(monkeypatch, mod)

    total = await mod._run(apply=True, max_passes=2, batch_size=10)
    assert total == 0, "two-node cycle must not copy utm"
    assert any("two-node cycle" in msg for msg in warnings)


def _capture_warnings(monkeypatch, mod):
    """Loguru bypasses pytest caplog; replace the module-level logger with
    a stub that captures formatted warning messages."""
    captured: List[str] = []

    def _warn(template: str, *args):
        try:
            captured.append(template % args)
        except TypeError:
            captured.append(template)

    monkeypatch.setattr(mod.logger, "warning", _warn)
    return captured


@pytest.mark.asyncio
async def test_backfill_applies_normal_inheritance(monkeypatch):
    mod = importlib.import_module("scripts.backfill_acquisition_utm")

    saved = {}

    async def save(update_fields):
        saved["utm"] = invitee.utm
        saved["fields"] = update_fields

    invitee = SimpleNamespace(id=1, referred_by=2, utm=None, save=save)
    referrer = SimpleNamespace(id=2, referred_by=None, utm="qr_rt_launch_2026_05")

    class FakeUsers:
        @classmethod
        def filter(cls, *args, **kwargs):
            class QS:
                def offset(self_inner, _o):
                    return self_inner

                def limit(self_inner, _l):
                    return self_inner

                async def all(self_inner):
                    served["count"] += 1
                    return [invitee] if served["count"] == 1 else []

            return QS()

        @classmethod
        async def get_or_none(cls, id):
            return referrer if id == 2 else None

    served = {"count": 0}
    monkeypatch.setattr(mod, "Users", FakeUsers)

    total = await mod._run(apply=True, max_passes=2, batch_size=10)
    assert total == 1
    assert saved["utm"] == "qr_rt_launch_2026_05"
    assert saved["fields"] == ["utm"]


class _AsyncRaiser:
    """Stub: any call must signal that the cycle guard failed to fire."""

    def __init__(self, label):
        self.label = label

    async def __call__(self, *args, **kwargs):
        raise AssertionError(f"{self.label} must not be reached for cycle cases")
