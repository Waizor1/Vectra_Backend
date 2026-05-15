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
        present_tables: set[str] | None = None,
    ):
        self._runtime_schema_rows = runtime_schema_rows
        self._fk_rows = fk_rows
        self._retry_index_rows = retry_index_rows
        # Tables visible to the recent-migration verifier. None = "match every
        # request" so legacy tests don't have to enumerate them; existing tests
        # also disable the verifier via monkeypatch so this is mostly a
        # safety net.
        self._present_tables = present_tables

    async def fetch(self, query: str, *params):
        if "FROM pg_namespace n" in query:
            return self._runtime_schema_rows

        if "information_schema.table_constraints" in query:
            key = (params[0], params[1], params[2], params[3], params[4])
            return self._fk_rows.get(key, [])

        if "FROM pg_indexes" in query:
            return self._retry_index_rows

        if "FROM information_schema.tables" in query:
            requested = list(params[1]) if len(params) >= 2 else []
            if self._present_tables is None:
                # Default: pretend every requested table exists.
                return [{"table_name": name} for name in requested]
            return [
                {"table_name": name}
                for name in requested
                if name in self._present_tables
            ]

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


class _FakeLegacyAerichCommand(_FakeAerichCommand):
    async def init(self):
        self.init_calls += 1
        raise RuntimeError("Old format of migration file detected")


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
async def test_apply_migrations_handles_legacy_aerich_format(monkeypatch):
    apply_migrations = _load_module("scripts.apply_migrations")
    called = {"prepare_legacy": 0, "legacy_upgrade": 0, "verify": 0}

    async def _fake_prepare_legacy(command):
        assert isinstance(command, _FakeLegacyAerichCommand)
        called["prepare_legacy"] += 1

    async def _fake_legacy_upgrade(command):
        assert isinstance(command, _FakeLegacyAerichCommand)
        called["legacy_upgrade"] += 1

    async def _fake_verify():
        called["verify"] += 1

    monkeypatch.setattr(apply_migrations, "Command", _FakeLegacyAerichCommand)
    monkeypatch.setattr(
        apply_migrations,
        "_prepare_legacy_aerich_upgrade",
        _fake_prepare_legacy,
    )
    monkeypatch.setattr(
        apply_migrations,
        "_legacy_tolerant_upgrade",
        _fake_legacy_upgrade,
    )
    monkeypatch.setattr(apply_migrations, "verify_runtime_state", _fake_verify)

    await apply_migrations.run()

    assert called == {"prepare_legacy": 1, "legacy_upgrade": 1, "verify": 1}


def test_apply_migrations_recognizes_legacy_already_applied_errors():
    apply_migrations = _load_module("scripts.apply_migrations")

    assert apply_migrations._is_legacy_schema_already_applied_error(
        RuntimeError('column "renew_id" of relation "users" already exists')
    )
    assert apply_migrations._is_legacy_schema_already_applied_error(
        RuntimeError('relation "auth_identities" already exists')
    )
    assert apply_migrations._is_legacy_schema_already_applied_error(
        RuntimeError('column "price" does not exist'),
        version_file="48_20250101_add_device_discount_to_tariffs.py",
    )
    assert not apply_migrations._is_legacy_schema_already_applied_error(
        RuntimeError('column "key_activated" of relation "users" does not exist'),
        version_file="100_20260428120000_key_activation_hwid_gate.py",
    )


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


# ---------------------------------------------------------------------------
# Recent-migration table-existence guard
# ---------------------------------------------------------------------------
#
# 2026-05-15 incident root cause: `apply_migrations.py::_legacy_tolerant_upgrade`
# logged "Applied migrations: 119_..." but the SQL never executed against the
# real DB (silent no-op). The narrow runtime-schema query was satisfied by the
# pre-existing core tables, so the deploy went green. The new
# `_verify_recent_migration_tables` helper closes that gap by walking the last
# N migration files and confirming every CREATE TABLE landed.

from pathlib import Path  # noqa: E402  (kept local to the new test block)


def test_extract_table_names_handles_both_create_table_styles(tmp_path: Path):
    verifier = _load_module("scripts.verify_runtime_state")
    fake_migration = tmp_path / "120_dummy.py"
    fake_migration.write_text(
        '''
async def upgrade(db):
    return """
        CREATE TABLE "first_table" ("id" SERIAL PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS "second_table" ("id" SERIAL PRIMARY KEY);
        ALTER TABLE first_table ADD COLUMN extra INT;
    """
'''
    )
    names = verifier._extract_table_names_from_migration(fake_migration)
    assert names == ["first_table", "second_table"]


def test_extract_table_names_deduplicates_repeated_creates(tmp_path: Path):
    verifier = _load_module("scripts.verify_runtime_state")
    fake = tmp_path / "121_dummy.py"
    fake.write_text(
        'CREATE TABLE "shared" (id int);\n'
        'CREATE TABLE IF NOT EXISTS "shared" (id int);\n'
    )
    assert verifier._extract_table_names_from_migration(fake) == ["shared"]


def test_extract_table_names_empty_for_missing_or_no_create(tmp_path: Path):
    verifier = _load_module("scripts.verify_runtime_state")
    assert verifier._extract_table_names_from_migration(tmp_path / "missing.py") == []
    only_alters = tmp_path / "only_alters.py"
    only_alters.write_text("ALTER TABLE x ADD COLUMN y INT;")
    assert verifier._extract_table_names_from_migration(only_alters) == []


def test_list_recent_migration_files_orders_by_numeric_prefix(tmp_path: Path):
    verifier = _load_module("scripts.verify_runtime_state")
    for name in [
        "117_a.py",
        "118_b.py",
        "119_c.py",
        "120_d.py",
        "__init__.py",
        "not-a-migration.py",
    ]:
        (tmp_path / name).write_text("")
    paths = verifier._list_recent_migration_files(tmp_path, lookback=3)
    assert [p.name for p in paths] == ["120_d.py", "119_c.py", "118_b.py"]


def test_list_recent_migration_files_handles_missing_directory():
    verifier = _load_module("scripts.verify_runtime_state")
    paths = verifier._list_recent_migration_files(Path("/no/such/path"), lookback=5)
    assert paths == []


@pytest.mark.asyncio
async def test_verify_recent_migration_tables_returns_none_when_all_present(tmp_path: Path):
    verifier = _load_module("scripts.verify_runtime_state")
    (tmp_path / "119_create.py").write_text(
        'CREATE TABLE "alpha" (id int);\nCREATE TABLE IF NOT EXISTS "beta" (id int);'
    )

    conn = _FakeConnection(
        runtime_schema_rows=[{"schema_name": "public"}],
        fk_rows={},
        retry_index_rows=[],
        present_tables={"alpha", "beta"},
    )

    issue = await verifier._verify_recent_migration_tables(
        conn, runtime_schema="public", migrations_dir=tmp_path, lookback=5
    )
    assert issue is None


@pytest.mark.asyncio
async def test_verify_recent_migration_tables_flags_missing(tmp_path: Path):
    """Reproduces the 2026-05-15 PR #88 incident pattern: migration claims to
    create golden_period_configs but the SQL never executed."""
    verifier = _load_module("scripts.verify_runtime_state")
    (tmp_path / "119_golden_period.py").write_text(
        'CREATE TABLE IF NOT EXISTS "golden_period_configs" (id int);\n'
        'CREATE TABLE IF NOT EXISTS "golden_periods" (id int);\n'
    )

    conn = _FakeConnection(
        runtime_schema_rows=[{"schema_name": "public"}],
        fk_rows={},
        retry_index_rows=[],
        present_tables=set(),  # Neither table exists in the DB.
    )

    issue = await verifier._verify_recent_migration_tables(
        conn, runtime_schema="public", migrations_dir=tmp_path, lookback=5
    )
    assert issue is not None
    assert "golden_period_configs" in issue
    assert "golden_periods" in issue
    assert "119_golden_period.py" in issue


@pytest.mark.asyncio
async def test_verify_recent_migration_tables_flags_partial_apply(tmp_path: Path):
    verifier = _load_module("scripts.verify_runtime_state")
    (tmp_path / "119_partial.py").write_text(
        'CREATE TABLE "applied_one" (id int);\nCREATE TABLE "missing_one" (id int);\n'
    )

    conn = _FakeConnection(
        runtime_schema_rows=[{"schema_name": "public"}],
        fk_rows={},
        retry_index_rows=[],
        present_tables={"applied_one"},
    )

    issue = await verifier._verify_recent_migration_tables(
        conn, runtime_schema="public", migrations_dir=tmp_path, lookback=5
    )
    assert issue is not None
    assert "missing_one" in issue
    assert "applied_one" not in issue


@pytest.mark.asyncio
async def test_verify_recent_migration_tables_skips_when_runtime_schema_unknown(tmp_path: Path):
    verifier = _load_module("scripts.verify_runtime_state")
    (tmp_path / "119_dummy.py").write_text('CREATE TABLE "x" (id int);')

    conn = _FakeConnection(
        runtime_schema_rows=[],
        fk_rows={},
        retry_index_rows=[],
        present_tables=set(),
    )

    issue = await verifier._verify_recent_migration_tables(
        conn, runtime_schema=None, migrations_dir=tmp_path, lookback=5
    )
    assert issue is None  # No schema → can't verify; surface only the schema issue.


@pytest.mark.asyncio
async def test_verify_recent_migration_tables_flags_empty_directory(tmp_path: Path):
    verifier = _load_module("scripts.verify_runtime_state")

    conn = _FakeConnection(
        runtime_schema_rows=[{"schema_name": "public"}],
        fk_rows={},
        retry_index_rows=[],
    )

    issue = await verifier._verify_recent_migration_tables(
        conn, runtime_schema="public", migrations_dir=tmp_path, lookback=5
    )
    assert issue is not None
    assert "No migration files found" in issue


@pytest.mark.asyncio
async def test_collect_issues_includes_migration_tables_failure(tmp_path: Path, monkeypatch):
    """End-to-end: a missing migration table surfaces from _collect_issues
    (which is what verify_runtime_state actually consumes)."""
    verifier = _load_module("scripts.verify_runtime_state")
    migrations_dir = tmp_path / "models"
    migrations_dir.mkdir()
    (migrations_dir / "119_create.py").write_text('CREATE TABLE "ghost" (id int);')

    monkeypatch.setattr(verifier, "MIGRATIONS_DIR", migrations_dir)

    conn = _FakeConnection(
        runtime_schema_rows=[{"schema_name": "public"}],
        fk_rows={
            ("active_tariffs", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "x", "delete_rule": "CASCADE"}
            ],
            ("notification_marks", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "x", "delete_rule": "CASCADE"}
            ],
            ("promo_usages", "user_id", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "x", "delete_rule": "CASCADE"}
            ],
            ("users", "referred_by", "users", "id", "public"): [
                {"constraint_schema": "public", "constraint_name": "x", "delete_rule": "SET NULL"}
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
        present_tables=set(),  # ghost table is missing
    )

    issues = await verifier._collect_issues(conn)
    assert any("ghost" in i for i in issues)
