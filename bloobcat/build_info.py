from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Optional, TypedDict


class BuildInfo(TypedDict):
    version: str
    build_time: str


def _iso_utc_now() -> str:
    # ISO-8601 in UTC without microseconds, example: 2026-02-10T12:34:56Z
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _read_poetry_version_from_pyproject(pyproject_path: Path) -> Optional[str]:
    try:
        text = pyproject_path.read_text(encoding="utf-8")
    except OSError:
        return None

    # Try to only inspect the [tool.poetry] section to avoid false matches.
    section_match = re.search(r"(?ms)^\[tool\.poetry\]\s*(.*?)(^\[|\Z)", text)
    if not section_match:
        return None

    section = section_match.group(1)
    version_match = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"\s*$', section)
    if not version_match:
        return None

    return version_match.group(1).strip()


def _detect_version() -> str:
    env_version = (os.getenv("APP_VERSION") or "").strip()
    if env_version:
        return env_version

    try:
        return metadata.version("bloobcat")
    except Exception:
        pass

    repo_root = Path(__file__).resolve().parent.parent
    pyproject_version = _read_poetry_version_from_pyproject(repo_root / "pyproject.toml")
    if pyproject_version:
        return pyproject_version

    return "0.0.0"


def _detect_build_time() -> str:
    for key in ("BUILD_TIME", "APP_BUILD_TIME", "BLOOBCAT_BUILD_TIME"):
        val = (os.getenv(key) or "").strip()
        if val:
            return val

    # Fallback for local dev: stamp at process start/import time.
    return _iso_utc_now()


_CACHED_BUILD_INFO: BuildInfo = {
    "version": _detect_version(),
    "build_time": _detect_build_time(),
}


def get_build_info() -> BuildInfo:
    return _CACHED_BUILD_INFO.copy()

