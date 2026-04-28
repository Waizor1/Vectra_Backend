from __future__ import annotations

from collections.abc import Sequence
import ipaddress
import socket

from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.resolver import DefaultResolver
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TEST

TELEGRAM_API_HOST = "api.telegram.org"


def build_fallback_resolve_results(
    host: str,
    port: int,
    fallback_ips: Sequence[str],
    family: socket.AddressFamily = socket.AF_UNSPEC,
) -> list[ResolveResult]:
    results: list[ResolveResult] = []
    seen: set[tuple[socket.AddressFamily, str, int]] = set()

    for candidate in fallback_ips:
        normalized_ip = str(ipaddress.ip_address(candidate))
        resolved_family = (
            socket.AF_INET6 if ":" in normalized_ip else socket.AF_INET
        )
        if family not in (socket.AF_UNSPEC, resolved_family):
            continue

        key = (resolved_family, normalized_ip, port)
        if key in seen:
            continue
        seen.add(key)

        results.append(
            ResolveResult(
                hostname=host,
                host=normalized_ip,
                port=port,
                family=resolved_family,
                proto=0,
                flags=socket.AI_NUMERICHOST,
            )
        )

    return results


def merge_resolve_results(
    preferred: Sequence[ResolveResult],
    fallback: Sequence[ResolveResult],
) -> list[ResolveResult]:
    merged: list[ResolveResult] = []
    seen: set[tuple[socket.AddressFamily, str, int]] = set()

    for record in (*preferred, *fallback):
        key = (record["family"], record["host"], record["port"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(record)

    return merged


class TelegramFallbackResolver(AbstractResolver):
    def __init__(
        self,
        fallback_ips: Sequence[str],
        *,
        target_host: str = TELEGRAM_API_HOST,
    ) -> None:
        self._fallback_ips = tuple(fallback_ips)
        self._target_host = target_host
        self._default_resolver: DefaultResolver | None = None

    def _get_default_resolver(self) -> DefaultResolver:
        if self._default_resolver is None:
            self._default_resolver = DefaultResolver()
        return self._default_resolver

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[ResolveResult]:
        if host != self._target_host or not self._fallback_ips:
            return await self._get_default_resolver().resolve(host, port, family)

        preferred = build_fallback_resolve_results(
            host,
            port,
            self._fallback_ips,
            family,
        )
        try:
            default_results = await self._get_default_resolver().resolve(
                host,
                port,
                family,
            )
        except OSError:
            default_results = []

        return merge_resolve_results(preferred, default_results)

    async def close(self) -> None:
        if self._default_resolver is not None:
            await self._default_resolver.close()


class TelegramFallbackAiohttpSession(AiohttpSession):
    def __init__(self, fallback_ips: Sequence[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self._connector_init["resolver"] = TelegramFallbackResolver(fallback_ips)
        self._should_reset_connector = True


def create_bot_session(
    *,
    is_dev: bool,
    fallback_ips: Sequence[str],
) -> AiohttpSession | None:
    if fallback_ips:
        session: AiohttpSession = TelegramFallbackAiohttpSession(fallback_ips)
    elif is_dev:
        session = AiohttpSession()
    else:
        return None

    if is_dev:
        session.api = TEST

    return session
