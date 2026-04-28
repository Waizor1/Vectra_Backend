import importlib

import pytest


def _load_module(name: str):
    return importlib.import_module(name)


class _FakeConnection:
    def __init__(
        self,
        *,
        runtime_schema_rows: list[dict],
        fk_rows: dict[tuple[str, str, str, str, str], list[dict]],
        retry_index_rows: list[dict],
    ):
        self._runtime_schema_rows = runtime_schema_rows
        self._fk_rows = fk_rows
        self._retry_index_rows = retry_index_rows

    async def fetch(self, query: str, *params):
        if "FROM pg_namespace n" in query:
            return self._runtime_schema_rows

        if "information_schema.table_constraints" in query:
            key = (params[0], params[1], params[2], params[3], params[4])
            return self._fk_rows.get(key, [])

        if "FROM pg_indexes" in query:
            return self._retry_index_rows

        raise AssertionError(f"Unexpected query in test fake: {query}")


@pytest.mark.asyncio
async def test_verify_runtime_state_passes_when_all_invariants_hold():
    verifier = _load_module("scripts.verify_runtime_state")
    conn = _FakeConnection(
        runtime_schema_rows=[{"schema_name": "public"}],
        fk_rows={
            ("active_tariffs", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_active_tariffs_user", "delete_rule": "CASCADE"}
            ],
            ("notification_marks", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_notification_marks_user", "delete_rule": "CASCADE"}
            ],
            ("promo_usages", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_promo_usages_user", "delete_rule": "CASCADE"}
            ],
            ("users", "referred_by", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "users_referred_by_foreign", "delete_rule": "SET NULL"}
            ],
        },
        retry_index_rows=[
            {
                "schemaname": "public",
                "indexname": "ux_remnawave_retry_jobs_active_user",
                "indexdef": (
                    'CREATE UNIQUE INDEX "ux_remnawave_retry_jobs_active_user" '
                    'ON public."remnawave_retry_jobs" USING btree ("job_type", "user_id") '
                    "WHERE (\"status\" = ANY (ARRAY['pending'::text, 'processing'::text]))"
                ),
            }
        ],
    )

    await verifier.verify_runtime_state(conn=conn)


@pytest.mark.asyncio
async def test_verify_runtime_state_accepts_casted_any_array_predicate_variant():
    verifier = _load_module("scripts.verify_runtime_state")
    conn = _FakeConnection(
        runtime_schema_rows=[{"schema_name": "public"}],
        fk_rows={
            ("active_tariffs", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_active_tariffs_user", "delete_rule": "CASCADE"}
            ],
            ("notification_marks", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_notification_marks_user", "delete_rule": "CASCADE"}
            ],
            ("promo_usages", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_promo_usages_user", "delete_rule": "CASCADE"}
            ],
            ("users", "referred_by", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "users_referred_by_foreign", "delete_rule": "SET NULL"}
            ],
        },
        retry_index_rows=[
            {
                "schemaname": "public",
                "indexname": "ux_remnawave_retry_jobs_active_user",
                "indexdef": (
                    'CREATE UNIQUE INDEX "ux_remnawave_retry_jobs_active_user" '
                    'ON public."remnawave_retry_jobs" USING btree ("job_type", "user_id") '
                    "WHERE ((status)::text = ANY ((ARRAY['pending'::character varying, 'processing'::character varying])::text[]))"
                ),
            }
        ],
    )

    await verifier.verify_runtime_state(conn=conn)


@pytest.mark.asyncio
async def test_verify_runtime_state_fails_on_drift_with_actionable_message():
    verifier = _load_module("scripts.verify_runtime_state")
    conn = _FakeConnection(
        runtime_schema_rows=[{"schema_name": "public"}],
        fk_rows={
            ("active_tariffs", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_active_tariffs_user", "delete_rule": "NO ACTION"}
            ],
            ("notification_marks", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_notification_marks_user", "delete_rule": "CASCADE"}
            ],
            ("promo_usages", "user_id", "users", "id", "public"): [],
            ("users", "referred_by", "users", "id", "public"): [],
        },
        retry_index_rows=[],
    )

    with pytest.raises(RuntimeError) as exc:
        await verifier.verify_runtime_state(conn=conn)

    message = str(exc.value)
    assert "active_tariffs.user_id -> users.id" in message
    assert "expected ON DELETE CASCADE" in message
    assert "promo_usages.user_id -> users.id" in message
    assert "users.referred_by -> users.id" in message
    assert "ux_remnawave_retry_jobs_active_user" in message


@pytest.mark.asyncio
async def test_verify_runtime_state_fails_when_retry_index_definition_is_stale():
    verifier = _load_module("scripts.verify_runtime_state")
    conn = _FakeConnection(
        runtime_schema_rows=[{"schema_name": "public"}],
        fk_rows={
            ("active_tariffs", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_active_tariffs_user", "delete_rule": "CASCADE"}
            ],
            ("notification_marks", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_notification_marks_user", "delete_rule": "CASCADE"}
            ],
            ("promo_usages", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_promo_usages_user", "delete_rule": "CASCADE"}
            ],
            ("users", "referred_by", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "users_referred_by_foreign", "delete_rule": "SET NULL"}
            ],
        },
        retry_index_rows=[
            {
                "schemaname": "public",
                "indexname": "ux_remnawave_retry_jobs_active_user",
                "indexdef": (
                    "CREATE UNIQUE INDEX ux_remnawave_retry_jobs_active_user "
                    "ON remnawave_retry_jobs (user_id) "
                    "WHERE status IN ('pending')"
                ),
            }
        ],
    )

    with pytest.raises(RuntimeError) as exc:
        await verifier.verify_runtime_state(conn=conn)

    message = str(exc.value)
    assert "Invalid index definition for ux_remnawave_retry_jobs_active_user" in message
    assert "Expected UNIQUE index on remnawave_retry_jobs (job_type, user_id)" in message
    assert "Recreate ux_remnawave_retry_jobs_active_user" in message


@pytest.mark.asyncio
async def test_verify_runtime_state_fails_when_retry_index_name_is_ambiguous_across_schemas():
    verifier = _load_module("scripts.verify_runtime_state")
    conn = _FakeConnection(
        runtime_schema_rows=[{"schema_name": "public"}],
        fk_rows={
            ("active_tariffs", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_active_tariffs_user", "delete_rule": "CASCADE"}
            ],
            ("notification_marks", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_notification_marks_user", "delete_rule": "CASCADE"}
            ],
            ("promo_usages", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_promo_usages_user", "delete_rule": "CASCADE"}
            ],
            ("users", "referred_by", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "users_referred_by_foreign", "delete_rule": "SET NULL"}
            ],
        },
        retry_index_rows=[
            {
                "schemaname": "public",
                "indexname": "ux_remnawave_retry_jobs_active_user",
                "indexdef": (
                    "CREATE UNIQUE INDEX ux_remnawave_retry_jobs_active_user "
                    "ON remnawave_retry_jobs (job_type, user_id) "
                    "WHERE status IN ('pending', 'processing')"
                ),
            },
            {
                "schemaname": "shadow",
                "indexname": "ux_remnawave_retry_jobs_active_user",
                "indexdef": (
                    "CREATE UNIQUE INDEX ux_remnawave_retry_jobs_active_user "
                    "ON shadow.remnawave_retry_jobs (job_type, user_id) "
                    "WHERE status IN ('pending')"
                ),
            },
        ],
    )

    with pytest.raises(RuntimeError) as exc:
        await verifier.verify_runtime_state(conn=conn)

    message = str(exc.value)
    assert "Ambiguous index definition for ux_remnawave_retry_jobs_active_user" in message
    assert "expected exactly one matching definition across schemas" in message
    assert "found 2 rows" in message
    assert "public:" in message
    assert "shadow:" in message


@pytest.mark.asyncio
async def test_verify_runtime_state_fails_when_retry_index_has_extra_key_column():
    verifier = _load_module("scripts.verify_runtime_state")
    conn = _FakeConnection(
        runtime_schema_rows=[{"schema_name": "public"}],
        fk_rows={
            ("active_tariffs", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_active_tariffs_user", "delete_rule": "CASCADE"}
            ],
            ("notification_marks", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_notification_marks_user", "delete_rule": "CASCADE"}
            ],
            ("promo_usages", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_promo_usages_user", "delete_rule": "CASCADE"}
            ],
            ("users", "referred_by", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "users_referred_by_foreign", "delete_rule": "SET NULL"}
            ],
        },
        retry_index_rows=[
            {
                "schemaname": "public",
                "indexname": "ux_remnawave_retry_jobs_active_user",
                "indexdef": (
                    "CREATE UNIQUE INDEX ux_remnawave_retry_jobs_active_user "
                    "ON remnawave_retry_jobs (job_type, user_id, retry_after) "
                    "WHERE status IN ('pending', 'processing')"
                ),
            }
        ],
    )

    with pytest.raises(RuntimeError) as exc:
        await verifier.verify_runtime_state(conn=conn)

    message = str(exc.value)
    assert "Invalid index definition for ux_remnawave_retry_jobs_active_user" in message
    assert "index key columns are not (job_type, user_id)" in message


@pytest.mark.asyncio
async def test_verify_runtime_state_fails_when_retry_index_predicate_has_extra_constraint():
    verifier = _load_module("scripts.verify_runtime_state")
    conn = _FakeConnection(
        runtime_schema_rows=[{"schema_name": "public"}],
        fk_rows={
            ("active_tariffs", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_active_tariffs_user", "delete_rule": "CASCADE"}
            ],
            ("notification_marks", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_notification_marks_user", "delete_rule": "CASCADE"}
            ],
            ("promo_usages", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "fk_promo_usages_user", "delete_rule": "CASCADE"}
            ],
            ("users", "referred_by", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "users_referred_by_foreign", "delete_rule": "SET NULL"}
            ],
        },
        retry_index_rows=[
            {
                "schemaname": "public",
                "indexname": "ux_remnawave_retry_jobs_active_user",
                "indexdef": (
                    "CREATE UNIQUE INDEX ux_remnawave_retry_jobs_active_user "
                    "ON remnawave_retry_jobs (job_type, user_id) "
                    "WHERE status = ANY (ARRAY['pending'::text, 'processing'::text]) "
                    "AND retry_after IS NOT NULL"
                ),
            }
        ],
    )

    with pytest.raises(RuntimeError) as exc:
        await verifier.verify_runtime_state(conn=conn)

    message = str(exc.value)
    assert "Invalid index definition for ux_remnawave_retry_jobs_active_user" in message
    assert "index predicate must enforce exactly pending + processing statuses" in message


@pytest.mark.asyncio
async def test_verify_runtime_state_fails_when_runtime_schema_resolution_is_ambiguous():
    verifier = _load_module("scripts.verify_runtime_state")
    conn = _FakeConnection(
        runtime_schema_rows=[{"schema_name": "public"}, {"schema_name": "shadow"}],
        fk_rows={},
        retry_index_rows=[
            {
                "schemaname": "public",
                "indexname": "ux_remnawave_retry_jobs_active_user",
                "indexdef": (
                    "CREATE UNIQUE INDEX ux_remnawave_retry_jobs_active_user "
                    "ON remnawave_retry_jobs (job_type, user_id) "
                    "WHERE status IN ('pending', 'processing')"
                ),
            }
        ],
    )

    with pytest.raises(RuntimeError) as exc:
        await verifier.verify_runtime_state(conn=conn)

    message = str(exc.value)
    assert "Ambiguous runtime schema for FK verification" in message
    assert "found 2 candidates: public, shadow" in message
    assert "Drop shadow/duplicate schemas" in message


class _FakeAerichCommand:
    def __init__(self, *args, **kwargs):
        self.init_calls = 0
        self.upgrade_calls = 0

    async def init(self):
        self.init_calls += 1

    async def upgrade(self, *, run_in_transaction: bool):
        assert run_in_transaction is True
        self.upgrade_calls += 1


@pytest.mark.asyncio
async def test_apply_migrations_runs_runtime_verify_by_default(monkeypatch):
    apply_migrations = _load_module("scripts.apply_migrations")
    called = {"verify": 0}

    async def _fake_verify():
        called["verify"] += 1

    monkeypatch.setattr(apply_migrations, "Command", _FakeAerichCommand)
    monkeypatch.setattr(apply_migrations, "verify_runtime_state", _fake_verify)

    await apply_migrations.run()
    assert called["verify"] == 1


@pytest.mark.asyncio
async def test_apply_migrations_skip_runtime_verify_flag(monkeypatch):
    apply_migrations = _load_module("scripts.apply_migrations")
    called = {"verify": 0}

    async def _fake_verify():
        called["verify"] += 1

    monkeypatch.setattr(apply_migrations, "Command", _FakeAerichCommand)
    monkeypatch.setattr(apply_migrations, "verify_runtime_state", _fake_verify)

    args = apply_migrations.parse_args(["--skip-runtime-verify"])
    assert args.skip_runtime_verify is True

    await apply_migrations.run(skip_runtime_verify=args.skip_runtime_verify)
    assert called["verify"] == 0
