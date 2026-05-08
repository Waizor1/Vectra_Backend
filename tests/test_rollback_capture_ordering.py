"""Guard: the bloobcat rollback-target capture in `auto-deploy.yml` must run
BEFORE the always-on `cleanup_stale_fixed_name_container "bloobcat"` call.

Why this exists
---------------
`docker-compose.yml` declares ``container_name: bloobcat`` (a fixed name).
``cleanup_stale_fixed_name_container "bloobcat" "true"`` does
``docker rm -f bloobcat`` for any container with the matching compose-project
label — i.e. the live production bloobcat container.

The rollback path captures the previous image SHA via:

    ROLLBACK_CONTAINER_ID="$(docker compose ... ps -q bloobcat ...)"
    ROLLBACK_IMAGE_SHA="$(docker inspect --format '{{.Image}}' ...)"

If capture runs AFTER the cleanup call, the container has already been removed
and ``docker compose ps -q bloobcat`` returns nothing → ``ROLLBACK_IMAGE_SHA``
stays empty → the rollback function exits early with "Rollback target
unavailable", and the auto-rollback safety net never fires.

This was the original layout in `854658a chore(deploy): harden CI/deploy
pipeline after 2026-05-08 outage (P0+P1)` — caught and fixed before the PR
merged. This test ratchets the correct ordering so a future refactor can't
silently re-introduce the bug.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTO_DEPLOY_YAML = ROOT / ".github" / "workflows" / "auto-deploy.yml"


def _line_of(needle: str, *, occurrence: int = 1) -> int:
    """Return the 1-based line number of the *occurrence*-th match of *needle*.

    Raises AssertionError if there are fewer matches than requested.
    """
    text = AUTO_DEPLOY_YAML.read_text(encoding="utf-8").splitlines()
    seen = 0
    for idx, line in enumerate(text, start=1):
        if needle in line:
            seen += 1
            if seen == occurrence:
                return idx
    raise AssertionError(
        f"Expected at least {occurrence} occurrence(s) of {needle!r} in "
        f"{AUTO_DEPLOY_YAML}; only saw {seen}."
    )


def test_rollback_capture_runs_before_always_on_cleanup() -> None:
    """ROLLBACK_IMAGE_SHA capture must precede the unconditional cleanup of
    the ``bloobcat`` container so that capture sees a running container.
    """
    capture_line = _line_of('ROLLBACK_IMAGE_SHA=""')

    # Two occurrences of `cleanup_stale_fixed_name_container "bloobcat" "true"`:
    #   1. inside the FULL_REINSTALL_ACTIVE block (destructive prerelease only)
    #   2. unconditional, always-runs (this is the one that races with capture
    #      in normal deploys)
    always_on_cleanup_line = _line_of(
        'cleanup_stale_fixed_name_container "bloobcat" "true"', occurrence=2
    )

    assert capture_line < always_on_cleanup_line, (
        "ROLLBACK_IMAGE_SHA capture is at line "
        f"{capture_line} but the always-on `docker rm -f bloobcat` "
        f"runs at line {always_on_cleanup_line}. The container is removed "
        "before the SHA can be captured, which silently disables auto-rollback."
        "\n\nFix: move the `# Snapshot the bloobcat image *currently* serving "
        "traffic ...` block (and the `ROLLBACK_*` assignments below it) to "
        "BEFORE the `cleanup_stale_fixed_name_container \"bloobcat_db\" "
        "\"true\"` line in the always-on cleanup section. Do not touch the "
        "FULL_REINSTALL_ACTIVE branch — destructive mode wipes volumes and "
        "rollback to the old image is intentionally unavailable there."
    )


def test_rollback_function_uses_captured_vars() -> None:
    """Sanity-check that the rollback function references the captured vars
    so that the ordering test above is meaningful.
    """
    body = AUTO_DEPLOY_YAML.read_text(encoding="utf-8")
    fn_marker = "rollback_bloobcat_to_previous_image() {"
    assert fn_marker in body, (
        "rollback function definition not found — this guard test is stale, "
        "update it to track the new function name."
    )
    fn_start = body.index(fn_marker)
    fn_end = body.index("\n        }\n", fn_start)
    fn_body = body[fn_start:fn_end]

    assert "ROLLBACK_IMAGE_SHA" in fn_body, (
        "rollback function no longer references ROLLBACK_IMAGE_SHA. If the "
        "capture variable was renamed, update both this test and the ordering "
        "test above to match."
    )
    assert "ROLLBACK_IMAGE_REF" in fn_body, (
        "rollback function no longer references ROLLBACK_IMAGE_REF."
    )
    assert "docker tag" in fn_body, (
        "rollback function no longer issues `docker tag` to retag the "
        "previous image — the rollback path is broken differently."
    )
