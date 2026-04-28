import asyncio
from typing import cast

import aiohttp
import pytest

from bloobcat.routes.remnawave import client as remnawave_client


@pytest.mark.asyncio
async def test_execute_with_retry_a025_not_found_is_not_retried(monkeypatch):
    api = remnawave_client.UsersAPI(client=cast(remnawave_client.RemnaWaveClient, object()))
    calls = {"attempts": 0, "sleep": 0}

    async def _fake_sleep(_seconds):
        calls["sleep"] += 1

    async def _always_a025():
        calls["attempts"] += 1
        raise Exception("API error [A025]: User not found")

    monkeypatch.setattr(remnawave_client.asyncio, "sleep", _fake_sleep)

    with pytest.raises(Exception, match="A025"):
        await api._execute_with_retry(_always_a025)

    assert calls["attempts"] == 1
    assert calls["sleep"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_message",
    [
        "api error [a025]: user not found",
        "API error [A-063]: User with specified params not found",
    ],
)
async def test_execute_with_retry_not_found_variants_are_not_retried(monkeypatch, error_message):
    api = remnawave_client.UsersAPI(client=cast(remnawave_client.RemnaWaveClient, object()))
    calls = {"attempts": 0, "sleep": 0}

    async def _fake_sleep(_seconds):
        calls["sleep"] += 1

    async def _always_not_found_variant():
        calls["attempts"] += 1
        raise Exception(error_message)

    monkeypatch.setattr(remnawave_client.asyncio, "sleep", _fake_sleep)

    with pytest.raises(Exception):
        await api._execute_with_retry(_always_not_found_variant)

    assert calls["attempts"] == 1
    assert calls["sleep"] == 0


@pytest.mark.asyncio
async def test_execute_with_retry_keeps_transient_retry_behavior(monkeypatch):
    api = remnawave_client.UsersAPI(client=cast(remnawave_client.RemnaWaveClient, object()))
    calls = {"attempts": 0, "sleep_delays": []}

    async def _fake_sleep(seconds):
        calls["sleep_delays"].append(seconds)

    async def _flaky_then_success():
        calls["attempts"] += 1
        if calls["attempts"] < 3:
            raise Exception("Network error: timeout")
        return {"ok": True}

    monkeypatch.setattr(remnawave_client.asyncio, "sleep", _fake_sleep)

    result = await api._execute_with_retry(_flaky_then_success)

    assert result == {"ok": True}
    assert calls["attempts"] == 3
    assert calls["sleep_delays"] == [3, 3]


@pytest.mark.asyncio
async def test_execute_with_retry_validation_error_is_not_retried(monkeypatch):
    api = remnawave_client.UsersAPI(client=cast(remnawave_client.RemnaWaveClient, object()))
    calls = {"attempts": 0, "sleep": 0}

    async def _fake_sleep(_seconds):
        calls["sleep"] += 1

    async def _always_validation_error():
        calls["attempts"] += 1
        raise Exception("Validation error: invalid payload")

    monkeypatch.setattr(remnawave_client.asyncio, "sleep", _fake_sleep)

    with pytest.raises(Exception, match="Validation error"):
        await api._execute_with_retry(_always_validation_error)

    assert calls["attempts"] == 1
    assert calls["sleep"] == 0


@pytest.mark.asyncio
async def test_request_timeout_is_explicit_and_classified(monkeypatch):
    client = remnawave_client.RemnaWaveClient("https://example.com", "token")
    captured = {}

    class _DummySession:
        def request(self, method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["timeout"] = kwargs.get("timeout")
            raise asyncio.TimeoutError("request timed out")

    client.session = _DummySession()

    with pytest.raises(Exception, match="Timeout error"):
        await client._request("GET", "/api/ping")

    assert captured["url"] == "https://example.com/api/ping"
    assert isinstance(captured["timeout"], aiohttp.ClientTimeout)


@pytest.mark.asyncio
async def test_execute_with_retry_respects_total_budget_without_oversleep(monkeypatch):
    api = remnawave_client.UsersAPI(client=cast(remnawave_client.RemnaWaveClient, object()))
    calls = {"attempts": 0, "sleep_delays": []}
    fake_clock = {"now": 0.0}

    def _fake_monotonic():
        return fake_clock["now"]

    async def _fake_sleep(seconds):
        calls["sleep_delays"].append(seconds)
        fake_clock["now"] += seconds

    async def _almost_budget_exhausted_failure():
        calls["attempts"] += 1
        fake_clock["now"] += 59.5
        raise Exception("Network error: transient")

    monkeypatch.setattr(remnawave_client.time_module, "monotonic", _fake_monotonic)
    monkeypatch.setattr(remnawave_client.asyncio, "sleep", _fake_sleep)

    with pytest.raises(Exception, match="transient"):
        await api._execute_with_retry(_almost_budget_exhausted_failure)

    assert calls["attempts"] == 1
    assert calls["sleep_delays"] == [0.5]


@pytest.mark.asyncio
async def test_execute_with_retry_caps_request_timeout_by_remaining_budget(monkeypatch):
    fake_clock = {"now": 59.0}
    observed = {"timeouts": [], "sleep_delays": []}

    class _DummyClient:
        def __init__(self):
            self.request_timeout = aiohttp.ClientTimeout(total=15)

        async def _request(self, *_args, **kwargs):
            timeout = kwargs["timeout"]
            observed["timeouts"].append(timeout.total)
            fake_clock["now"] += timeout.total
            raise Exception("Timeout error: simulated")

    def _fake_monotonic():
        return fake_clock["now"]

    async def _fake_sleep(seconds):
        observed["sleep_delays"].append(seconds)
        fake_clock["now"] += seconds

    client = _DummyClient()
    api = remnawave_client.UsersAPI(client=cast(remnawave_client.RemnaWaveClient, client))

    monkeypatch.setattr(remnawave_client.time_module, "monotonic", _fake_monotonic)
    monkeypatch.setattr(remnawave_client.asyncio, "sleep", _fake_sleep)

    with pytest.raises(Exception, match="Timeout error"):
        await api._execute_with_retry(client._request, "GET", "/api/ping")

    assert observed["timeouts"] == [15.0, 15.0, 15.0, 6.0]
    assert observed["sleep_delays"] == [3.0, 3.0, 3.0]
    assert sum(observed["timeouts"]) + sum(observed["sleep_delays"]) == 60.0


@pytest.mark.asyncio
async def test_execute_with_retry_caps_timeout_for_wrapper_callable(monkeypatch):
    fake_clock = {"now": 59.0}
    observed = {"timeouts": [], "sleep_delays": []}

    class _DummyClient:
        def __init__(self):
            self.request_timeout = aiohttp.ClientTimeout(total=15)

    def _fake_monotonic():
        return fake_clock["now"]

    async def _fake_sleep(seconds):
        observed["sleep_delays"].append(seconds)
        fake_clock["now"] += seconds

    client = _DummyClient()
    api = remnawave_client.UsersAPI(client=cast(remnawave_client.RemnaWaveClient, client))

    async def _wrapper_with_timeout(timeout: aiohttp.ClientTimeout | None = None):
        if timeout is None:
            raise AssertionError("timeout must be injected for request-capable wrappers")
        observed["timeouts"].append(timeout.total)
        fake_clock["now"] += float(timeout.total or 0)
        raise Exception("Timeout error: wrapped request simulated")

    monkeypatch.setattr(remnawave_client.time_module, "monotonic", _fake_monotonic)
    monkeypatch.setattr(remnawave_client.asyncio, "sleep", _fake_sleep)

    with pytest.raises(Exception, match="Timeout error"):
        await api._execute_with_retry(_wrapper_with_timeout)

    assert observed["timeouts"] == [15.0, 15.0, 15.0, 6.0]
    assert observed["sleep_delays"] == [3.0, 3.0, 3.0]
