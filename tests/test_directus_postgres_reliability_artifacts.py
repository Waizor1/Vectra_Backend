import re
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _line(relative_path: str, prefix: str) -> str:
    for line in _read(relative_path).splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"Line starting with '{prefix}' not found in {relative_path}")


def _extract_remote_script_block(workflow_content: str) -> str:
    start_marker = "REMOTE_SCRIPT_CONTENT=\"$(cat <<'REMOTE_SCRIPT'"
    end_marker = '\n        REMOTE_SCRIPT\n        )"'

    start = workflow_content.index(start_marker) + len(start_marker)
    end = workflow_content.index(end_marker, start)
    return workflow_content[start:end]


def _normalize_multiline_shell_commands(script_content: str) -> str:
    """Collapse backslash-newline continuations for stable command scanning."""
    return re.sub(r"\\\r?\n[ \t]*", " ", script_content)


def _extract_fk_fix_block(workflow_content: str) -> str:
    remote_script = _extract_remote_script_block(workflow_content)
    start_marker = (
        'echo "Fixing FK active_tariffs.user_id -> users.id (ON DELETE CASCADE)..."'
    )
    end_marker = "\n        SUPER_SETUP_OK=0"

    start = remote_script.index(start_marker)
    end = remote_script.index(end_marker, start)
    return remote_script[start:end]


def _extract_post_super_setup_runtime_gate_block(workflow_content: str) -> str:
    remote_script = _extract_remote_script_block(workflow_content)
    start_marker = 'echo "Running post-super-setup runtime-state verification gate..."'
    end_marker = "\n        TARIFFS_SEED_OK=0"

    start = remote_script.index(start_marker)
    end = remote_script.index(end_marker, start)
    return remote_script[start:end]


def test_compose_uses_required_postgres_password_authority_everywhere():
    content = _read("docker-compose.yml")
    required_marker = "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

    assert content.count(required_marker) == 3
    assert (
        "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
        in content
    )
    assert (
        "SCRIPT_DB=postgres://postgres:${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}@bloobcat_db:5432/postgres"
        in content
    )
    assert "DB_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}" in content


def test_env_example_declares_plain_postgres_password_and_non_interpolated_script_db():
    content = _read(".env.example")
    script_db_line = _line(".env.example", "SCRIPT_DB=")

    assert "POSTGRES_PASSWORD=change-me" in content
    assert (
        script_db_line
        == "SCRIPT_DB=postgres://postgres:change-me@bloobcat_db:5432/postgres"
    )
    assert "${POSTGRES_PASSWORD}" not in script_db_line


def test_reconcile_script_has_required_guard_and_non_destructive_auth_probe():
    content = _read("scripts/db/reconcile_postgres_auth.sh")

    assert "set -eu" in content
    assert "require_non_empty_env POSTGRES_PASSWORD" in content
    assert "DB_USER must match ^[A-Za-z_][A-Za-z0-9_]*$" in content
    assert "grep -Eq '^[A-Za-z_][A-Za-z0-9_]*$'" in content
    assert (
        "escaped_postgres_password=$(printf '%s' \"$POSTGRES_PASSWORD\" | sed \"s/'/''/g\")"
        in content
    )
    assert (
        "-c \"ALTER ROLE $DB_USER WITH PASSWORD '$escaped_postgres_password';\""
        in content
    )
    assert "quote_literal(:'NEW_PASSWORD')" not in content
    assert "</dev/null" in content
    assert "-h 127.0.0.1" in content
    assert "TCP password auth probe failed" in content

    assert content.index("require_non_empty_env POSTGRES_PASSWORD") < content.index(
        "command -v docker"
    )


def test_destructive_script_is_fail_closed_and_has_safety_guards():
    content = _read("scripts/db/destructive_reset_once.sh")

    assert "set -eu" in content
    assert "DRY_RUN must be either 'true' or 'false'" in content
    assert "COMPOSE_PROJECT_NAME is required for destructive volume scoping" in content
    assert "ALLOW_DESTRUCTIVE_DB_RESET_ONCE=true" in content
    assert "DESTRUCTIVE_RESET_ACK=$EXPECTED_ACK_TOKEN" in content
    assert "One-time marker already exists" in content
    assert "Could not acquire lock" in content
    assert "No scoped DB volume found" in content
    assert "Multiple scoped DB volumes found" in content


def test_workflow_keeps_reconcile_first_and_evidence_gated_fallback_order():
    content = _read(".github/workflows/auto-deploy.yml")

    full_reinstall_cleanup = content.index(
        'if [ "$FULL_REINSTALL_ACTIVE" -eq 1 ]; then'
    )
    stale_cleanup = content.index("cleanup_stale_fixed_name_container() {")
    db_start = content.index("Starting DB service first with retries")
    first_db_deploy = content.index(
        "docker compose \\$COMPOSE_ARGS up -d --build bloobcat_db"
    )

    assert full_reinstall_cleanup < stale_cleanup < db_start < first_db_deploy

    reconcile = content.index("Running non-destructive DB credential reconcile")
    app_start = content.index("Starting application services with retries")
    assert db_start < reconcile < app_start

    assert "ALLOW_DESTRUCTIVE_DB_RESET_ONCE" in content
    assert "DESTRUCTIVE_RESET_ACK" in content
    assert 'logs --since "\\$DIRECTUS_HEALTH_WAIT_STARTED_AT"' in content
    assert (
        'if [ "\\$DB_AUTH_LOG_MATCH" -ne 1 ] && [ "\\$DB_AUTH_PROBE_MISMATCH" -ne 1 ]; then'
        in content
    )

    after_fallback_ready = content.index(
        "Waiting for DB readiness after destructive fallback"
    )
    after_fallback_reconcile = content.index(
        "Re-running non-destructive DB credential reconcile after fallback"
    )
    after_fallback_health = content.index(
        "Re-running service health checks after fallback"
    )
    assert after_fallback_ready < after_fallback_reconcile < after_fallback_health


def test_workflow_runs_blocking_migration_verify_after_health_and_before_setup_ops():
    content = _read(".github/workflows/auto-deploy.yml")

    bloobcat_health = content.index("Waiting for bloobcat health")
    directus_health = content.index("Waiting for Directus health")
    migration_stage = content.index(
        "Running explicit migrations + runtime-state verification"
    )
    migration_call = content.index("python scripts/apply_migrations.py")
    migration_fail = content.index(
        "Migration + runtime-state verification failed; abort deploy"
    )
    super_setup = content.index("Применяем Directus super-setup")

    assert bloobcat_health < directus_health < migration_stage < super_setup
    assert migration_stage < migration_call < migration_fail < super_setup
    assert "compose_exec bloobcat sh -lc '" in content
    assert "exit 1" in content[migration_fail:super_setup]


def test_workflow_prerelease_full_reinstall_toggle_wiring_and_push_main_gate():
    content = _read(".github/workflows/auto-deploy.yml")

    # Post-2026-05-08: deploy is gated on a successful Backend CI run rather than
    # a raw push. We assert both the workflow_run wiring and the normalisation
    # that maps a workflow_run-on-main back into "push-to-main" semantics so the
    # rest of the script (prerelease gate included) stays untouched.
    assert 'workflows: ["Backend CI"]' in content
    assert "types: [completed]" in content
    assert "branches: [main]" in content
    assert "github.event.workflow_run.conclusion == 'success'" in content
    assert "github.event.workflow_run.head_branch == 'main'" in content
    assert (
        "GITHUB_EVENT_NAME: ${{ github.event_name == 'workflow_run' && 'push' || github.event_name }}"
        in content
    )
    assert (
        "GITHUB_REF_NAME: ${{ github.event_name == 'workflow_run' && github.event.workflow_run.head_branch || github.ref_name }}"
        in content
    )

    assert (
        "cancel-in-progress: ${{ vars.PRERELEASE_FULL_REINSTALL != 'true' }}" in content
    )
    assert (
        "PRERELEASE_FULL_REINSTALL: ${{ vars.PRERELEASE_FULL_REINSTALL || 'false' }}"
        in content
    )
    assert 'PRERELEASE_FULL_REINSTALL="${7:-false}"' in content
    assert 'GITHUB_EVENT_NAME="${8:-}"' in content
    assert (
        'PRERELEASE_FULL_REINSTALL_NORMALIZED="$(normalize_toggle "${PRERELEASE_FULL_REINSTALL:-false}")"'
        in content
    )
    assert 'if [ "${GITHUB_EVENT_NAME:-}" != "push" ]; then' in content
    assert 'elif [ "$TARGET_BRANCH" != "main" ]; then' in content
    assert 'elif [ "$PRERELEASE_FULL_REINSTALL_NORMALIZED" != "true" ]; then' in content
    assert "PRERELEASE FULL REINSTALL MODE: ACTIVE" in content
    assert "PRERELEASE FULL REINSTALL MODE: INACTIVE" in content


def test_workflow_prerelease_full_reinstall_path_wipes_project_scoped_volumes_only():
    content = _read(".github/workflows/auto-deploy.yml")

    assert 'if [ "$FULL_REINSTALL_ACTIVE" -eq 1 ]; then' in content
    assert "docker compose $COMPOSE_ARGS down -v --remove-orphans" in content
    assert "resolve_compose_project_name()" in content
    assert 'raw_name="$(basename "$PROJECT_PATH")"' in content
    assert "tr -c 'a-z0-9_-' '_'" in content
    assert "sed 's/^_*//; s/_*$//'" in content
    assert 'COMPOSE_PROJECT_NAME_RESOLVED="$(resolve_compose_project_name)"' in content
    assert 'export COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME_RESOLVED"' in content
    assert "Using deterministic COMPOSE_PROJECT_NAME" in content
    assert (
        'docker volume ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME_RESOLVED"'
        in content
    )
    assert 'docker volume rm "$volume_name"' in content
    assert "container_exists_exact()" in content
    assert (
        "docker ps -a --filter \"name=^/${container_name}$\" --format '{{.Names}}'"
        in content
    )
    assert "inspect_container_compose_project_label()" in content
    assert (
        'docker inspect --format \'{{ index .Config.Labels "com.docker.compose.project" }}\' "$container_name"'
        in content
    )
    assert "cleanup_stale_fixed_name_container()" in content
    assert (
        "Skipping stale fixed-name container owned by another compose project"
        in content
    )
    assert (
        "Skipping unlabeled fixed-name container due alias safety guard: $container_name"
        in content
    )
    assert 'cleanup_stale_fixed_name_container "bloobcat_db" "true"' in content
    assert 'cleanup_stale_fixed_name_container "bloobcat" "true"' in content
    assert 'cleanup_stale_fixed_name_container "bloobcat_directus" "true"' in content
    assert 'cleanup_stale_fixed_name_container "directus" "false"' in content
    assert "Removing stale fixed-name container" in content
    assert 'docker rm -f "$container_name"' in content


def test_workflow_db_retry_loop_captures_real_nonzero_exit_code():
    content = _read(".github/workflows/auto-deploy.yml")

    assert "set +e" in content
    assert "docker compose \\$COMPOSE_ARGS up -d --build bloobcat_db" in content
    assert "rc=\\$?" in content
    assert "set -e" in content
    assert 'if [ "\\$rc" -eq 0 ]; then' in content
    assert "DB deploy attempt failed (exit=\\$rc)" in content


def test_workflow_ssh_transport_contract_and_completion_marker_enforcement():
    content = _read(".github/workflows/auto-deploy.yml")

    assert "<<'REMOTE_SCRIPT'" in content
    assert 'ssh "$SERVER_USER@$SERVER_HOST" bash -seuo pipefail -s --' in content
    assert 'PROJECT_PATH="$1"' in content
    assert 'PRERELEASE_FULL_REINSTALL="${7:-false}"' in content
    assert 'GITHUB_EVENT_NAME="${8:-}"' in content
    assert 'REMOTE_SCRIPT_CONTENT="${REMOTE_SCRIPT_CONTENT//\\\\$/\\$}"' in content

    assert 'echo "__TVPN_DEPLOY_DONE__"' in content
    assert 'grep -Fq "__TVPN_DEPLOY_DONE__" "$DEPLOY_LOG_FILE"' in content
    assert "Remote deploy completion marker missing" in content


def test_workflow_uses_robust_postgres_password_resolution_markers():
    content = _read(".github/workflows/auto-deploy.yml")
    transformed_content = content.replace("\\$", "$")

    assert "normalize_secret_value()" in content
    assert "resolve_postgres_password()" in content
    assert 'while IFS= read -r line || [ -n "\\$line" ]; do' in content
    assert (
        'resolved="\\$(compose_exec bloobcat_db sh -c \'printf %s "\\${POSTGRES_PASSWORD:-}"\' 2>/dev/null || true)"'
        in content
    )
    assert (
        "POSTGRES_PASSWORD appears unresolved (contains literal dollar-brace placeholder pattern)"
        in content
    )
    assert "contains literal ${...} placeholder" not in transformed_content
    assert 'POSTGRES_PASSWORD="\\$(resolve_postgres_password)"' in content

    assert "sed -n 's/^POSTGRES_PASSWORD=//p'" not in content


def test_workflow_exec_calls_disable_stdin_for_piped_remote_script():
    content = _read(".github/workflows/auto-deploy.yml")
    remote_script = _extract_remote_script_block(content)
    normalized_remote_script = _normalize_multiline_shell_commands(remote_script)

    helper_match = re.search(
        r"compose_exec\(\) \{.*?\n        \}",
        normalized_remote_script,
        re.DOTALL,
    )
    assert helper_match, "compose_exec helper not found in REMOTE_SCRIPT block"
    helper_block = helper_match.group(0)

    assert "docker compose $COMPOSE_ARGS exec --help" in helper_block
    assert "grep -q -- '--interactive'" in helper_block
    assert "COMPOSE_EXEC_SUPPORTS_INTERACTIVE=1" in helper_block
    assert "COMPOSE_EXEC_SUPPORTS_INTERACTIVE=0" in helper_block
    assert (
        'docker compose $COMPOSE_ARGS exec --interactive=false -T "$@" </dev/null'
        in helper_block
    )
    assert 'docker compose $COMPOSE_ARGS exec -T "$@" </dev/null' in helper_block

    remote_script_without_helper = normalized_remote_script.replace(helper_block, "")
    direct_exec_calls_outside_helper = re.findall(
        r"docker compose[^\n]*\bexec\b[^\n]*",
        remote_script_without_helper,
    )

    assert not direct_exec_calls_outside_helper, (
        "All docker compose exec call sites in REMOTE_SCRIPT must go through "
        f"compose_exec helper. Offending calls: {direct_exec_calls_outside_helper}"
    )

    # Critical early call site: DB readiness probe.
    assert "compose_exec bloobcat_db pg_isready -U postgres -d postgres" in content

    # Early env/probe exec calls that run before/around fallback gates.
    assert "compose_exec directus sh -c 'printf %s \"\\${DB_USER:-}\"'" in content
    assert "compose_exec directus sh -c 'printf %s \"\\${DB_PASSWORD:-}\"'" in content
    assert "DB_AUTH_PROBE_CONTRACT: in-container-sh-posargs" in content
    assert (
        'compose_exec bloobcat_db sh -lc \'PGPASSWORD="\\$1" psql -h 127.0.0.1 -U "\\$2" -d "\\$3" -c "SELECT 1;"\' sh "\\$DIRECTUS_DB_PASSWORD" "\\$DIRECTUS_DB_USER" "\\$DIRECTUS_DB_DATABASE"'
        in content
    )


def test_workflow_directus_super_setup_is_blocking_after_retry_exhaustion():
    content = _read(".github/workflows/auto-deploy.yml")

    assert "SUPER_SETUP_OK=0" in content
    assert "max_attempts=4" in content
    assert 'while [ "\\$attempt" -le "\\$max_attempts" ]; do' in content
    assert 'if [ "\\$attempt" -lt "\\$max_attempts" ]; then' in content
    assert 'if [ "\\$SUPER_SETUP_OK" -ne 1 ]; then' in content
    assert "Directus super-setup failed after retries; abort deploy" in content

    setup_start = content.index("SUPER_SETUP_OK=0")
    setup_retry = content.index("max_attempts=4")
    setup_fail_guard = content.index('if [ "\\$SUPER_SETUP_OK" -ne 1 ]; then')
    setup_abort = content.index(
        "Directus super-setup failed after retries; abort deploy"
    )
    setup_exit = content.index("exit 1", setup_abort)

    assert setup_start < setup_retry < setup_fail_guard < setup_abort < setup_exit


def test_workflow_post_super_setup_runtime_gate_is_fail_closed_with_self_heal_and_reverify():
    content = _read(".github/workflows/auto-deploy.yml")
    runtime_gate_block = _extract_post_super_setup_runtime_gate_block(content).replace(
        "\\$", "$"
    )

    assert "POST_SETUP_VERIFY_OK=0" in runtime_gate_block
    assert (
        runtime_gate_block.count(
            "(python scripts/verify_runtime_state.py || python3 scripts/verify_runtime_state.py)"
        )
        >= 2
    )
    assert (
        "Runtime-state verify failed after super-setup; attempting FK self-heal..."
        in runtime_gate_block
    )
    assert (
        "(python scripts/self_heal_runtime_state.py || python3 scripts/self_heal_runtime_state.py)"
        in runtime_gate_block
    )
    assert (
        "Self-heal completed, re-running runtime-state verification..."
        in runtime_gate_block
    )
    assert 'if [ "$POST_SETUP_VERIFY_OK" -ne 1 ]; then' in runtime_gate_block
    assert (
        "Post-super-setup runtime-state gate failed; abort deploy" in runtime_gate_block
    )

    gate_start = runtime_gate_block.index("POST_SETUP_VERIFY_OK=0")
    first_verify = runtime_gate_block.index(
        "(python scripts/verify_runtime_state.py || python3 scripts/verify_runtime_state.py)"
    )
    self_heal = runtime_gate_block.index("attempting FK self-heal")
    self_heal_call = runtime_gate_block.index(
        "(python scripts/self_heal_runtime_state.py || python3 scripts/self_heal_runtime_state.py)"
    )
    second_verify = runtime_gate_block.index(
        "(python scripts/verify_runtime_state.py || python3 scripts/verify_runtime_state.py)",
        first_verify + 1,
    )
    still_failing = runtime_gate_block.index(
        "Runtime-state verification still failing after self-heal"
    )
    fail_guard = runtime_gate_block.index('if [ "$POST_SETUP_VERIFY_OK" -ne 1 ]; then')
    abort_marker = runtime_gate_block.index(
        "Post-super-setup runtime-state gate failed; abort deploy"
    )
    exit_marker = runtime_gate_block.index("exit 1", abort_marker)

    assert (
        gate_start
        < first_verify
        < self_heal
        < self_heal_call
        < second_verify
        < still_failing
        < fail_guard
        < abort_marker
        < exit_marker
    )


def test_workflow_post_super_setup_runtime_gate_runs_before_tariffs_seed():
    content = _read(".github/workflows/auto-deploy.yml")

    super_setup_guard = content.index('if [ "\\$SUPER_SETUP_OK" -ne 1 ]; then')
    runtime_gate = content.index(
        "Running post-super-setup runtime-state verification gate..."
    )
    tariffs_seed = content.index("TARIFFS_SEED_OK=0")

    assert super_setup_guard < runtime_gate < tariffs_seed


def test_runtime_fk_self_heal_script_covers_all_invariant_guards():
    content = _read("scripts/self_heal_runtime_state.py")

    assert "from bloobcat.clients import TORTOISE_ORM" in content
    assert "await Tortoise.init(config=TORTOISE_ORM)" in content
    assert "await ensure_active_tariffs_fk_cascade()" in content
    assert "await ensure_notification_marks_fk_cascade()" in content
    assert "await ensure_promo_usages_fk_cascade()" in content
    assert "await ensure_users_referred_by_fk_set_null()" in content
    assert "await Tortoise.close_connections()" in content
    assert "Runtime FK self-heal incomplete" in content


def test_workflow_fk_fix_block_has_deterministic_markers_and_no_escaped_do_delimiter():
    content = _read(".github/workflows/auto-deploy.yml")
    fk_fix_block = _extract_fk_fix_block(content)
    normalized_fk_fix_block = _normalize_multiline_shell_commands(fk_fix_block)
    runtime_fk_fix_block = normalized_fk_fix_block.replace("\\$", "$")

    assert "FK_FIX_OK=0" in runtime_fk_fix_block
    assert "compose_exec bloobcat_db" in runtime_fk_fix_block
    assert (
        'docker compose $COMPOSE_ARGS config --services | grep -qx "bloobcat_db"'
        not in runtime_fk_fix_block
    )
    assert "psql -U postgres -d postgres -v ON_ERROR_STOP=1" in runtime_fk_fix_block

    assert "FK_REPAIR_SQL=\"$(cat <<-'FK_REPAIR_SQL_PAYLOAD'" in runtime_fk_fix_block
    assert "DO $tvpn_fk$" in runtime_fk_fix_block
    assert "DECLARE" in runtime_fk_fix_block
    assert "schema_rec RECORD;" in runtime_fk_fix_block
    assert "fk_rec RECORD;" in runtime_fk_fix_block
    assert "ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I" in runtime_fk_fix_block
    assert (
        "ALTER TABLE %I.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I.%I (%I) ON DELETE CASCADE"
        in runtime_fk_fix_block
    )
    assert re.search(r"END\s+\$tvpn_fk\$;", runtime_fk_fix_block)
    assert "FK_REPAIR_SQL_PAYLOAD" in runtime_fk_fix_block
    assert '-c "$FK_REPAIR_SQL"' in runtime_fk_fix_block
    assert "rc.delete_rule" in runtime_fk_fix_block
    assert "DO $$" not in runtime_fk_fix_block

    assert not re.search(r"DO\s+\\+\$\\+\$", runtime_fk_fix_block)


def test_workflow_fk_fix_block_is_fail_closed_with_abort_marker():
    content = _read(".github/workflows/auto-deploy.yml")
    runtime_fk_fix_block = _extract_fk_fix_block(content).replace("\\$", "$")

    assert "skipping FK DB fix" not in runtime_fk_fix_block
    assert (
        "Required DB service 'bloobcat_db' not found in compose config; FK DB fix aborted"
        not in runtime_fk_fix_block
    )
    assert (
        'docker compose $COMPOSE_ARGS config --services | grep -qx "bloobcat_db"'
        not in runtime_fk_fix_block
    )

    compose_exec_marker = runtime_fk_fix_block.index("if compose_exec bloobcat_db")
    fail_guard = runtime_fk_fix_block.index('if [ "$FK_FIX_OK" -ne 1 ]; then')
    abort_marker = runtime_fk_fix_block.index('echo "FK DB fix failed; abort deploy"')
    exit_marker = runtime_fk_fix_block.index("exit 1", abort_marker)

    assert compose_exec_marker < fail_guard < abort_marker < exit_marker


def test_deploy_troubleshooting_doc_covers_new_reliability_flow():
    rel_path = "memory-base/deploy-troubleshooting.md"
    content = _read(rel_path)

    assert content.lstrip().startswith("# ")
    assert "Directus unhealthy из-за рассинхрона DB auth" in content
    assert "scripts/db/reconcile_postgres_auth.sh" in content
    assert "scripts/db/destructive_reset_once.sh" in content
    assert "28P01" in content


def test_deploy_troubleshooting_doc_marks_directus_super_setup_failures_as_blocking():
    rel_path = "memory-base/deploy-troubleshooting.md"
    content = _read(rel_path)

    assert (
        "`DIRECTUS_ADMIN_EMAIL not set` / `DIRECTUS_ADMIN_PASSWORD not set`" in content
    )
    assert "Ошибки внутри `directus_super_setup.py`" in content
    assert (
        "- **Блокирующий статус**: **да**. После исчерпания retry для `directus super-setup` "
        "workflow завершает деплой с `exit 1` (fail-closed)." in content
    )
    assert (
        "- **Блокирующий статус**: **да**. Если `directus_super_setup.py` не проходит после всех retry, "
        "deploy прерывается (`exit 1`) и требует ручного исправления причины."
        in content
    )
    assert "Workflow фиксирует warning и продолжает деплой" not in content
    assert "идёт по warning-пути" not in content
    assert "fail-closed" in content


def test_directus_super_setup_aligns_user_relations_with_cascade_delete_action():
    content = _read("scripts/directus_super_setup.py")

    assert '"active_tariffs"' in content
    assert '"promo_usages"' in content
    assert '"notification_marks"' in content
    assert '"family_invites"' in content
    assert "schema_on_delete: Optional[str] = None" in content
    assert "if schema_on_delete:" in content
    assert 'payload["schema"] = {"on_delete": schema_on_delete}' in content
    assert re.search(
        r'\(\s*"active_tariffs",\s*"user_id",\s*"active_tariffs_list",[\s\S]*?"delete",\s*"CASCADE",',
        content,
    )
    assert re.search(
        r'\(\s*"promo_usages",\s*"user_id",\s*"promo_usages_list",[\s\S]*?"delete",\s*"CASCADE",',
        content,
    )
    assert re.search(
        r'\(\s*"notification_marks",\s*"user_id",\s*"notification_marks_list",[\s\S]*?"delete",\s*"CASCADE",',
        content,
    )
    assert "schema_on_delete," in content
    assert ") in relation_specs:" in content
    assert "one_deselect_action=one_deselect_action" in content
    assert "schema_on_delete=schema_on_delete" in content
    assert '"one_deselect_action": "delete"' in content
    assert 'many_field="referred_by"' in content
    assert 'one_deselect_action="nullify"' in content
    assert 'schema_on_delete="SET NULL"' in content


def test_remnawave_sync_hook_has_predelete_fk_safety_cleanup_for_users():
    # The legacy by-type extensions/hooks/<name>/index.js layout was purged;
    # Directus 11 only loads top-level packages with their own package.json.
    content = _read("directus/extensions/remnawave-sync/src/index.js")

    assert "const SCHEMA_CAPABILITY_CACHE = {" in content
    assert "tables: new Map()," in content
    assert "columns: new Map()," in content
    assert "const hasTableSafe = async (database, tableName) => {" in content
    assert (
        "const hasColumnSafe = async (database, tableName, columnName) => {" in content
    )
    assert "schema capability check failed" in content
    assert "const normalizeUserIds = (payload, meta) => {" in content
    assert "const applyDeleteSafetyCleanup = async (database, userIds) => {" in content
    assert 'await hasTableSafe(database, "active_tariffs")' in content
    assert 'await hasColumnSafe(database, "active_tariffs", "user_id")' in content
    assert 'typeof database.transaction !== "function"' in content
    assert "await database.transaction(async (trx) => {" in content
    assert 'await trx("active_tariffs").whereIn("user_id", userIds).delete()' in content
    assert 'await hasTableSafe(database, "notification_marks")' in content
    assert 'await hasColumnSafe(database, "notification_marks", "user_id")' in content
    assert (
        'await trx("notification_marks").whereIn("user_id", userIds).delete()'
        in content
    )
    assert 'await hasTableSafe(database, "promo_usages")' in content
    assert 'await hasColumnSafe(database, "promo_usages", "user_id")' in content
    assert 'await trx("promo_usages").whereIn("user_id", userIds).delete()' in content
    assert 'await hasTableSafe(database, "family_invites")' in content
    assert 'await hasColumnSafe(database, "family_invites", "owner_id")' in content
    assert (
        'await trx("family_invites").whereIn("owner_id", userIds).delete()' in content
    )
    assert 'await hasTableSafe(database, "subscription_freezes")' in content
    assert 'await hasColumnSafe(database, "subscription_freezes", "user_id")' in content
    assert (
        'await trx("subscription_freezes").whereIn("user_id", userIds).delete()'
        in content
    )
    assert 'await hasColumnSafe(database, "users", "referred_by")' in content
    assert (
        'await trx("users").whereIn("referred_by", userIds).update({ referred_by: null })'
        in content
    )
    assert "Number.parseInt" not in content
    assert "/^[1-9]\\d*$/.test(rawId)" in content
    assert "const parsed = Number(rawId);" in content
    assert "Number.isSafeInteger(parsed)" in content
    assert "parsed > 0" in content
    assert "await applyDeleteSafetyCleanup(database, ids);" in content
    assert 'console.info("[remnawave-sync] users pre-delete cleanup", {' in content
    assert "schemaCheckFailed" in content
    assert "if (cleanupReport.schemaCheckFailed) {" in content
    assert 'console.warn("[remnawave-sync] users pre-delete skipped", {' in content
    assert (
        'const CLEANUP_SCHEMA_CHECK_FAILED_REASON = "cleanup_schema_check_failed";'
        in content
    )
    assert "reason: CLEANUP_SCHEMA_CHECK_FAILED_REASON" in content
    assert "userIds: ids" in content
    assert "const error = new Error(CLEANUP_SCHEMA_CHECK_FAILED_REASON);" in content
    assert "error.reason = CLEANUP_SCHEMA_CHECK_FAILED_REASON;" in content
    assert "throw error;" in content

    schema_fail_guard = content.index("if (cleanupReport.schemaCheckFailed) {")
    fail_closed_throw = content.index("throw error;", schema_fail_guard)
    pre_delete_loop = content.index("for (const userId of ids) {")
    assert fail_closed_throw < pre_delete_loop


def test_remnawave_sync_extension_variants_keep_predelete_fk_safety_cleanup_for_users():
    extension_paths = [
        "directus/extensions/remnawave-sync/src/index.js",
        "directus/extensions/remnawave-sync/dist/index.js",
    ]

    for rel_path in extension_paths:
        content = _read(rel_path)

        assert "const SCHEMA_CAPABILITY_CACHE = {" in content
        assert "tables: new Map()," in content
        assert "columns: new Map()," in content
        assert "const hasTableSafe = async (database, tableName) => {" in content
        assert (
            "const hasColumnSafe = async (database, tableName, columnName) => {"
            in content
        )
        assert "schema capability check failed" in content
        assert "const normalizeUserIds = (payload, meta) => {" in content
        assert (
            "const applyDeleteSafetyCleanup = async (database, userIds) => {" in content
        )
        assert 'await hasTableSafe(database, "active_tariffs")' in content
        assert 'await hasColumnSafe(database, "active_tariffs", "user_id")' in content
        assert 'typeof database.transaction !== "function"' in content
        assert "await database.transaction(async (trx) => {" in content
        assert (
            'await trx("active_tariffs").whereIn("user_id", userIds).delete()'
            in content
        )
        assert 'await hasTableSafe(database, "notification_marks")' in content
        assert (
            'await hasColumnSafe(database, "notification_marks", "user_id")' in content
        )
        assert (
            'await trx("notification_marks").whereIn("user_id", userIds).delete()'
            in content
        )
        assert 'await hasTableSafe(database, "promo_usages")' in content
        assert 'await hasColumnSafe(database, "promo_usages", "user_id")' in content
        assert 'await trx("promo_usages").whereIn("user_id", userIds).delete()' in content
        assert 'await hasTableSafe(database, "family_invites")' in content
        assert 'await hasColumnSafe(database, "family_invites", "owner_id")' in content
        assert (
            'await trx("family_invites").whereIn("owner_id", userIds).delete()'
            in content
        )
        assert 'await hasTableSafe(database, "subscription_freezes")' in content
        assert (
            'await hasColumnSafe(database, "subscription_freezes", "user_id")'
            in content
        )
        assert (
            'await trx("subscription_freezes").whereIn("user_id", userIds).delete()'
            in content
        )
        assert 'await hasColumnSafe(database, "users", "referred_by")' in content
        assert (
            'await trx("users").whereIn("referred_by", userIds).update({ referred_by: null })'
            in content
        )
        assert "Number.parseInt" not in content
        assert "/^[1-9]\\d*$/.test(rawId)" in content
        assert "const parsed = Number(rawId);" in content
        assert "Number.isSafeInteger(parsed)" in content
        assert "parsed > 0" in content
        assert "await applyDeleteSafetyCleanup(database, ids);" in content
        assert 'console.info("[remnawave-sync] users pre-delete cleanup", {' in content
        assert "schemaCheckFailed" in content
        assert "if (cleanupReport.schemaCheckFailed) {" in content
        assert 'console.warn("[remnawave-sync] users pre-delete skipped", {' in content
        assert (
            'const CLEANUP_SCHEMA_CHECK_FAILED_REASON = "cleanup_schema_check_failed";'
            in content
        )
        assert "reason: CLEANUP_SCHEMA_CHECK_FAILED_REASON" in content
        assert "userIds: ids" in content
        assert "const error = new Error(CLEANUP_SCHEMA_CHECK_FAILED_REASON);" in content
        assert "error.reason = CLEANUP_SCHEMA_CHECK_FAILED_REASON;" in content
        assert "throw error;" in content

        schema_fail_guard = content.index("if (cleanupReport.schemaCheckFailed) {")
        fail_closed_throw = content.index("throw error;", schema_fail_guard)
        pre_delete_loop = content.index("for (const userId of ids) {")
        assert fail_closed_throw < pre_delete_loop


def test_remnawave_sync_users_cleanup_transaction_rolls_back_on_mutation_failure_proxy():
    script = textwrap.dedent(
        """
        import registerHook from './directus/extensions/remnawave-sync/src/index.js';

        const deepClone = (value) => JSON.parse(JSON.stringify(value));

        const createDatabase = (state) => {
          const makeQuery = (workingState) => (tableName) => ({
            whereIn(column, ids) {
              const idSet = new Set(ids);
              return {
                async delete() {
                  if (tableName === 'notification_marks') {
                    throw new Error('forced mutation failure');
                  }
                  const source = workingState[tableName] || [];
                  const kept = [];
                  let affected = 0;
                  for (const row of source) {
                    if (idSet.has(row[column])) {
                      affected += 1;
                    } else {
                      kept.push(row);
                    }
                  }
                  workingState[tableName] = kept;
                  return affected;
                },
                async update(patch) {
                  const source = workingState[tableName] || [];
                  let affected = 0;
                  for (const row of source) {
                    if (idSet.has(row[column])) {
                      Object.assign(row, patch);
                      affected += 1;
                    }
                  }
                  return affected;
                }
              };
            }
          });

          const db = makeQuery(state);
          db.schema = {
            async hasTable(tableName) {
              return Object.prototype.hasOwnProperty.call(state, tableName);
            },
            async hasColumn() {
              return true;
            }
          };
          db.transaction = async (cb) => {
            const snapshot = deepClone(state);
            const trx = makeQuery(snapshot);
            await cb(trx);
            for (const key of Object.keys(state)) {
              delete state[key];
            }
            Object.assign(state, snapshot);
          };
          return db;
        };

        const state = {
          users: [{ id: 100, referred_by: null }, { id: 200, referred_by: 100 }],
          active_tariffs: [{ id: 1, user_id: 100 }],
          family_invites: [{ id: 5, owner_id: 100 }],
          notification_marks: [{ id: 10, user_id: 100 }],
          promo_usages: [{ id: 15, user_id: 100 }],
          subscription_freezes: [{ id: 20, user_id: 100 }]
        };
        const before = deepClone(state);

        let deleteHandler = null;
        registerHook(
          {
            action() {},
            filter(event, handler) {
              if (event === 'items.delete') {
                deleteHandler = handler;
              }
            }
          },
          { database: createDatabase(state) }
        );

        if (typeof deleteHandler !== 'function') {
          throw new Error('items.delete handler not registered');
        }

        let failed = false;
        try {
          await deleteHandler([100], { collection: 'users', keys: [100] });
        } catch (error) {
          failed = error?.message === 'forced mutation failure';
        }

        if (!failed) {
          throw new Error('expected deterministic mutation failure');
        }
        if (JSON.stringify(state) !== JSON.stringify(before)) {
          throw new Error('transaction rollback failed: partial cleanup persisted');
        }
        """
    )

    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_remnawave_sync_users_cleanup_ignores_oversized_numeric_string_ids_proxy():
    script = textwrap.dedent(
        """
        import registerHook from './directus/extensions/remnawave-sync/src/index.js';

        const cleanupTargets = [];

        const state = {
          users: [
            { id: 42, referred_by: null },
            { id: 7, referred_by: 42 },
            { id: 9007199254740992, referred_by: null }
          ],
          active_tariffs: [
            { id: 1, user_id: 42 },
            { id: 2, user_id: 7 },
            { id: 3, user_id: 9007199254740992 }
          ],
          promo_usages: [
            { id: 31, user_id: 42 },
            { id: 32, user_id: 7 },
            { id: 33, user_id: 9007199254740992 }
          ],
          family_invites: [
            { id: 4, owner_id: 42 },
            { id: 5, owner_id: 7 },
            { id: 6, owner_id: 9007199254740992 }
          ],
          notification_marks: [
            { id: 10, user_id: 42 },
            { id: 11, user_id: 7 },
            { id: 12, user_id: 9007199254740992 }
          ],
          subscription_freezes: [
            { id: 20, user_id: 42 },
            { id: 21, user_id: 7 },
            { id: 22, user_id: 9007199254740992 }
          ]
        };

        const makeQuery = (workingState) => (tableName) => ({
          whereIn(column, ids) {
            cleanupTargets.push({ tableName, column, ids: [...ids] });
            const idSet = new Set(ids);
            return {
              async delete() {
                const source = workingState[tableName] || [];
                workingState[tableName] = source.filter((row) => !idSet.has(row[column]));
                return source.length - workingState[tableName].length;
              },
              async update(patch) {
                let affected = 0;
                for (const row of workingState[tableName] || []) {
                  if (idSet.has(row[column])) {
                    Object.assign(row, patch);
                    affected += 1;
                  }
                }
                return affected;
              }
            };
          }
        });

        const database = makeQuery(state);
        database.schema = {
          async hasTable() {
            return true;
          },
          async hasColumn() {
            return true;
          }
        };
        database.transaction = async (cb) => {
          await cb(makeQuery(state));
        };

        let deleteHandler = null;
        registerHook(
          {
            action() {},
            filter(event, handler) {
              if (event === 'items.delete') {
                deleteHandler = handler;
              }
            }
          },
          { database }
        );

        if (typeof deleteHandler !== 'function') {
          throw new Error('items.delete handler not registered');
        }

        await deleteHandler(['9007199254740993', '42', 7], { collection: 'users' });

        const cleanupIdSets = cleanupTargets
          .filter((call) => call.column === 'user_id' || call.column === 'owner_id' || call.column === 'referred_by')
          .map((call) => call.ids.join(','));
        if (!cleanupIdSets.every((serialized) => serialized === '42,7')) {
          throw new Error(`unexpected cleanup targets: ${JSON.stringify(cleanupTargets)}`);
        }

        if (!state.active_tariffs.some((row) => row.user_id === 9007199254740992)) {
          throw new Error('oversized-id row was unexpectedly cleaned up');
        }
        if (!state.family_invites.some((row) => row.owner_id === 9007199254740992)) {
          throw new Error('oversized-id family invite row was unexpectedly cleaned up');
        }
        if (!state.promo_usages.some((row) => row.user_id === 9007199254740992)) {
          throw new Error('oversized-id promo usage row was unexpectedly cleaned up');
        }
        """
    )

    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
