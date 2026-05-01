from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_backend_dockerfile_runs_application_as_non_root_user():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "adduser" in dockerfile.lower() or "useradd" in dockerfile.lower()
    assert "USER bloobcat" in dockerfile
    assert dockerfile.rstrip().endswith('ENTRYPOINT [ "python", "-m", "bloobcat" ]')


def test_compose_drops_runtime_privileges_for_backend_services():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "cap_drop:" in compose
    assert "- ALL" in compose
    assert "security_opt:" in compose
    assert "no-new-privileges:true" in compose


def test_backend_ci_runs_dependency_and_directus_extension_security_checks():
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "pip-audit" in workflow
    assert "Directus extensions" in workflow
    assert "npm audit" in workflow
