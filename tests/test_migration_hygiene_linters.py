"""Smoke tests for the CI linters that guard the migrations directory.

These verify the linters detect obvious mistakes (duplicate prefix, raw
CREATE INDEX in a post-grandfathered migration) and that the current
repository state passes both linters.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"


def _run(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_check_migration_prefixes_passes_on_current_repo():
    result = _run("check_migration_prefixes.py", REPO)
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    assert "OK:" in result.stdout


def test_check_migration_prefixes_fails_on_new_collision(tmp_path: Path, monkeypatch):
    """Adding a 6th file with prefix 100_* (currently capped at 5) must fail."""
    fake_root = tmp_path / "migrations" / "models"
    fake_root.mkdir(parents=True)
    # Mirror grandfathered prefixes
    for i, name in enumerate([
        "100_a.py", "100_b.py", "100_c.py", "100_d.py", "100_e.py",
        "100_f.py",  # 6th → over limit
        "108_ok.py",
    ]):
        (fake_root / name).write_text("# stub\n", encoding="utf-8")

    # Run the linter against the fake root by tweaking ROOT via env injection.
    # The linter computes ROOT at import time, so instead invoke it as a
    # subprocess with a temporary working directory + a vendored copy.
    # The linter uses `parents[1]` to find migrations/, so vendor it one
    # level deep so ROOT == tmp_path.
    vendor_dir = tmp_path / "scripts"
    vendor_dir.mkdir()
    vendored = vendor_dir / "check_migration_prefixes.py"
    vendored.write_text(
        (SCRIPTS / "check_migration_prefixes.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(vendored)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "collisions beyond grandfathered" in result.stderr
    assert "'100': 6" in result.stderr


def test_check_migration_safety_passes_on_current_repo():
    result = _run("check_migration_safety.py", REPO)
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    assert "OK:" in result.stdout


def test_check_migration_safety_fails_on_raw_create_index(tmp_path: Path):
    """A new migration (prefix > 107) creating a blocking index must fail."""
    fake_root = tmp_path / "migrations" / "models"
    fake_root.mkdir(parents=True)
    (fake_root / "120_demo_offender.py").write_text(
        'sql = "CREATE INDEX idx_demo ON users (id);"\n',
        encoding="utf-8",
    )
    (fake_root / "121_demo_ok.py").write_text(
        'sql = "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ok ON users (email);"\n',
        encoding="utf-8",
    )

    vendor_dir = tmp_path / "scripts"
    vendor_dir.mkdir(exist_ok=True)
    vendored = vendor_dir / "check_migration_safety.py"
    vendored.write_text(
        (SCRIPTS / "check_migration_safety.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(vendored)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "120_demo_offender.py" in result.stderr
    # The CONCURRENTLY example must not trigger
    assert "121_demo_ok.py" not in result.stderr


def test_check_migration_safety_grandfathers_old_blocking_indexes(tmp_path: Path):
    """Migrations with prefix <= 107 are grandfathered and must not fail the linter."""
    fake_root = tmp_path / "migrations" / "models"
    fake_root.mkdir(parents=True)
    (fake_root / "099_old_blocking.py").write_text(
        'sql = "CREATE INDEX idx_old ON users (id);"\n',
        encoding="utf-8",
    )

    vendor_dir = tmp_path / "scripts"
    vendor_dir.mkdir(exist_ok=True)
    vendored = vendor_dir / "check_migration_safety.py"
    vendored.write_text(
        (SCRIPTS / "check_migration_safety.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(vendored)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "OK:" in result.stdout
