## 2026-03-06

### test(warnings): continuity closure after deprecation-warning remediation

- Scope (exact changed files in tests):
  - `tests/_sqlite_datetime_compat.py`
  - `tests/test_user_recreate_cleanup.py`
  - `tests/test_family_freeze_resume.py`
  - `tests/test_payment_idempotency_race.py`
  - `tests/test_payments_no_yookassa.py`
  - `tests/test_user_family_summary.py`
- Goal and achieved warning reduction:
  - goal: remove backend pytest deprecation-warning noise introduced by SQLite datetime parsing paths and keep suites strict-clean;
  - baseline -> current: `114 warnings` (previous full-suite baseline) -> `0 warnings observed` on full suite with default warnings mode.
- Verification commands and concise results:
  - `py -3.12 -m pytest tests/test_user_recreate_cleanup.py tests/test_family_freeze_resume.py tests/test_payment_idempotency_race.py tests/test_payments_no_yookassa.py tests/test_user_family_summary.py -q -W error::DeprecationWarning` -> `48 passed`.
  - `py -3.12 -m pytest tests -q -W default` -> `203 passed`, `0 warnings observed`.
  - `py -3.12 -m pytest tests -q -W error::DeprecationWarning` -> `203 passed`.
  - `powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "scripts/predeploy.ps1"` -> `PASS`.
- Versioning decision:
  - no bump (test-only remediation; no runtime/user-visible behavior change).
- Residual risks/notes:
  - strict-clean state depends on preserving test bootstrap compatibility helper usage in warning-sensitive suites;
  - future dependency upgrades (Python/stdlib/ORM) can reintroduce warnings and should be revalidated with both default and strict warning gates.

## 2026-03-04

### chore(version/backend): governance remediation minor bump for Directus fail-closed hotfix

- Scope: `pyproject.toml` (required policy bump) and `memory-base/log.md` continuity entry only.
- Version policy action: backend `0.27.0 -> 0.28.0` for runtime-visible backend hotfix train.
- Key invariants:
  - no migrations;
  - no runtime logic changes;
  - no unrelated file edits.
- Verification commands:
  - `py -3.12 -m pytest tests/test_directus_postgres_reliability_artifacts.py -q`
  - `py -3.12 -c "import tomllib, pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))['tool']['poetry']['version'])"`

## 2026-03-04

### fix(deploy/post-setup-gate): remove fragile heredoc self-heal shell and switch to dedicated script

- Incident from production deploy log:
  - post-super-setup verify correctly detected FK drift (`NO ACTION` on three critical FKs);
  - fallback self-heal step crashed before execution with shell parse error:
    - `sh: 45: Syntax error: end of file unexpected (expecting ")")`.
- Root cause: inline Python heredoc embedded inside grouped shell condition in workflow remote script (`if ( ... <<'PY' || ... )`) remained parser-fragile in container `sh`.
- Implemented hard fix:
  - added `scripts/self_heal_runtime_state.py` that initializes Tortoise and runs:
    - `ensure_active_tariffs_fk_cascade()`
    - `ensure_notification_marks_fk_cascade()`
    - `ensure_users_referred_by_fk_set_null()`
  - workflow post-super-setup gate now calls script directly:
    - `python scripts/self_heal_runtime_state.py || python3 scripts/self_heal_runtime_state.py`
  - removed inline heredoc Python block from workflow gate.
- Regression coverage updates:
  - updated workflow artifact assertions to require script invocation marker;
  - added script artifact test verifying all 3 FK guards and Tortoise init/close lifecycle.
- Backend version bump (runtime/deploy-visible policy):
  - `pyproject.toml` `0.25.0 -> 0.26.0`.
- Validation:
  - `py -3.12 -c "import pathlib, yaml; yaml.safe_load(pathlib.Path('.github/workflows/auto-deploy.yml').read_text(encoding='utf-8')); print('YAML OK')"` -> `YAML OK`
  - `py -3.12 -m py_compile scripts/self_heal_runtime_state.py tests/test_directus_postgres_reliability_artifacts.py` -> OK
  - `py -3.12 -m pytest tests/test_directus_postgres_reliability_artifacts.py tests/test_runtime_state_verification.py -q` -> `32 passed`

## 2026-03-04

### ci(deploy/yaml): fix invalid workflow syntax in post-setup self-heal block

- Fixed YAML parse error in `.github/workflows/auto-deploy.yml` (reported at line 784): embedded Python heredoc body had zero indentation in workflow file and broke YAML block-scalar structure.
- Restored consistent indentation for the heredoc payload and closing `PY` marker inside the remote deploy script.
- Validation:
  - `py -3.12 -c "import pathlib, yaml; yaml.safe_load(pathlib.Path('.github/workflows/auto-deploy.yml').read_text(encoding='utf-8')); print('YAML OK')"` -> `YAML OK`
  - `py -3.12 -m pytest tests/test_directus_postgres_reliability_artifacts.py -q` -> `21 passed`

## 2026-03-04

### ci(deploy/directus-fk): post-super-setup runtime gate with auto self-heal and fail-closed abort

- Incident class confirmed on VPS: `scripts/apply_migrations.py` passed, but after `scripts/directus_super_setup.py` FK rules drifted back to `NO ACTION`, causing runtime verify failures and blocking Directus user delete.
- Hardened deploy workflow in `.github/workflows/auto-deploy.yml`:
  - added blocking post-super-setup gate: `verify_runtime_state.py`;
  - on first failure, runs automatic FK self-heal in `bloobcat` container via `bloobcat.db.fk_guards`:
    - `ensure_active_tariffs_fk_cascade()`
    - `ensure_notification_marks_fk_cascade()`
    - `ensure_users_referred_by_fk_set_null()`;
  - re-runs `verify_runtime_state.py`;
  - aborts deploy (`exit 1`) if drift remains after self-heal.
- Added regression coverage in `tests/test_directus_postgres_reliability_artifacts.py`:
  - verifies new post-super-setup gate markers;
  - verifies self-heal invocation markers and re-verify order;
  - verifies fail-closed abort path exists before tariffs seed.
- Version bump per runtime-visible backend policy:
  - `pyproject.toml` `0.24.0 -> 0.25.0`.
- Verification:
  - `py -3.12 -m py_compile tests/test_directus_postgres_reliability_artifacts.py` -> OK
  - `py -3.12 -m pytest tests/test_directus_postgres_reliability_artifacts.py -q` -> `21 passed`

## 2026-03-04

### fix(directus/users-delete): fail-safe pre-delete cleanup + relation action alignment

- Incident: Directus delete for `users` regressed again with FK blocker
  `fk_active_tariffs_user` (`active_tariffs.user_id -> users.id`).
- Root cause class: Directus path deletes users directly in DB and bypasses Python `Users.delete()` guards; any relation/FK drift can re-break deletion.
- Implemented permanent resilience in two layers:
  - `directus/extensions/hooks/remnawave-sync/index.js`
    - added deterministic `normalizeUserIds(...)`;
    - added `applyDeleteSafetyCleanup(...)` executed in `filter("items.delete")` for `users` before delete;
    - cleanup now proactively removes blocker rows in `active_tariffs` and `notification_marks`, and nullifies `users.referred_by` for children (with table/column existence guards).
  - `scripts/directus_super_setup.py`
    - aligned users-related relation metadata to cascade intent: `one_deselect_action="delete"` for business O2M blocks tied to user lifecycle;
    - kept `users.referred_by` relation on `one_deselect_action="nullify"` (expected `SET NULL` behavior).
- Regression coverage:
  - `tests/test_directus_postgres_reliability_artifacts.py` extended with checks that:
    - `directus_super_setup.py` uses delete action for cascade-bound user relations;
    - `remnawave-sync` hook contains pre-delete cleanup for FK blockers.
- Versioning policy (runtime-visible backend fix): `pyproject.toml` bumped `0.23.0 -> 0.24.0`.
- Verification:
  - `py -3.12 -m py_compile scripts/directus_super_setup.py tests/test_directus_postgres_reliability_artifacts.py` -> OK
  - `node --check directus/extensions/hooks/remnawave-sync/index.js` -> OK
  - `py -3.12 -m pytest tests/test_directus_postgres_reliability_artifacts.py -q` -> `19 passed`
  - `py -3.12 -m pytest tests/test_runtime_state_verification.py -q` -> `10 passed`
- Next ops step on server:
  - run deploy so updated hook/setup/version are applied, then re-check Directus user deletion and `scripts/verify_runtime_state.py`.

## 2026-03-03

### fix(db/fk-runtime): add deterministic reconcile migration for runtime FK invariants

- Problem observed on fresh redeploy: `scripts/apply_migrations.py` completed Aerich upgrade but failed fail-closed runtime verification with FK drift:
  - `active_tariffs.user_id -> users.id` expected `ON DELETE CASCADE`, found `NO ACTION`;
  - `notification_marks.user_id -> users.id` expected `ON DELETE CASCADE`, found `NO ACTION`;
  - `users.referred_by -> users.id` expected `ON DELETE SET NULL`, found `NO ACTION`.
- Implemented permanent fix:
  - added migration `migrations/models/92_20260304011000_reconcile_runtime_fk_invariants.py`:
    - resolves target runtime schema deterministically (prefers `public`);
    - cleans orphan links for `users.referred_by` and `notification_marks.user_id`;
    - rebuilds FK constraints to canonical runtime rules:
      - `fk_active_tariffs_user` -> `ON DELETE CASCADE`
      - `fk_notification_marks_user` -> `ON DELETE CASCADE`
      - `users_referred_by_foreign` -> `ON DELETE SET NULL`
    - keeps downgrade path deterministic (`NO ACTION`) for rollback symmetry.
- Versioning policy applied (runtime-visible backend change):
  - `pyproject.toml` bumped `0.22.0 -> 0.23.0`.
- Verification commands to run on server:
  - `docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T bloobcat python scripts/apply_migrations.py`
  - `docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T bloobcat python scripts/verify_runtime_state.py`
  - `curl -fsS http://localhost:33083/health`
  - `curl -fsS http://localhost:8055/server/health`
- Known risk:
  - migration targets one effective runtime schema (expected production model); multi-schema shadow copies may still require manual cleanup if present.
- Next step:
  - trigger backend auto-deploy (`push` to `main`) so migration `92_*` is applied on VPS before next runtime-verify gate.

## 2026-03-02

### fix(runtime-verifier): parser false-negative for casted ANY/ARRAY predicate

- Fixed false-negative in runtime index predicate parser for casted `ANY/ARRAY` form.
- Touched files:
  - `scripts/verify_runtime_state.py`
  - `tests/test_runtime_state_verification.py`
- Verification commands/results:
  - `py -3.12 -m pytest tests/test_runtime_state_verification.py -q` -> `10 passed`
  - `py -3.12 -m py_compile scripts/verify_runtime_state.py tests/test_runtime_state_verification.py` -> `OK`
- Residual risk:
  - environment-coupled imports may still require env stubs in some contexts.

## 2026-03-02

### fix(payments/replay): claim-false replay hardening continuity

- Fixed strict GO remediation scope for payment replay reliability:
  - hardened claim-false replay path;
  - aligned webhook/fallback handling parity;
  - tightened lease safety around replay ownership/processing windows.
- Exact changed files (implementation wave):
  - `bloobcat/routes/payment.py`
  - `tests/test_payment_idempotency_race.py`
  - `pyproject.toml`
  - `memory-base/log.md`
- Latest verification commands/results:
  - `py -3.12 -m pytest tests/test_payment_idempotency_race.py tests/test_payments_no_yookassa.py -q` -> `23 passed`
  - `py -3.12 -m pytest tests/test_runtime_state_verification.py tests/test_remnawave_client_retry_policy.py tests/test_resilience_hardening.py tests/test_admin_integration_delete_retry.py tests/test_remnawave_activation.py -q` -> `91 passed`
  - `py -3.12 -m py_compile bloobcat/routes/payment.py bloobcat/tasks/payment_reconcile.py` -> `OK`
- Residual risks:
  - test bootstrap remains environment-coupled in some paths;
  - optional follow-up: add explicit concurrency/performance evidence artifacts.

## 2026-03-02

### chore(stabilization/backend): finalize backend stabilization wave continuity

- Consolidated finalized stabilization scope:
  - FK hardening finalized, including `users.referred_by` nullable/`SET NULL`-safe guard path and migration continuity.
  - RemnaWave A025 path stabilized with non-retry handling and classifier alignment.
  - Retry-job pipeline hardened: dedup + claim/lease safety, including migration `91_20260301143000_remnawave_retry_jobs_active_unique.py`.
  - Runtime verifier flow aligned with `apply_migrations` integration.
  - Deploy workflow finalized with explicit migration + runtime-verify gate before release completion.
- Verification summary references (this stabilization session):
  - `tests/test_admin_integration_delete_retry.py` -> `18 passed`.
  - `tests/test_remnawave_activation.py tests/test_hwid_antitwink.py tests/test_connections_process.py tests/test_resilience_hardening.py` -> `49 passed`.
  - `tests/test_auth_registration_modes.py tests/test_resilience_hardening.py tests/test_remnawave_activation.py tests/test_connections_process.py` -> `33 passed`.
  - `tests/test_directus_postgres_reliability_artifacts.py` -> `16 passed`.
- Release governance: backend version bumped from `0.19.0` to `0.20.0` (minor) per runtime-visible backend policy.

## 2026-03-01

### fix(remnawave-retry): atomic active-job dedup for delete enqueue

- Blocker addressed with DB + app safeguards against non-atomic enqueue races for RemnaWave delete retry jobs.
- Implemented scope:
  - `migrations/models/91_20260301143000_remnawave_retry_jobs_active_unique.py`
    - deduplicates existing active rows per `(job_type, user_id)` (keeps highest-priority active row, moves extras to `dead_letter`);
    - replaces partial unique index to enforce at most one active job for `status IN ('pending', 'processing')`.
  - `bloobcat/services/admin_integration.py`
    - `enqueue_remnawave_delete_retry` now checks both active statuses (`pending`/`processing`) before create;
    - updates existing active job `remnawave_uuid/last_error` when needed;
    - on `IntegrityError` race, re-reads active job and applies the same update path to avoid duplicates and preserve latest error/uuid.
  - `tests/test_admin_integration_delete_retry.py`
    - added dedup test for existing `processing` job;
    - added race fallback test (`create -> IntegrityError -> re-read active job update`).
- Verification commands/results:
  - `py -3.12 -m pytest tests/test_admin_integration_delete_retry.py -q` -> `18 passed`.
  - `py -3.12 -m compileall bloobcat/services/admin_integration.py migrations/models/91_20260301143000_remnawave_retry_jobs_active_unique.py` -> OK.
- Residual risks:
  - Migration dedup policy keeps one active row per key by priority/order; historical duplicate rows are moved to `dead_letter` (non-destructive), which may slightly alter retry chronology for already-duplicated data.

### fix(db/directus-delete): notification_marks FK schema-safe hardening continuity

- Root cause: удаление пользователя через Directus падало на FK `fk_notification_marks_user` (delete rule drift/non-cascade в целевой схеме).
- Implemented scope (runtime-visible backend fix):
  - `migrations/models/89_20260301120000_harden_notification_marks_fk_schema_safe.py` — schema-safe repair FK `notification_marks.user_id -> users.id` до `ON DELETE CASCADE`, с дедупликацией конфликтующих constraint-ов.
  - `directus/extensions/endpoints/server-ops/index.js` — добавлены/усилены ops-команды для диагностики и ремонта FK, cleanup orphan-данных, и generic error response без утечки SQL/internal details.
- Verification commands/results:
  - `py -3.12 -c "import tomllib, pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))['tool']['poetry']['version'])"` -> `0.19.0`.
  - sanity-read обновлённого `memory-base/log.md` и root `memory-base/log.md` -> формат секций/буллетов сохранён.
- Residual risks/notes:
  - SQL-ветки в ops/migration опираются на single-schema assumption (рабочая схема Postgres должна совпадать с ожидаемой deploy-конфигурацией).
  - Есть узкое concurrency window между orphan-cleanup и повторной FK-проверкой при внешних конкурентных delete/write операциях.
  - Deploy gate remains fail-closed: при ошибках DB repair/ops проверок релиз не должен считаться безопасно завершённым.

### docs(deploy): lock non-destructive baseline after successful deploy

- Confirmed successful backend deploy run: `22546902029`.
- Operational baseline fixed to `PRERELEASE_FULL_REINSTALL=false`.
- Default policy: no reset/reinstall by default in routine deploy flow.
- Prerelease full reinstall remains incident-driven opt-in only (explicit temporary enablement, then revert to baseline).

### docs(deploy/security): review-finding remediation final state sync

- Убрана инструкция с выводом plaintext-значения `POSTGRES_PASSWORD` из `.env`; для проверки оставлен только non-disclosing presence-check.
- Финальное состояние fix-пакета зафиксировано как: backend version `0.18.0`, targeted test result `16 passed`.

### fix(deploy/db-fk): deterministic FK SQL payload + fail-closed DB guard

- Root cause: FK repair step using `DO $$ ... $$` was vulnerable to escaping/interpolation corruption in deploy shell context, which could break SQL execution in the FK fix stage.
- Implemented solution: switched to deterministic FK repair payload with explicit dollar-quote tag `$tvpn_fk$` to avoid accidental mutation of SQL body during workflow execution.
- Fail-open remediation: missing `bloobcat_db` in deploy context is now treated as blocking (`fail-closed`) instead of continuing with a partial/unsafe path.
- Verification commands/results:
  - `python -m pytest tests/test_directus_postgres_reliability_artifacts.py -q` -> `16 passed`.

### chore(version/backend): bump for prerelease deploy-flow runtime changes

- Bumped backend version in `TVPN_BACK_END/pyproject.toml` from `0.17.0` to `0.18.0` because current changes alter runtime deploy behavior/workflow scripts for prerelease operations.

### docs/workflow(deploy): align prerelease toggle strict semantics

- Aligned prerelease toggle behavior between workflow concurrency cancellation and runtime gate.
- `PRERELEASE_FULL_REINSTALL` now enables prerelease mode only when value is exact literal `true`; legacy truthy aliases (`1/yes/on`) and other variants are no longer accepted.
- Updated deploy troubleshooting docs to remove truthy alias claims and match strict toggle semantics.

### docs(deploy): prerelease full reinstall continuity notes

- Reason: documented temporary prerelease full reinstall mode as incident-only opt-in (not routine validation usage), with mandatory rollback to baseline immediately after incident handling.
- Key files changed:
  - `TVPN_BACK_END/memory-base/deploy-troubleshooting.md`
  - `TVPN_BACK_END/memory-base/log.md`
- Verification commands used:
  - `python -c "import re, pathlib, sys; p=pathlib.Path('TVPN_BACK_END/.github/workflows/auto-deploy.yml'); txt=p.read_text(encoding='utf-8'); pats=[r'PRERELEASE_FULL_REINSTALL', r'PRERELEASE FULL REINSTALL MODE', r'Running prerelease full reinstall', r'down -v --remove-orphans']; [sys.stdout.buffer.write((f'{i+1}:{line}\n').encode('utf-8','backslashreplace')) for pat in pats for i,line in enumerate(txt.splitlines()) if re.search(pat,line)]"`
  - `rg` unavailable in current shell environment; workflow conditions/markers were validated via direct file review (`.github/workflows/auto-deploy.yml`) and pattern scan above.

### chore(version/backend): minor bump for governance gate

- Bumped backend version in `TVPN_BACK_END/pyproject.toml` from `0.15.0` to `0.16.0` to satisfy runtime/deploy-visible change policy.
- Removed unintended workspace artifact `TVPN_BACK_END/NUL`.

### test(deploy/db): targeted reliability artifact verification + regression tests

- Objective: final static/command-level verification for Directus/Postgres reliability fix package.
- Added regression tests: `tests/test_directus_postgres_reliability_artifacts.py` covering:
  - required `POSTGRES_PASSWORD` authority in `docker-compose.yml`;
  - `.env.example` non-interpolated `SCRIPT_DB` and authority declaration;
  - safety guards/invariants in `scripts/db/reconcile_postgres_auth.sh` and `scripts/db/destructive_reset_once.sh`;
  - `auto-deploy.yml` reconcile-first order and evidence-gated destructive fallback sequence;
  - docs sanity for `memory-base/deploy-troubleshooting.md`.
- Verification commands/results:
  - `sh -n scripts/db/reconcile_postgres_auth.sh && sh -n scripts/db/destructive_reset_once.sh` -> OK
  - `POSTGRES_PASSWORD= docker compose -f docker-compose.yml config` -> expected fail with required-var marker
  - `POSTGRES_PASSWORD=verifier docker compose -f docker-compose.yml config` -> OK
  - `sh scripts/db/reconcile_postgres_auth.sh` (without env) -> expected guard fail (exit 2)
  - `COMPOSE_PROJECT_NAME=tvpn_test sh scripts/db/destructive_reset_once.sh` -> expected opt-in guard fail (exit 10)
  - `DRY_RUN=true sh scripts/db/destructive_reset_once.sh` -> expected project-scope guard fail (exit 10)
  - `DRY_RUN=true COMPOSE_PROJECT_NAME=tvpn_test COMPOSE_FILES=missing.yml sh scripts/db/destructive_reset_once.sh` -> expected compose-file guard fail (exit 12)
  - `python -m pytest tests/test_directus_postgres_reliability_artifacts.py -q` -> 6 passed
- Tooling availability notes:
  - `shellcheck` not installed in current environment (static shell lint skipped).
  - `yamllint` not installed (workflow YAML validated via Python `yaml.safe_load` + regression assertions).

### fix(subscription-overlay): rollback-safe family freeze resume on mutation failure

- Scope: targeted fix in `bloobcat/services/subscription_overlay.py` and regression coverage in `tests/test_family_freeze_resume.py`.
- Problem: `resume_frozen_base_if_due()` caught exceptions **inside** `in_transaction()`, allowing transaction commit with partial state changes (e.g., old active tariff deleted before restore create failure).
- Implementation:
  - moved exception handling outside transaction boundary so mutation failures bubble out of transaction context and trigger DB rollback;
  - preserved bool contract (`True` on success, `False` for operational failures);
  - after rollback, persisted diagnostics in a separate DB write (`last_resume_error`) and incremented `resume_attempt_count` once per failed attempt.
- Added regression test `test_resume_failure_rolls_back_partial_mutations`:
  - monkeypatches `ActiveTariffs.create` to raise after old tariff delete point;
  - asserts function returns `False`;
  - verifies old active tariff remains, user linkage/state is unchanged, freeze remains active/not applied, and error diagnostics are stored.
- Verification:
  - `py -3.12 -m pytest tests/test_family_freeze_resume.py -q` -> 2 passed
  - `py -3.12 -m pytest tests -q` -> 98 passed
  - `py -3.12 -m compileall bloobcat` -> OK

## 2026-02-20

### fix(deploy/ci/directus-user-delete): CI hardening + forced FK CASCADE repair in deploy

- **Симптом:** после фиксов в коде удаление пользователя в Directus всё равно падало с
  `violates foreign key constraint "fk_active_tariffs_user"`.
- **Что сделано (infra/deploy):**
  - `.github/workflows/auto-deploy.yml`:
    - тестовый этап переведен на стабильный импорт-контекст:
      - `pip install pytest pytest-asyncio`
      - `pip install -e . --no-deps`
      - `python -m pytest tests -q`
    - перед тестами добавлен `cp .env.example .env` (иначе падал импорт `bloobcat.settings` из-за обязательных env).
    - добавлен debug-шаг для CI (cwd/sys.path/пути модулей) на период стабилизации.
    - в deploy-шаг добавлен **принудительный SQL repair** FK
      `active_tariffs.user_id -> users.id` с `ON DELETE CASCADE` через `psql` в `bloobcat_db`.
    - после добавления SQL-блока исправлена YAML-ошибка workflow (убран heredoc, заменено на `psql -c ...`).
- **Результат:** пайплайн проходит, FK приводится к `CASCADE` в целевой БД, удаление пользователей в Directus снова работает.
- **Операционный note:** если регресс повторится, сначала проверить deploy-лог шага
  `Fixing FK active_tariffs.user_id -> users.id (ON DELETE CASCADE)` и `delete_rule` в выводе SQL-проверки.

### DB-005: FK `active_tariffs -> users` — финальный хардening + миграция

**Симптом (воспроизведен локально):**
- Удаление пользователя через Directus падало с `INTERNAL_SERVER_ERROR`.
- Error log: `violates foreign key constraint "fk_active_tariffs_user" Key (user_id)=(...) is still referenced from table "active_tariffs"`.

**Root Cause Analysis (RCA):**
1. FK `active_tariffs.user_id -> users.id` был создан с `NO ACTION` вместо `CASCADE`.
2. Runtime guard в `bloobcat/__main__.py` проверял FK только по имени constraint, могли быть дублеты или иные имена в разных окружениях.
3. Directus-удаление идет напрямую на уровне БД, а не через Python `Users.delete()`, поэтому не срабатывал runtime fail-safe.
4. При drift схемы FK мог оставаться не-CASCADE, и cascade-удаления блокировались.

**Что изменено:**

1. **Новый модуль `bloobcat/db/fk_guards.py`** (schema-aware, constraint-agnostic):
   - `ensure_active_tariffs_fk_cascade()`: проверяет FK по структуре (`active_tariffs.user_id -> users.id`), не по имени.
   - Использует `information_schema.table_constraints` и `key_column_usage` для поиска FK.
   - При обнаружении не-CASCADE constraint → автоматически пересоздает с `ON DELETE CASCADE`.
   - Отдельный guard для `notification_marks.user_id -> users.id` (`ensure_notification_marks_fk_cascade()`).
   - Оба guard'а возвращают `bool` (True = выполнен repair, False = early return).

2. **`bloobcat/__main__.py`** (стартовый hardening):
   - Вызов `ensure_active_tariffs_fk_cascade()` и `ensure_notification_marks_fk_cascade()` в `lifespan` перед запуском приложения.
   - Инициализация временного Tortoise connection если нужно.
   - Лог "FK self-heal guard выполнен" при успешном выполнении обоих guard'ов.

3. **Миграция `84_20260220203000_harden_active_tariffs_fk_schema_safe.py`**:
   - Schema-safe repair на уровне БД (SQL).
   - Удаление дублеты FK (если есть) и создание единого CASCADE constraint.

4. **`docker-compose.yml`** (порядок старта):
   - Добавлен `healthcheck` для `bloobcat_db`.
   - `depends_on: condition: service_healthy` для `bloobcat`, `directus` → гарантирует БД готова перед запуском.

5. **`directus/extensions/endpoints/server-ops/index.js`** (ops-диагностика):
   - Команда `fk_active_tariffs` теперь:
     - Показывает все существующие FK на `active_tariffs.user_id`.
     - Выводит `delete_rule` (ACTION/CASCADE/SET NULL).
     - Использует schema-safe SQL (join через `constraint_column_usage`).

**Тесты:** `tests/test_resilience_hardening.py`
- `test_ensure_active_tariffs_fk_cascade_repairs_non_cascade`: проверяет repair при NO ACTION.
- `test_ensure_active_tariffs_fk_cascade_repairs_missing_constraint`: миграция отсутствующего FK.
- `test_ensure_active_tariffs_fk_cascade_skips_when_single_cascade`: skip если уже CASCADE.
- `test_ensure_notification_marks_fk_cascade_*`: аналогичные тесты для notification_marks.
- `test_users_delete_calls_fk_guard()`: проверяет вызов guard при `Users.delete()`.

**Проверено:**
- `py -3.12 -m pytest tests/test_resilience_hardening.py -q` → **11 passed**.
- Локальное воспроизведение: удаление user → **успешно** (cascade срабатывает).
- `ReadLints` по измененным файлам → без ошибок.

**Residual risks:**
- Если в окружении setup.py не запустился → FK остается в старом состоянии. Решение: миграция + runtime guard гарантируют repair при первом запуске.
- Advisory lock убран (нестабилен), но в-process lock + идемпотентность хватает для high-concurrency.

---

- **fix(db/db-003-fk-guard-startup-order):** минимальный hardening старта для гарантированного self-heal `active_tariffs.user_id -> users.id`.
  - **RCA-контекст:** после `Aerich upgrade` соединение Tortoise могло быть закрыто, из-за чего FK guard выполнялся без активного connection.
  - **Исправление:**
    - `bloobcat/__main__.py`: перед FK guard добавлена проверка/инициализация временного `Tortoise` connection (`Tortoise.init(config=TORTOISE_ORM)` при отсутствии `default`), после guard — аккуратное `Tortoise.close_connections()` только для временно созданного подключения;
    - добавлен точечный стартовый лог `FK self-heal guard выполнен` для операционной видимости факта выполнения guard;
    - `docker-compose.yml`: добавлен `healthcheck` для `bloobcat_db` и `depends_on: condition: service_healthy` для `bloobcat`, `pgadmin`, `directus`, чтобы снизить флаки порядка старта.

- **fix(db/active-tariffs-fk-hardening):** закрыт повторяющийся падеж удаления `users` по FK `fk_active_tariffs_user`.
  - **Симптом:** удаление пользователя через Directus/GraphQL продолжало падать `violates foreign key constraint "fk_active_tariffs_user"` даже при наличии runtime-guard в `Users.delete()`.
  - **RCA:** удаление в Directus идет напрямую на уровне БД, а не через Python `Users.delete()`; при drift схемы в target schema FK на `active_tariffs.user_id` мог остаться не-CASCADE/дублированным.
  - **Исправление:**
    - `bloobcat/db/fk_guards.py`: `ensure_active_tariffs_fk_cascade()` усилен до schema-aware проверки/ремонта (не завязан на `current_schema()`, чинит все FK для `active_tariffs.user_id` в целевой схеме, оставляет единый CASCADE);
    - добавлена миграция `migrations/models/84_20260220203000_harden_active_tariffs_fk_schema_safe.py` с таким же schema-safe repair на уровне БД;
    - `directus/extensions/endpoints/server-ops/index.js`: команды `fk_active_tariffs` и `fix_fk_active_tariffs` синхронизированы с schema-safe логикой и корректным `pg_constraint` join по схеме/таблице.
  - **Тесты:** `tests/test_resilience_hardening.py` расширен кейсами mixed-rules и single-cascade для `ensure_active_tariffs_fk_cascade()`.

- **fix(db/users-delete-fk-guard):** устранен повторяющийся `INTERNAL_SERVER_ERROR` при удалении пользователя из-за дрейфа FK `active_tariffs -> users`.
  - **Симптом:** удаление `users` падало с `violates foreign key constraint "fk_active_tariffs_user"` (в `active_tariffs` было `NO ACTION/RESTRICT` вместо `CASCADE`).
  - **RCA:** проверка self-heal в `bloobcat/__main__.py` была завязана на фиксированное имя constraint (`fk_active_tariffs_user`) и могла пропустить drift при другом имени FK.
  - **Исправление:**
    - добавлен модуль `bloobcat/db/fk_guards.py` с устойчивой проверкой FK по структуре связи (`active_tariffs.user_id -> users.id`), а не по имени;
    - `bloobcat/__main__.py` переведен на импорт guard-функций из `fk_guards`;
    - в `bloobcat/db/users.py::Users.delete()` добавлен runtime fail-safe `await ensure_active_tariffs_fk_cascade()` перед `super().delete()`.
  - **Ops-диагностика:** `directus/extensions/endpoints/server-ops/index.js` команда `fk_active_tariffs` теперь также проверяет FK по таблице/колонке и показывает фактические constraints + `delete_rule`.
  - Дополнительно исправлен SQL в `fk_users_overview` (PostgreSQL-совместимый join через `constraint_column_usage`), чтобы команда не падала при диагностике.
  - **Тесты:** в `tests/test_resilience_hardening.py` добавлены регрессии:
    - self-heal применяет repair при `NO ACTION`;
    - `Users.delete()` вызывает FK guard перед удалением.
  - Прогон: `py -3.12 -m pytest tests/test_resilience_hardening.py -q` -> `11 passed`.

- **feat(deploy/tariffs):** автосидирование тарифов добавлено в backend auto-deploy.
  - Добавлен новый идемпотентный скрипт `scripts/seed_tariffs.py`:
    - source-of-truth по дефолтным тарифам теперь хранится в `TARIFFS` (редактирование/добавление позиций в одном месте);
    - режим изменен на `insert-only` по логическому ключу `name + months` (без `TRUNCATE`);
    - существующие тарифы не обновляются и не удаляются при деплое (ручные правки в админке не затираются).
  - Обновлён workflow `TVPN_BACK_END/.github/workflows/auto-deploy.yml`:
    - после шага `directus_super_setup` запускается `python scripts/seed_tariffs.py` внутри контейнера `bloobcat`;
    - добавлен retry (`3` попытки, пауза `5s`);
    - при неуспехе seed деплой завершается с ошибкой (`exit 1`), чтобы не выкатывать backend без актуальных тарифов.
  - Операционный эффект: для обновления дефолтных тарифов достаточно править `scripts/seed_tariffs.py`; при каждом деплое изменения автоматически применяются.

- **fix(remnawave/checkers):** устранена регрессия чекеров активации, подключений и HWID — восстановлена синхронизация с Directus и отправка в лог-канал.
  - **Симптом:** после миграции с FastAdmin на Directus перестали работать чекеры активации устройств, количества подключений и одинаковых HWID; значения не обновлялись в Directus (админ-панель), уведомления не приходили в лог-чат.
  - **Корневая причина (баг `continue`):**
    - В `bloobcat/routes/remnawave/catcher.py` на строке ~392 при неудачном парсинге `onlineAt` через `_safe_parse_online_at()` выполнялся `continue`, который пропускал **ВСЮ** оставшуюся обработку пользователя, включая:
      - синхронизацию `expired_at` (БД → RemnaWave),
      - синхронизацию `hwid_limit` (двустороннюю),
      - отправку обновлений в RemnaWave.
    - В эталонном бэкенде при ошибке парсинга исключение ловилось outer try/except, и пропускался только этот пользователь, но expired_at/hwid_limit sync НЕ пропускался.
  - **Исправление:**
    - Убран `continue` при неудачном парсинге `onlineAt`. Теперь если `onlineAt` невалиден, пропускается только activation/connection блок, а expired_at/hwid_limit sync продолжается.
    - Вынесены `registration_changed`, `connection_changed`, `sanction_changed` флаги перед блоком `if online_at:`.
    - Блок батчевого обновления (bulk_update) и перепланирования задач вынесен после `if online_at:`, чтобы работать для всех вариантов.
    - Activation skip логирование переведено с `INFO` на `DEBUG` для уменьшения шума в логах.
  - **Добавлена диагностика:**
    - Новый endpoint `GET /remnawave/status` — возвращает статус последнего запуска `remnawave_updater` (время, summary, ошибки).
    - `_last_run_status` глобальный dict с метриками: `last_run_at`, `last_success_at`, `last_error`, `total_runs`, `total_errors`, `last_summary`.
    - `Checker summary` теперь логируется как структурированный dict с полями: `total_users_db`, `users_with_uuid`, `remnawave_users_fetched`, `onlineAt_available`, `activation_candidates`, `activation_notify_sent`, `duplicate_hwid_detected/blocked`, `not_found_in_remnawave`, `elapsed_seconds`, `errors`.
    - `send_admin_message()` теперь ведёт счётчик `_admin_msg_stats` (sent/failed/last_error), доступный через `/remnawave/status` → `admin_notifications`.
    - В warning при невалидном `onlineAt` добавлен тип значения для диагностики формата.
  - **Файлы:**
    - `bloobcat/routes/remnawave/catcher.py` — основной фикс + диагностика.
    - `bloobcat/bot/notifications/admin.py` — счётчик ошибок отправки.
  - **Тесты:** `pytest tests/test_remnawave_activation.py tests/test_hwid_antitwink.py tests/test_connections_process.py tests/test_resilience_hardening.py` → 49 passed.

- **fix(auth/referral-fk):** устранен крэш регистрации из-за `users.referred_by = 0` при FK-ограничении.
  - **Симптом в прод-логах:** `insert or update on table "users" violates foreign key constraint "users_referred_by_foreign"` с `Key (referred_by)=(0) is not present`.
  - Причина: в средах с self-FK на `users.referred_by -> users.id` значение `0` невалидно (должно быть `NULL`, если реферера нет).
  - `bloobcat/db/users.py`:
    - поле `referred_by` переведено в nullable (`null=True`, `default=None`);
    - в `Users.get_user(...).update_or_create(defaults=...)` для новых пользователей явно выставляется `referred_by=None`;
    - проверка возможности реф-привязки переведена с sentinel `0` на отсутствие значения (`not user.referred_by`).
  - `bloobcat/routes/auth.py`:
    - базовый `referred_by` в `/auth/telegram` теперь `None` (вместо `0`), чтобы не прокидывать невалидный sentinel.
  - Миграция: `migrations/models/83_20260220173000_fix_users_referred_by_nullable_fk.py`:
    - `DROP DEFAULT` для `users.referred_by`,
    - backfill `0 -> NULL`,
    - `DROP NOT NULL`.
  - Эффект: создание нового пользователя не падает на FK при отсутствии реферера.

- **fix(auth/registration-resilience):** снижена вероятность `Internal Server Error` на welcome-регистрации (`POST /auth/telegram`).
  - `bloobcat/db/users.py`:
    - `count_referrals()` переведен в non-blocking режим (ошибка логируется, но не валит регистрацию);
    - `schedule_user_tasks(user)` для нового пользователя обёрнут в `try/except` (ошибка планировщика не прерывает выдачу токена).
  - `bloobcat/routes/auth.py`:
    - основной путь `/auth/telegram` обёрнут защитным `try/except` с traceback-логированием;
    - если `Users.get_user(...)` неожиданно вернул `None`, endpoint больше не падает `500`, а возвращает `requires_registration=true`;
    - неожиданные ошибки теперь возвращают `503 Service temporarily unavailable` вместо необработанного `500`.
  - Цель: успешная регистрация пользователя даже при временной деградации side-effect логики (scheduler/referrals/внешние зависимости).

## 2026-02-20

- **fix(auth/registration-policy):** внедрен безопасный режим отложенной регистрации пользователя.
  - `bloobcat/routes/auth.py`:
    - добавлен `registerIntent` в `POST /auth/telegram`;
    - добавлен флаг ответа `requires_registration`;
    - без `registerIntent` и без `start_param` пользователь **не создается** (возвращается `requires_registration=true`);
    - при `start_param` (family/ref/qr) авто-регистрация сохранена.
  - `bloobcat/funcs/validate.py`:
    - отключено неявное создание пользователя на обычной валидации без `start_param`;
    - для отсутствующего пользователя теперь `403 User not registered`.
  - `bloobcat/bot/routes/start.py`:
    - убрано создание пользователя через `Users.get_user()` на `/start`;
    - админ-клавиатура ставится только для уже существующих админов (`get_or_none`).

- **fix(remnawave/activation-sync):** восстановлен сбор факта подключений и триггер активаций.
  - `bloobcat/routes/remnawave/catcher.py`:
    - добавлен helper `_extract_online_at()` с fallback: `onlineAt -> userTraffic.onlineAt -> userTraffic.firstConnectedAt`;
    - добавлен точечный `get_user_by_uuid` fallback для незарегистрированных пользователей без `connected_at`;
    - добавлено диагностическое `INFO`-логирование формата ответа RemnaWave (`has_userTraffic`, `sample_onlineAt`).
  - Эффект: снова заполняются `connected_at`/`connections`, срабатывает путь `is_registered` и `on_activated_key`.

- **fix(db/connections):** защита от дублей в `connections`.
  - `bloobcat/db/connections.py`:
    - добавлен `Meta.unique_together = (("user_id", "at"),)`;
    - `Connections.process()` теперь устойчив к race (`IntegrityError` -> безопасный `get`).
  - Новая миграция: `migrations/models/82_20260220150000_connections_unique_user_at.py`:
    - чистит исторические дубли;
    - добавляет DB-constraint `UNIQUE (user_id, at)`.

- **tests:** добавлены/обновлены регрессионные проверки.
  - Новый файл: `tests/test_auth_registration_modes.py` (режимы регистрации `/auth/telegram`).
  - Новый файл: `tests/test_connections_process.py` (обработка `IntegrityError` в `Connections.process`).
  - Обновлены:
    - `tests/test_resilience_hardening.py` (кейс `User not registered` + корректный tuple-return для `Users.get_user`);
    - `tests/test_remnawave_activation.py` (fallback `firstConnectedAt` через `_extract_online_at`).
  - Прогоны:
    - `py -3.12 -m pytest tests/test_auth_registration_modes.py tests/test_resilience_hardening.py tests/test_remnawave_activation.py tests/test_connections_process.py -q` -> `33 passed`;
    - `pnpm exec tsc --noEmit` (TelegramVPN) -> успешно.

## 2026-02-15

- **fix(remnawave/updater):** восстановлена наблюдаемость ошибок воркера активации и anti-bot.
  - `bloobcat/tasks/remnawave_updater.py`: удалено глотание ошибок в scheduler, добавлен `logger.exception(...)`.
  - `bloobcat/routes/remnawave/catcher.py`: расширено логирование traceback в критичных ветках updater; webhook на `/remnawave/webhook` переведен на фоновой запуск обновления с lock-защитой от параллельного выполнения.

- **fix(hwid/checker):** унифицирован парсинг устройств RemnaWave и усилена анти-твинк логика.
  - `bloobcat/routes/remnawave/hwid_utils.py`:
    - единый парсер `parse_remnawave_devices(...)` для форматов `list`, `response`, `response.devices`, `response.data`;
    - унифицированное извлечение HWID (`hwid | deviceId | id`);
    - helper `has_duplicate_hwid(...)` для проверки дубликатов между user UUID.
  - `bloobcat/routes/remnawave/catcher.py`: переход на общие HWID-утилиты + race-safe fallback при `IntegrityError` в `HwidDeviceLocal.get_or_create(...)`.
  - добавлен `bloobcat/routes/remnawave/activation_logic.py` с изолированным правилом `should_trigger_registration(...)`.

- **feat(in-app-notifications):** реализована backend-система динамических уведомлений.
  - Новые модели:
    - `InAppNotification` (title/body/start/end/limits/auto-hide/is_active);
    - `NotificationView` (user_id, notification_id, session_id, viewed_at).
  - Новые API:
    - `GET /notifications/active` — фильтр по периоду и лимитам per-user/per-session;
    - `POST /notifications/{id}/view` — фиксация показа (idempotent при duplicate).
  - Интеграция:
    - `bloobcat/routes/notifications.py`,
    - подключение в `bloobcat/routes/__init__.py`,
    - регистрация моделей в `bloobcat/clients.py`.
  - Миграции:
    - `79_20260219120000_in_app_notifications.py`,
    - `80_20260220120000_notification_views_unique.py` (UNIQUE `(user_id, notification_id, session_id)`).

- **feat(admin/notifications):** добавлен FastAdmin CRUD для `InAppNotification`.
  - Валидации:
    - `end_at >= start_at` (если обе даты заданы),
    - `max_per_user/max_per_session >= 1` или `null`,
    - `auto_hide_seconds >= 1` или `null`.

- **test(remnawave/hwid):** добавлены регресс-тесты:
  - `tests/test_remnawave_activation.py`,
  - `tests/test_hwid_antitwink.py`.

- Проверки:
  - `python -m py_compile` по измененным backend-файлам — успешно;
  - `ReadLints` — ошибок нет;
  - `pytest` локально блокируется из-за отсутствующей зависимости `yookassa` в окружении (ModuleNotFoundError при collection).

- **fix(admin/family-section-workspace):** добавлен fallback-workspace в секцию `Семья` карточки `users` Directus.
  - В `scripts/directus_super_setup.py` добавлена фаза `ensure_users_family_workspace_aliases`.
  - Создаются/обновляются два alias-поля, которые гарантированно видны между `Семья` и `Техника`:
    - `family_workspace_notice` (`presentation-notice`) с пояснением и fallback-логикой;
    - `family_workspace_links` (`presentation-links`) с быстрыми переходами в `family_members`, `family_invites`, `family_audit_logs` по текущему `{{id}}`.
  - Это дает рабочий интерфейс даже в инстансах, где relation list-o2m может не рендериться в item-form.
  - Применено на проде: `DIRECTUS_URL=https://admin.waiz-store.ru python scripts/directus_super_setup.py` — успешно.
  - Проверка: `python -m py_compile scripts/directus_super_setup.py` + `ReadLints` — OK.

- **fix(admin/family-self-heal):** добавлен защитный self-heal секции `Семья` в карточке `users` Directus.
  - `scripts/directus_super_setup.py`: добавлена фаза `ensure_users_family_section_ux`, которая повторно и идемпотентно:
    - восстанавливает `one_field` для family relations через `/relations/...`;
    - гарантирует наличие alias-полей `family_*` с `interface=list-o2m`;
    - форсирует `hidden=false`, `width=full`, корректный `sort`, `group=None`.
  - Добавлена верификация `verify_users_family_section_visibility` в финальные проверки setup.
  - Цель: исключить повторный регресс вида «есть divider `Семья`, но блоки внутри не рендерятся».
  - Валидация: `python -m py_compile scripts/directus_super_setup.py` — успешно, `ReadLints` — без ошибок.

- **fix(db/family-fk-delete):** устранен `INTERNAL_SERVER_ERROR` при удалении пользователей из админки (`family_members_member_id_fkey`).
  - Добавлена миграция `migrations/models/77_20260215035500_fix_family_fk_on_delete.py`.
  - Все FK family-таблиц к `users` принудительно приведены к `ON DELETE CASCADE`:
    - `family_members.member_id`, `family_members.owner_id`
    - `family_invites.owner_id`
    - `family_devices.user_id`
    - `family_audit_logs.actor_id`, `family_audit_logs.owner_id`
  - Проверено на локальном Docker после `apply_migrations`:
    - до фикса часть FK была `NO ACTION`;
    - после фикса все перечисленные FK = `CASCADE`.

- **fix(admin/family-relations-runtime):** выявлена и устранена реальная причина пустой секции `Семья` в Directus.
  - Диагностика в локальном Docker показала:
    - `POST /items/directus_relations` возвращал `403 FORBIDDEN`;
    - relation rows для `family_members.owner_id/member_id` и `family_invites.owner_id` не получали `one_field`;
    - из-за этого блоки в карточке `users` не были связаны с данными.
  - Исправление в `scripts/directus_super_setup.py`:
    - `ensure_users_relations_ux.ensure_relation(...)` переведен с `/items/directus_relations` на официальный endpoint `/relations` (`GET/PATCH/POST /relations...`).
    - Для bootstrap-прав admin-policy расширен системный набор (`directus_collections` + fallback на `get_primary_policy_id_for_role(...)`).
  - Проверка после прогона setup:
    - `GET /relations/family_members/owner_id` -> `one_field=family_members_owner_list`;
    - `GET /relations/family_members/member_id` -> `one_field=family_members_member_list`;
    - `GET /relations/family_invites/owner_id` -> `one_field=family_invites_list`.
  - Локальный стенд: `docker compose up -d --build`, `python scripts/directus_super_setup.py` — успешно.

- **fix(admin/family-empty-section):** устранена причина пустого блока `Семья` для не-Administrator ролей в Directus.
  - `scripts/directus_super_setup.py`: добавлены family-коллекции в `apply_collection_ux` (видимы в навигации): `family_members`, `family_invites`, `family_devices`, `family_audit_logs`.
  - В `ensure_permissions_baseline` расширены права:
    - `manager_rw` и `admin_rw` теперь включают `family_members`, `family_invites`, `family_devices`, `family_audit_logs`;
    - `viewer_ro` включает read на те же family-коллекции.
  - Это убирает ситуацию, когда раздел в карточке пользователя отрисован, но relation-поля пустые/недоступные из-за отсутствия read/update прав на связанные коллекции.
  - Валидация: `python -m py_compile scripts/directus_super_setup.py` — успешно.

- **fix(admin/family-visibility):** устранен риск "пустой" секции `Семья` в карточке `users` в Directus.
  - `scripts/directus_super_setup.py`: в `ensure_alias_o2m_field(...)` добавлен явный `meta.hidden = False` для alias o2m-полей (family/referred/relations), чтобы Directus не прятал их по умолчанию.
  - Порядок фаз в `main()` скорректирован: `ensure_users_relations_ux` теперь выполняется **до** `apply_users_form_ux`, чтобы form-UX/width/sort/visibility применялись уже к реально существующим alias-полям.
  - Валидация: `python -m py_compile scripts/directus_super_setup.py` — успешно.

- **fix(admin/family):** расширена карточка пользователя в Directus для семейного контекста.
  - `scripts/directus_super_setup.py`: в `users` добавлены relation-блоки `family_members_owner_list`, `family_members_member_list`, `family_invites_list`.
  - В форму `users` добавлены размеры/сортировка для новых полей и отдельный divider `ui_divider_family` (секция "Семья").
  - В `ensure_users_relations_ux` добавлены o2m связи:
    - `family_members.owner_id -> users.family_members_owner_list`,
    - `family_members.member_id -> users.family_members_member_list`,
    - `family_invites.owner_id -> users.family_invites_list`.
  - В `apply_users_luxury_ux` добавлены шаблоны отображения (owner/member/status/allocated devices/usage).
  - Валидация: `python -m py_compile scripts/directus_super_setup.py` — успешно.

- **fix(family):** корректный расчет остатка устройств у главы семьи:
  - `bloobcat/routes/user.py`: `/user` для owner теперь возвращает `devices_limit` как остаток `база - активные выделения участникам`.
- **fix(family):** синхронизация реального лимита главы в RemnaWave при изменении состава/квоты семьи:
  - `bloobcat/routes/family_invites.py`: после `accept` (new/reactivate), `patch member`, `delete member`, `member leave` выполняется `_sync_owner_effective_remnawave_limit(...)`.
- **fix(family-legacy):** `bloobcat/routes/family.py` (`POST /subscription/family`) теперь учитывает аллокации участникам через `FamilyMembers` и ограничивает создание устройств по реальному остатку.
- Валидация: `python -m py_compile` по 3 файлам — успешно.
## 2026-02-15

- **fix(family):** реактивация участника по новому инвайту после статуса `disabled`:
  - файл: `bloobcat/routes/family_invites.py`, endpoint `POST /family/invites/{token}/accept`;
  - если member-запись уже существует и `status=disabled`/`allocated_devices=0`, теперь запись реактивируется (`status=active`, новый `allocated_devices`) и синхронизируется `hwid_limit` в RemnaWave.
- **fix(family):** чистка "мусора" в выдаче:
  - `GET /family/members` возвращает только активных участников (`status=active`, `allocated_devices>0`);
  - `GET /family/invites` возвращает только актуальные инвайты (не revoked, не expired, не exhausted).
- **fix(family):** family-контекст для участника считается только при активном членстве:
  - `bloobcat/routes/user.py` (`/user`) и `bloobcat/routes/family_invites.py` (`/family/membership`) фильтруют по `status=active` + `allocated_devices>0`.
- Проверка: `python -m py_compile bloobcat/routes/family_invites.py bloobcat/routes/user.py` — OK.
## Журнал изменений

### 2026-02-15 — subscription status: добавлен явный флаг автопродления

- В `bloobcat/routes/subscription.py` расширен ответ `GET /subscription/status`:
  - добавлено поле `autoRenewEnabled` (`bool(user.renew_id)`).
- `POST /subscription/cancel-renewal` теперь возвращает расширенный ответ:
  - `ok`,
  - `autoRenewEnabled: false`,
  - `wasAlreadyCancelled` (помогает фронту различать первое/повторное нажатие).
- Изменение обратно-совместимое для текущего фронта: дополнительные поля не ломают существующих клиентов.

### 2026-02-14 — найден root cause API 502: падение FastAPI на dependency-типе

- По логам с VPS (`bloobcat` + `caddy`) подтверждено:
  - `caddy` отдавал `502` из-за `connect: connection refused` к `bloobcat:33083`;
  - `bloobcat` падал на импорте роутов (`/status/{payment_id}`) с ошибкой FastAPI:
    - `Invalid args for response field ... starlette.requests.Request | None`.
- Корневая причина в `bloobcat/funcs/validate.py`:
  - dependency `validate()` имел сигнатуру `request: Request | None = None`, которая ломает FastAPI schema/dependency analysis на текущем стеке.
- Исправление:
  - сигнатура заменена на `request: Request = None`.
- Локальная проверка:
  - `python -m py_compile bloobcat/funcs/validate.py` — успешно.

### 2026-02-14 — пост-проверка после успешного деплоя: API все еще 502

- GitHub Actions run `22014681822` завершился `success` (step `Deploy to server` тоже `success`).
- Внешняя проверка прод-эндпоинтов показала:
  - `https://api.waiz-store.ru/health` -> `502 Bad Gateway` (Server: Caddy);
  - `https://api.waiz-store.ru/pay/tariffs` -> `502`;
  - `https://admin.waiz-store.ru/server/health` -> `200`.
- Вывод: деплой как процесс прошел, но backend app за reverse-proxy не отвечает корректно (или падает после старта); проблема не в GitHub workflow status.

### 2026-02-14 — RCA: `super_setup` падает `exit 137` без traceback

- Проверен свежий run `Auto Deploy Backend` (`22014607058`):
  - шаг доходит до `Directus super-setup attempt 1/4`;
  - затем immediate `Process completed with exit code 137`;
  - Python traceback отсутствует.
- Вывод: это не типичная логическая ошибка Python, а внешнее завершение процесса (OOM/SIGKILL/kill контейнера).
- Сделано 2 уровня hardening:
  - `.github/workflows/auto-deploy.yml`:
    - `super-setup` переведен в non-blocking post-deploy шаг (не валит релиз бэкенда),
    - при фейле печатаются диагностики `docker compose ps` + логи `bloobcat/directus`.
  - `scripts/directus_super_setup.py`:
    - уменьшена нагрузка на запрос пресетов (`/presets` limit `400` + ограниченный набор `fields`);
    - добавлены короткие паузы между тяжелыми фазами (`DIRECTUS_SUPER_SETUP_PHASE_PAUSE`, default `0.2s`);
    - фазы теперь логируются `Phase start: ...` для точного pinpoint места падения.

### 2026-02-14 — deploy hardening: non-blocking super-setup on exit 137

- По свежему failed run (`22014607058`) зафиксировано:
  - `Directus super-setup attempt 1/4` -> мгновенный `exit code 137` без Python traceback.
  - Это типично для принудительного убийства процесса (OOM/SIGKILL/перезапуск контейнера), а не для обычной логической ошибки в `directus_super_setup.py`.
- В `TVPN_BACK_END/.github/workflows/auto-deploy.yml` изменено поведение:
  - `super-setup` оставлен с retry, но больше не блокирует весь релиз при фейле;
  - при неуспехе выводятся диагностические логи `bloobcat` и `directus`, затем деплой продолжается.
- Цель: не срывать выпуск бэкенда из-за нестабильного post-deploy шага админского UX-setup.

### 2026-02-13 — hotfix workflow: корректная подстановка переменных в SSH heredoc

- Выявлена причина новых падений деплоя после предыдущего hardening-патча:
  - в `auto-deploy.yml` внутри `ssh << EOF` переменные shell (`$DIRECTUS_READY`, `$attempt`, `$max_attempts`, `$COMPOSE_ARGS`) интерпретировались некорректно;
  - в логе это проявлялось как:
    - `-bash: line ...: [: : integer expression expected`;
    - `sh: ...: [: Illegal number:`;
    - преждевременный fail `Directus super-setup failed after retries`.
- Исправление:
  - в проблемных местах переменные экранированы (`\$...`), чтобы они вычислялись на удаленном хосте в нужном контексте.

### 2026-02-13 — стабилизация auto-deploy Directus (готовность + retry)

- В `TVPN_BACK_END/.github/workflows/auto-deploy.yml` усилен этап ожидания Directus:
  - добавлен флаг `DIRECTUS_READY` и явная проверка после цикла ожидания;
  - если health не достигнут за таймаут — деплой завершается с диагностикой (`docker compose ps` + `logs --tail=120 directus`), вместо тихого продолжения.
- Устранен источник флаки-падений после рестарта Directus:
  - запуск `scripts/directus_super_setup.py` внутри `bloobcat` обернут в retry (`4` попытки, пауза `10s` между попытками).
- Контекст инцидента:
  - предыдущий падший run имел `Connection refused` к `directus:8055` на шаге super-setup и `exit code 137` при том, что `git pull --ff-only` прошёл успешно.

### 2026-02-13 — RCA деплоя: это не Git

- Проверен последний failed run `Auto Deploy Backend` (`21999897972`).
- На этапе `Deploy to server` Git-часть проходит штатно:
  - `git stash push -u ...` -> `No local changes to save`;
  - `git pull --ff-only` -> `Already up to date`.
- Фактическое падение происходит позже:
  - в `directus_super_setup.py` при логине в Directus (`http://directus:8055/auth/login`) получен `Connection refused`;
  - job завершается `Process completed with exit code 137`.
- Доп. наблюдение: в логе до SSH есть строка `fatal: not a git repository...`, но она не блокирует деплой и не является корневой причиной падения этого run.

### 2026-02-13 — уведомление о покупке семейной подписки + CTA в раздел семьи

- Добавлено отдельное пользовательское уведомление при успешной покупке семейной подписки (до 10 участников):
  - `bloobcat/bot/notifications/subscription/renewal.py`: новая функция `notify_family_purchase_success_yookassa()`;
  - текст уведомления уточняет, что теперь доступна семейная подписка до 10 человек и предлагает пригласить близких;
  - кнопка в уведомлении открывает фронтенд-раздел семьи: `/subscription/family`.
- Логика отправки интегрирована в покупку:
  - `bloobcat/routes/payment.py`: добавлен helper `_notify_successful_purchase()` с выбором family-уведомления при `device_count >= 10`;
  - применено в трех сценариях: webhook успеха YooKassa, fallback-активация оплаты, покупка с бонусного баланса.

### 2026-02-13 — Логи ошибок в админке + triage workflow

- Добавлен workflow-статус в backend-модель логов ошибок:
  - `bloobcat/db/error_reports.py`: поля `triage_status`, `triage_owner`, `triage_note`, `triage_updated_at`.
  - `bloobcat/routes/error_reports.py`: новые входящие баг-репорты создаются со статусом `triage_status='new'`.
  - Миграция: `migrations/models/75_20260213_error_reports_triage_workflow.py`.
- Расширен setup Directus:
  - `scripts/directus_super_setup.py`:
    - коллекция `error_reports` вынесена в левое меню: группа `Служебное` → `Логи ошибок`;
    - добавлены RU-notes/переводы для полей error reports;
    - `triage_status` настроен как dropdown (`new / in_progress / resolved`);
    - добавлен UX формы/списка для triage;
    - права: `Manager` получил `read + update` для `error_reports` (без create/delete), `Viewer` — `read`, `Administrator` — полный доступ;
    - добавлены bookmarks: «не взяты», «в работе», «исправлены».
- В модуле `tvpn-home` добавлен быстрый переход в раздел:
  - `directus/extensions/tvpn-home/src/module.vue` → пункт навигации `Логи ошибок` (`/content/error_reports`).

### 2026-02-13 — SLA по баг-репортам (критичность + просрочка 24ч)

- Расширена модель `error_reports`:
  - `triage_severity` (`low/medium/high/critical`, default `medium`);
  - `triage_due_at` (SLA дедлайн triage).
- Добавлена миграция:
  - `migrations/models/76_20260213_error_reports_sla_and_severity.py`
  - для исторических записей `triage_due_at` backfill: `created_at + 24h`.
- Обновлен приём баг-репортов:
  - `bloobcat/routes/error_reports.py` задает новым логам `triage_due_at = now + 24h`.
- В Directus setup:
  - поле `triage_severity` настроено как dropdown;
  - добавлен bookmark **«Баг-репорты: просрочен triage (24ч)»** (`status=new` и `triage_due_at <= $NOW`);
  - в bookmarks/таблицах добавлены колонки критичности и SLA дедлайна.

### 2026-02-12 — моментальный пересчет тарифов при сохранении в Directus (Hook)

- Добавлен защищенный endpoint вычисления цены:
  - `POST /admin/integration/tariffs/compute-pricing`
  - файл: `bloobcat/routes/admin_integration.py`
  - логика: `bloobcat/services/admin_integration.py::compute_tariff_effective_pricing`.
- Расширен Directus hook `directus/extensions/remnawave-sync`:
  - `filter("items.create"/"items.update")` для коллекции `tariffs`;
  - перед сохранением вызывается backend compute endpoint и в payload подставляются `base_price` и `progressive_multiplier`;
  - за счет filter-хука пересчет происходит в момент сохранения карточки (без ожидания фронтовых/API обращений).
- Безопасность:
  - hook использует уже существующий `ADMIN_INTEGRATION_TOKEN`;
  - при любой ошибке вычисления сохранение не блокируется (fallback: payload без пересчета).
- UX/надежность setup:
  - в `scripts/directus_super_setup.py` добавлена проверка `verify_tariffs_form_visibility()` для контроля, что ключевые поля `tariffs` не скрыты.

### 2026-02-12 — UX-апгрейд админки тарифов: финальные цены карточек + понятная family-логика

- Цель:
  - упростить управление тарифами в Directus: редактировать именно финальную цену карточки, а не вручную подбирать `base_price`/`progressive_multiplier`;
  - сделать структуру формы тарифов более понятной и ближе к фактическим карточкам витрины.
- Что изменено:
  - `bloobcat/db/tariff.py`:
    - добавлены поля `family_plan_enabled`, `final_price_default`, `final_price_family`;
    - добавлен авто-пересчет эффективных `base_price/progressive_multiplier` из финальных цен карточек;
    - `calculate_price()` и snapshot-цены теперь используют эффективные (пересчитанные) параметры.
  - `migrations/models/74_20260212_add_tariff_card_pricing_fields.py`:
    - миграция новых полей карточек тарифа.
  - `bloobcat/routes/subscription.py`:
    - семейная карточка 12 месяцев учитывает `family_plan_enabled`;
    - покупка по `12months_family` доступна только если семейный режим включен.
  - `bloobcat/routes/payment.py`:
    - в snapshot `ActiveTariffs.progressive_multiplier` сохраняется эффективный множитель (после авто-пересчета из финальных цен).
  - `scripts/directus_super_setup.py`:
    - обновлены заметки/переводы для `tariffs` с акцентом на редактирование финальных цен;
    - добавлены `apply_tariffs_form_ux()` и `ensure_tariffs_presentation_dividers()`:
      - секции формы: карточка, финальные цены, лимиты/family, формула, LTE;
      - заметки-пояснения по family/non-family;
    - добавлены presets: `Тарифы: карточки витрины`, `Тарифы: 12 месяцев + family`.
  - `scripts/vps/seed_tariffs.sql`:
    - сид дополнен новыми полями и финальными ценами карточек.

### 2026-02-12 — синхронизация тарифов frontend ↔ admin (Directus) ↔ backend

- Проблема:
  - фронт строил планы из `GET /pay/tariffs` + локального маппинга, а не из `GET /subscription/plans`;
  - в `tariffs` не было `is_active` и явных лимитов устройств для витрины планов;
  - в админке нельзя было корректно управлять доступностью тарифа и лимитами планов.
- Что изменено:
  - `bloobcat/db/tariff.py`:
    - добавлены поля `is_active`, `devices_limit_default`, `devices_limit_family`;
    - `calculate_price()` переведен на `round`, чтобы цены совпадали с фронтовой витриной (290/748/1287/2189/4489).
  - `bloobcat/routes/payment.py`:
    - `GET /pay/tariffs` теперь возвращает только активные тарифы (`is_active=true`);
    - `GET /pay/{tariff_id}` блокирует покупку неактивного тарифа;
    - fallback-обработка платежа стала устойчивой к удаленному/неактивному тарифу (создается snapshot `ActiveTariff`, чтобы не терять состояние подписки).
  - `bloobcat/routes/subscription.py`:
    - `GET /subscription/plans` строится только из активных тарифов;
    - добавлен `tariffId` в ответ планов;
    - лимиты устройств для планов берутся из `tariffs.devices_limit_default/family`;
    - семейный план остается 12-месячным вариантом (не отдельной базовой записью тарифа).
  - `migrations/models/73_20260212_add_tariffs_activation_and_limits.py`:
    - миграция полей `is_active`, `devices_limit_default`, `devices_limit_family`.
  - `scripts/vps/seed_tariffs.sql`:
    - сид обновлен под новые поля и синхронные лимиты устройств.
  - `scripts/directus_super_setup.py`:
    - добавлены русские подсказки/лейблы по новым полям тарифов в админке.

### 2026-02-12 — расследование: 4 одинаковых trial-сообщения после регистрации

- Симптом:
  - новый пользователь получил 4 одинаковых сообщения «Поздравляем ... бесплатный 3-дневный доступ ...» сразу после первого входа.
- Подтвержденный источник текста:
  - `bloobcat/bot/notifications/trial/granted.py` (`notify_trial_granted`).
- Где вызывается отправка:
  - `bloobcat/db/users.py` в `_ensure_remnawave_user()`, когда одновременно выполняются условия:
    - `expired_at is None`,
    - `used_trial = False`,
    - и идет первичная привязка к RemnaWave.
- Почему возможны дубли:
  - на старте фронт может параллельно дергать несколько endpoint (`/auth/telegram`, `/user`, `/devices` и др. через `validate -> Users.get_user`);
  - локальный `asyncio.Lock` в `_ensure_remnawave_user` защищает только в рамках одного процесса;
  - при нескольких воркерах/инстансах нет межпроцессной дедупликации именно для `trial_granted` (нет `NotificationMarks` на этот тип уведомления).
- Важное уточнение по "регистрации":
  - пользователь создается в БД при первом `Users.get_user()` (обычно на `/auth/telegram`/`validate`);
  - флаг `is_registered=True` выставляется позже в `bloobcat/routes/remnawave/catcher.py` (`remnawave_updater`) только после первого фактического подключения к VPN (`onlineAt` в RemnaWave).

### 2026-02-12 — Directus users: пустые секции + клики из списка

- Симптомы:
  - в карточке `users` видны только заголовки секций (`Основное`, `Подписка` и т.д.), а поля не отображаются;
  - из списка пользователей переход в карточку может работать нестабильно.
- Подтвержденная причина:
  - в старых конфигурациях часть полей `users` могла остаться с `meta.hidden=true`;
  - setup ранее не принудительно снимал скрытие при раскладке полей по секциям;
  - часть пресетов могла иметь некорректный `layout_query` (невалидный `fields`), что влияет на открытие item-view.
- Что исправлено в `scripts/directus_super_setup.py`:
  - `apply_users_form_ux()` теперь выставляет `hidden=False` для всех ключевых полей карточки;
  - усилено восстановление пресетов:
    - `ensure_tabular_fields_include_pk()` лечит битые `layout_query/tabular/fields`;
    - `ensure_cards_fields_include_pk()` лечит битые `layout_query/cards/fields`.
- Проверка:
  - `python -m py_compile scripts/directus_super_setup.py` — успешно.
  - setup применен на локальный инстанс: `python scripts/directus_super_setup.py` — успешно.
  - setup применен на прод: `DIRECTUS_URL=https://admin.waiz-store.ru python scripts/directus_super_setup.py` — успешно.
  - API-проверка после применения:
    - у полей `users` (`id`, `username`, `full_name`, `email`, `expired_at`, `balance`, `is_blocked`, `referred_by`) `meta.hidden=false`;
    - у пользовательского tabular-пресета `users` присутствует поле `id` (критично для клика/перехода в item-view).

### 2026-02-12 — Премиум-доводка карточки users (прод)

- Что улучшено в `scripts/directus_super_setup.py`:
  - `is_admin` перенесен в секцию `Основное`:
    - `group=ui_divider_overview`, `sort=9`, `interface=toggle`, `hidden=false`;
  - устранен конфликт сортировки в секции `Операции`:
    - `ui_divider_ops` оставлен на `sort=66`, поля смещены начиная с `67`;
  - стабилизировано создание o2m-блоков (логи/связи):
    - alias-поле создается/патчится до обновления relation metadata;
    - из payload убран `special` для `/fields/*` (совместимость с Directus API, где это поле запрещено);
    - добавлен безопасный разбор `400 already exists`.
- Результат на проде (`admin.waiz-store.ru`):
  - setup повторно применен успешно;
  - карточка `users` получила корректную структуру с блоком `Операции`;
  - alias-поля операций доступны по endpoint `GET /fields/users/<alias>`:
    - `referred_users_list`, `active_tariffs_list`, `promo_usages_list`,
      `notification_marks_list`, `family_devices_list`,
      `partner_withdrawals_list`, `partner_earnings_list`, `family_audit_logs_owner`.

### 2026-02-12 — финальный фикс "пустых секций" users

- Симптом:
  - в карточке отображались только divider-блоки и `is_admin`, остальные поля не рендерились.
- Причина:
  - для части полей `users` в `meta.interface` было `null`; в текущем Directus UI такие поля могли не показываться в item-view.
  - отдельный прогон setup на проде мог падать из-за transient `503` на PATCH `/fields/...`.
- Исправлено:
  - в `ensure_users_presentation_dividers()` добавлен набор явных интерфейсов:
    - `input` для текстовых/числовых полей,
    - `toggle` для булевых,
    - существующие `datetime`, `id-link-editor`, `list-o2m` сохранены.
  - в `patch_field_meta()` добавлен retry на `502/503/504` (3 попытки с backoff), чтобы setup не обрывался посередине.
- Проверка:
  - повторный прод-прогон завершился успешно;
  - подтверждено по API, что ключевые поля (`username`, `full_name`, `email`, `balance`, `lte_gb_total`, `referrals`, `remnawave_uuid` и др.) имеют `interface` и `hidden=false`.

### 2026-02-12 — root cause найден: `group` на divider скрывал поля

- Факт:
  - на проде поля начинали отображаться сразу после снятия `meta.group` у `users`-полей;
  - при `group=ui_divider_*` (где divider = `presentation-divider`) UI рендерил только разделители/пустой контент.
- Исправление:
  - `users`-поля переведены в режим **без `group`** (`group=None`);
  - разделы сохраняются через `presentation-divider` и `sort`, но без привязки полей к divider как к group-якорю;
  - сохранены явные `interface` для полей (`input/toggle/datetime/id-link-editor/list-o2m`);
  - дополнительно выданы явные read-права Manager/Viewer на schema-коллекции:
    - `directus_collections`, `directus_fields`, `directus_relations`, `directus_presets`.
- Результат:
  - карточка `users` и форма создания элемента снова рендерят все ключевые поля;
  - редактирование пользователя доступно.

### 2026-02-12 — luxury UX слой users + русификация англоязычных пунктов

- Что добавлено в `scripts/directus_super_setup.py`:
  - новый этап `apply_users_luxury_ux()`:
    - KPI-блок вверху карточки (`ui_divider_kpi`): ключевые поля вынесены в top-zone (`balance`, `expired_at`, `lte_gb_total`, `is_subscribed`, `is_blocked`, `referrals`);
    - блок быстрых действий (`ui_divider_quick_actions`) с 1-клик переходами:
      - `referred_by` (к родителю-рефереру),
      - `active_tariff_id` (к активному тарифу),
      - быстрые тех-поля `familyurl`, `remnawave_uuid`;
    - улучшены шаблоны list-o2m для логов/истории:
      - `active_tariffs_list`, `promo_usages_list`, `notification_marks_list`, `family_audit_logs_owner`.
  - расширена русификация и пояснения в `apply_field_notes_ru()`:
    - добавлены `translations` и `note` для англоязычных полей `users` (`is_admin`, `is_subscribed`, `used_trial`, `active_tariff_id`, `remnawave_uuid` и др.);
    - в UI карточки лейблы и описания стали русскими и операционно понятными.
  - дополнительно усилены пояснения для системно-английских полей:
    - `username`, `full_name`, `created_at`, `prize_wheel_attempts`.
- Важно:
  - UX-слой реализован **без `meta.group`**, чтобы не вернуть баг с пустыми секциями на проде.
- Применение:
  - синтаксис: `python -m py_compile scripts/directus_super_setup.py` — успешно;
  - прод: `DIRECTUS_URL=https://admin.waiz-store.ru python scripts/directus_super_setup.py` — успешно.

### 2026-02-12 — корректировка luxury-слоя без вмешательства в прошлую раскладку

- Обратная связь:
  - после первой версии luxury-слоя поля визуально "перемешались".
- Причина:
  - в `apply_users_luxury_ux()` были патчи `sort/width` для существующих полей (`balance`, `expired_at`, `referred_by`, `active_tariff_id` и др.), что переупорядочивало базовую раскладку.
- Исправление:
  - из luxury-слоя удалены все мутации `sort/width` существующих полей;
  - прошлый порядок карточки оставлен как в стабильной итерации;
  - luxury-надстройка переведена в безопасный режим: только enrichment (notes/templates) + опциональные divider-поля в конце формы (`sort=9900+`), чтобы не влиять на основной UX.
- Дополнительно:
  - русские названия/пояснения для ранее англоязычных полей сохранены (`Логин`, `ФИО`, `Создан`, `Попытки колеса` и др.).
- Применение/проверка:
  - `python -m py_compile scripts/directus_super_setup.py` — успешно;
  - прод-прогон setup — успешно;
  - карточка `users` открывается, поля отображаются в прежнем порядке (без повторного перемешивания).

### 2026-02-12 — заполнение пустых luxury-секций KPI/быстрые действия

- Обратная связь:
  - в карточке `users` блоки `KPI и быстрый статус` и `Быстрые действия` отображались пустыми.
- Причина:
  - после безопасного переноса этих divider-полей в самый низ (`sort=9900+`) под ними не было отдельных полей/виджетов.
- Что добавлено:
  - в `apply_users_luxury_ux()` добавлены alias-поля (без изменения существующего порядка полей):
    - `ui_kpi_notice` (`presentation-notice`) — назначение KPI-блока;
    - `ui_kpi_links` (`presentation-links`) — быстрые переходы к связанным спискам:
      - `active_tariffs` по `user_id={{id}}`,
      - `promo_usages` по `user_id={{id}}`,
      - `notification_marks` по `user_id={{id}}`;
    - `ui_quick_actions_notice` (`presentation-notice`) — объяснение quick actions;
    - `ui_quick_actions_links` (`presentation-links`) — 1-клик переходы:
      - к рефереру `users/{{referred_by}}`,
      - к активному тарифу `active_tariffs/{{active_tariff_id}}`,
      - к списку рефералов `users?filter[referred_by][_eq]={{id}}`.
- Доп. фикс:
  - в URL button-links заменен префикс `/admin/content/...` на `/content/...`, т.к. при старом варианте роутер Directus строил путь как `/admin/admin/...` и открывал 404.
- Важно:
  - существующие поля и их сортировка не менялись;
  - добавлены только новые alias/presentation-блоки под уже существующие divider-секции.

### 2026-02-12 — объединение KPI/действий в один раздел + фильтры по текущему пользователю

- Запрос:
  - объединить `KPI и быстрый статус` и `Быстрые действия` в единый блок;
  - гарантировать, что переходы по логам/историям показывают данные только по текущему пользователю.
- Что изменено в `apply_users_luxury_ux()`:
  - единый раздел:
    - `ui_divider_kpi` переименован в `KPI и быстрые действия`;
    - `ui_divider_quick_actions` скрыт (`hidden=true`) как устаревший второй заголовок;
    - блоки `ui_quick_actions_notice` и `ui_quick_actions_links` подняты в тот же раздел (`sort=9903/9904`).
  - фильтры/переходы:
    - `active_tariffs` — `/content/active_tariffs?filter[user_id][_eq]={{id}}`;
    - `promo_usages` — `/content/promo_usages?filter[user_id][_eq]={{id}}`;
    - `notification_marks` — `/content/notification_marks?filter[user_id][_eq]={{id}}`;
    - добавлен `family_audit_logs` — `/content/family_audit_logs?filter[owner_id][_eq]={{id}}`;
    - `referred_by`/`active_tariff_id` остаются точечными переходами к связанным item;
    - список рефералов — `/content/users?filter[referred_by][_eq]={{id}}`.
- Результат UX:
  - один компактный операционный раздел внизу карточки;
  - все список-переходы открываются уже отфильтрованными по текущему пользователю.

### 2026-02-12 — добавлен индикатор filtered-view в карточке users

- Запрос:
  - показать оператору явный маленький индикатор, что переходы работают в режиме фильтра по текущему пользователю.
- Реализация:
  - в `apply_users_luxury_ux()` добавлено alias-поле `ui_filter_indicator`:
    - `interface: presentation-notice`,
    - `icon: filter_alt`,
    - текст: «Фильтр активен: переходы ниже открываются только в filtered-view по текущему пользователю.»,
    - компактное размещение `width=half`.
  - скорректированы `sort` у соседних блоков (`ui_kpi_links`, `ui_quick_actions_notice`, `ui_quick_actions_links`), чтобы индикатор стоял рядом/перед быстрыми ссылками.

### 2026-02-12 — фикс deep-link фильтров: формат `filter=<json>`

- Симптом:
  - по кнопкам открывался список, но фильтрация в Data Studio могла не применяться.
- Причина:
  - формат query `filter[field][_eq]=...` в deep-links для интерфейса `presentation-links` на текущем инстансе/версии мог не интерпретироваться как активный state-фильтр списка.
- Исправление:
  - все ссылки переведены на формат `filter=<json>` (url-encoded), например:
    - `/content/active_tariffs?filter=%7B%22user_id%22%3A%7B%22_eq%22%3A%22{{id}}%22%7D%7D`
    - `/content/users?filter=%7B%22referred_by%22%3A%7B%22_eq%22%3A%22{{id}}%22%7D%7D`
- Ожидаемый результат:
  - списки открываются сразу с примененным фильтром по текущему пользователю.

### 2026-02-12 — fallback на гарантированно фильтрованные истории в карточке users

- Проблема:
  - в текущем инстансе Data Studio `content`-роутер не отражал/не применял query `filter` как ожидаемый active-filter state списка (даже при валидном формате).
- Решение:
  - быстрые ссылки переведены с внешних collection-списков на встроенные relation-истории внутри карточки пользователя (через hash-якоря):
    - `#active_tariffs_list`,
    - `#promo_usages_list`,
    - `#notification_marks_list`,
    - `#family_audit_logs_owner`,
    - `#referred_users_list`.
- Почему это корректно:
  - эти списки — `o2m` связи текущего пользователя, поэтому данные по определению уже фильтрованы только по нему.
- Доп. правка UX:
  - текст индикатора обновлен, чтобы явно отражать новую механику: переход к встроенным спискам карточки пользователя (без заявлений про collection filtered-view).

### 2026-02-12 — расследование дублей подписки в RemnaWave (один user -> много remote users)

- Симптом:
  - у одного Telegram user в RemnaWave появляется несколько пользователей/подписок (`id`, `id_1`, `id_2`...).
- Подтвержденные причины в коде:
  - на фронте при старте параллельно уходят `POST /auth/telegram`, `GET /user`, `GET /devices` (до появления Bearer токена часто с `initData`);
  - при `initData` backend в `validate()` вызывает `Users.get_user()`, а он вызывает `_ensure_remnawave_user()` если `remnawave_uuid` пустой;
  - гонка: несколько одновременных запросов видят `remnawave_uuid=None` и одновременно доходят до `create_user`;
  - `_ensure_remnawave_user()` на коллизии имени (`already exists`) перебирает `username` с суффиксами `_1/_2`, фактически создавая дубликаты в RemnaWave;
  - `recreate_remnawave_user()` используется в `remnawave_updater` и `payment` при `User not found` и не удаляет старого remote user перед пересозданием.
- Дополнительный риск:
  - в `remnawave/catcher.py` флаг `update_in_progress` не защищен lock'ом (check/set не атомарны), что может запускать конкурирующие апдейтеры.
- Где подтверждено:
  - `TVPN_BACK_END/bloobcat/db/users.py` (`_ensure_remnawave_user`, `recreate_remnawave_user`, `get_user`);
  - `TVPN_BACK_END/bloobcat/routes/remnawave/catcher.py` (`update_in_progress`, `recreate_remnawave_user` вызовы);
  - `TVPN_BACK_END/bloobcat/routes/payment.py` (retry/update + `recreate_remnawave_user`);
  - `TelegramVPN/src/hooks/useAuthBootstrap.ts`, `useTvpnUserSync.ts`, `useTvpnDevicesSync.ts` (параллельный старт запросов).
- Рекомендованный план фикса:
  - сделать идемпотентность создания RemnaWave user на backend (пер-пользовательный lock + повторная проверка `remnawave_uuid` после lock);
  - убрать автогенерацию `username_*` как fallback для одного Telegram user, сначала искать существующего remote user по `telegramId/email` (или иной стабильной связке);
  - сделать `recreate_remnawave_user()` безопасным: сначала попытка rebind к существующему remote user, затем controlled recreate (по политике), опционально удаление/деактивация старого;
  - защитить `remnawave_updater` через `asyncio.Lock`;
  - добавить диагностику: `user_id`, caller, old/new uuid, причина recreate, correlation_id.

### 2026-02-12 — фикс дублей RemnaWave без ломки синхронизации

- Реализовано в `TVPN_BACK_END`:
  - `bloobcat/db/users.py`:
    - добавлен пер-пользовательный `asyncio.Lock` для `_ensure_remnawave_user()` и `recreate_remnawave_user()`;
    - удалена стратегия создания `username` с суффиксами `_1/_2` (источник дублей);
    - при коллизии имени добавлен **rebind** к существующему remote user по lookup (`telegramId/email/username`) + fallback-поиск по `/api/users`;
    - в `recreate_remnawave_user()` добавлена проверка: если текущий UUID уже валиден, пересоздание не выполняется.
  - `bloobcat/routes/remnawave/client.py`:
    - добавлены методы lookup: `get_user_by_telegram_id`, `get_user_by_email`, `get_user_by_username` (без retry, чтобы не ждать 60с на старых панелях без endpoint'ов).
  - `bloobcat/routes/remnawave/catcher.py`:
    - `update_in_progress` заменен на `asyncio.Lock` + non-blocking acquire через `wait_for(..., timeout=0)` для безопасного skip при параллельных запусках.
- Проверка:
  - `python -m py_compile bloobcat/db/users.py bloobcat/routes/remnawave/client.py bloobcat/routes/remnawave/catcher.py` — успешно.
- Известный компромисс:
  - в админских миграционных отчётах (`miglte/migexternal`) счётчик `recreated` может включать случаи rebind (это не ломает синхронизацию, но меняет точность формулировки «пересоздано»).

### 2026-02-12 — hardening под высокий трафик / штормы / ddos-like пики

- Дополнительно усилено:
  - `bloobcat/db/users.py`:
    - добавлен межпроцессный lock через PostgreSQL advisory lock (`pg_try_advisory_lock`) поверх in-process lock, чтобы защитить создание/пересоздание пользователя и при нескольких воркерах/инстансах;
    - fallback-safe поведение: при недоступности advisory lock система не падает, продолжает работу с локальным lock и логирует предупреждение.
  - `bloobcat/funcs/validate.py`:
    - добавлен fast-path: если пользователь уже есть и имеет `remnawave_uuid`, возвращаем его без повторного `Users.get_user()` (снижение нагрузки на БД и side-effects при initData-шторме).
  - `bloobcat/middleware/rate_limit.py`:
    - rate limiter сделан thread-safe/async-safe (`asyncio.Lock`);
    - добавлены IP-лимиты для горячих endpoint'ов: `/auth/telegram`, `/user`, `/devices`, `/app/info`, `/partner/summary`;
    - сохранены и адаптированы существующие user-level лимиты для операций (`reset_devices`, `family/revoke`, `promo/*`).
- Верификация:
  - `python -m py_compile bloobcat/db/users.py bloobcat/funcs/validate.py bloobcat/middleware/rate_limit.py` — успешно.
  - lint по измененным файлам — без ошибок.

### 2026-02-12 — post-review приоритетные фиксы (устранение найденных багов)

- По результатам code review внесены корректировки:
  - `bloobcat/middleware/rate_limit.py`:
    - исправлен bypass user-rate-limit для Bearer токенов (`get_user_id_from_request` теперь понимает JWT и initData);
    - добавлена защита от memory leak в limiter (очистка пустых ключей после window cleanup);
    - ужесточена работа с `X-Forwarded-For`: используется только если прямой клиент в `RATE_LIMIT_TRUSTED_PROXIES`;
    - добавлен fallback IP limiter для чувствительных endpoint'ов при `user_id=None`.
  - `bloobcat/funcs/validate.py`:
    - fast-path ограничен условием `not start_param`, чтобы не потерять referral/utm обработку.
  - `bloobcat/db/users.py`:
    - удален advisory-lock слой (как потенциально нестабильный из-за session-scoped семантики и пула соединений);
    - сохранен устойчивый in-process lock + идемпотентный rebind/create подход;
    - при rebind в `_ensure_remnawave_user` сохраняются также trial-поля (`is_trial`, `used_trial`, `expired_at`) во избежание повторной выдачи триала.
- Проверка:
  - `python -m py_compile ...` — успешно;
  - lint по измененным файлам — без ошибок.

### 2026-02-12 — фактическая верификация отказоустойчивости (контейнерный smoke + stress)

- Выполнено:
  - контейнерная проверка регрессионных тестов: `tests/test_resilience_hardening.py` (внутри `bloobcat` контейнера);
  - burst-тесты по HTTP на локально поднятом `bloobcat`:
    - `GET /app/info` (360 запросов, параллелизм 80),
    - `POST /promo/redeem` без авторизации (90 запросов, параллелизм 50).
- Результаты:
  - Unit/regression: `7 passed` (warnings только deprecation от tortoise);
  - `/app/info`: `200: 300`, `429: 60` — лимит отрабатывает корректно;
  - `/promo/redeem` unauth: `403: 60`, `429: 30` — fallback limiter для неавторизованных штормов срабатывает.
- Важный найденный и устраненный дефект:
  - при ранней версии middleware возвращался `500` вместо `429` (из-за `raise HTTPException` в middleware);
  - исправлено на явный `JSONResponse(status_code=429, Retry-After=...)`.
- Итог:
  - после исправлений текущая итерация подтверждена как рабочая в контейнерной среде с нагрузочными burst-сценариями.

### 2026-02-13 — Directus `tvpn-home`: кнопка сохранения техработ

- Контекст:
  - После вынесения настроек техработ в отдельную карточку кнопка "Сохранить" оставалась неактивной.
  - Причина: кнопка была заблокирована условием `:disabled="!settingsId"`, а `settingsId` может быть пустым, если запись в `tvpn_admin_settings` еще не создана.
- Исправление в `directus/extensions/tvpn-home/src/module.vue`:
  - Для обеих кнопок сохранения изменено условие блокировки на `:disabled="settingsSaving"`.
  - В `saveSettings()` добавлен upsert-подобный сценарий:
    - если `settingsId` отсутствует -> `POST /items/tvpn_admin_settings` и сохранить `id`;
    - иначе -> `PATCH /items/tvpn_admin_settings/{id}`.
- Верификация:
  - lint по файлу `module.vue` — без ошибок;
  - пересборка расширения `tvpn-home` (`npm run build`) — успешно.

### 2026-02-13 — Directus `tvpn-home`: сохранение через singleton endpoint

- Проблема:
  - В UI появлялась ошибка "Не удалось сохранить настройки (нет прав или коллекция не создана)".
  - Основная причина: роль часто имеет `update`, но не имеет `create`; сценарий с `POST` при пустом `settingsId` ломался.
- Исправление в `directus/extensions/tvpn-home/src/module.vue`:
  - `loadSettings()` сначала читает `GET /items/tvpn_admin_settings/singleton`, затем fallback на list endpoint.
  - `saveSettings()` сначала пишет `PATCH /items/tvpn_admin_settings/singleton`.
  - При `403/404` добавлен fallback на старый сценарий (`POST`/`PATCH by id`), чтобы покрыть нестандартные окружения.
  - Добавлены более точные тексты ошибок по статусам `401/403/404`.
- Верификация:
  - lint по `module.vue` — без ошибок.
  - сборка `tvpn-home` (`npm run build`) — успешно.
  - `python -m py_compile scripts/directus_super_setup.py` — успешно.

- Дополнительно:
  - В `scripts/directus_super_setup.py` для коллекции `tvpn_admin_settings` добавлена выдача `create` для роли Manager (вместе с `read/update`) для надежного fallback-создания записи.

### 2026-02-13 — RCA по ошибке "Коллекция tvpn_admin_settings не найдена"

- Что проверено вручную:
  - `directus_super_setup.py` не выполнялся из-за недоступного `http://localhost:8055` (контейнер Directus не был запущен).
  - После `docker compose up -d directus` и повторного запуска setup скрипт завершился успешно.
  - В БД подтверждено наличие коллекции: `directus_collections.collection='tvpn_admin_settings'`, `singleton=true`.
- Найден корневой дефект во frontend-модуле `tvpn-home`:
  - fallback сохранения использовал `POST /items/tvpn_admin_settings`, а для singleton в текущем Directus этот route возвращает `404 ROUTE_NOT_FOUND`.
  - из-за этого UI показывал сообщение "коллекция не найдена", хотя коллекция существовала.
- Исправление в `directus/extensions/tvpn-home/src/module.vue`:
  - чтение настроек переведено на `GET /items/tvpn_admin_settings` с корректным разбором singleton-ответа-объекта;
  - сохранение переведено на `PATCH /items/tvpn_admin_settings` (рабочий singleton endpoint для данного окружения);
  - оставлен fallback `PATCH /items/tvpn_admin_settings/{id}` для legacy/non-singleton сценариев.
- Верификация:
  - `npm run build` для `tvpn-home` — успешно;
  - API check: `GET /items/tvpn_admin_settings` возвращает `200` и `{"data":{"id":1}}`.

### 2026-02-13 — финальный фикс сохранения техработ (права/поля)

- Симптом:
  - В админке при сохранении блока "Технические работы" сохранялся UI-error `Не удалось сохранить настройки...`.
- Корневая причина:
  - В таблице `tvpn_admin_settings` в БД был только столбец `id` (остальные поля отсутствовали).
  - Из-за этого `PATCH /items/tvpn_admin_settings` падал на payload с `maintenance_*` и алерт-полями.
  - Почему так случилось: в `scripts/directus_super_setup.py` функция `ensure_field()` прекращала работу на `GET /fields/...` со статусом `403` и не пробовала создать поле, хотя `POST /fields/...` в этой инсталляции разрешен.
- Исправление:
  - `scripts/directus_super_setup.py` (`ensure_field`) изменен: при любом `GET != 200` выполняется попытка `POST /fields/{collection}` (с сохранением idempotency через `409`/`401`/`403`).
  - Скрипт `directus_super_setup.py` повторно выполнен.
- Подтверждение:
  - В `tvpn_admin_settings` теперь есть все колонки (`maintenance_mode`, `maintenance_message`, `alerts_enabled`, пороговые поля и т.д.).
  - `PATCH /items/tvpn_admin_settings` возвращает `200` и корректно обновляет значения.

### 2026-02-13 — hardening сохранения техработ (multi-endpoint fallback + post-create grants)

- Симптом:
  - Пользователь продолжал видеть ошибку сохранения в блоке "Технические работы" даже после базовых фиксов.
- Что доработано:
  - `directus/extensions/tvpn-home/src/module.vue`:
    - `loadSettings()` получил fallback чтения через `GET /items/tvpn_admin_settings/singleton`, если основной `GET /items/tvpn_admin_settings` недоступен;
    - `saveSettings()` теперь:
      - отправляет payload без `id`,
      - сначала пробует `PATCH /items/tvpn_admin_settings`,
      - затем fallback `PATCH /items/tvpn_admin_settings/singleton`,
      - и в крайнем случае `PATCH /items/tvpn_admin_settings/{id}` для legacy-сценариев;
    - улучшена диагностика ошибок: отдельный текст для network error и вывод деталей ответа API при неизвестном статусе.
  - `scripts/directus_super_setup.py`:
    - в `ensure_admin_settings()` добавлен grant `create` для Manager на `tvpn_admin_settings`;
    - в `main()` добавлен повторный `ensure_permissions_baseline(client)` сразу после `ensure_admin_settings(client)`, чтобы права гарантированно выставлялись и для коллекций, созданных поздно по ходу setup.
- Верификация:
  - `python -m py_compile scripts/directus_super_setup.py` — успешно;
  - `npm run build` в `directus/extensions/tvpn-home` — успешно;
  - `python scripts/directus_super_setup.py` — успешно;
  - `docker compose restart directus` — выполнено;
  - API smoke: `PATCH /items/tvpn_admin_settings` — `200`, rollback — `200`.

### 2026-02-13 — локальный E2E smoke (Manager + UI)

- Выполнено на локальном стенде `http://localhost:8055`:
  - создан тестовый пользователь `manager.local.test@example.com` с ролью `Manager` (active);
  - под токеном Manager проверены:
    - `GET /items/tvpn_admin_settings` -> `200`;
    - `PATCH /items/tvpn_admin_settings` -> `200`;
    - rollback сохранения -> `200`.
- UI-проверка через браузер MCP:
  - в `Directus /admin/tvpn-home` изменен `Текст для пользователей`;
  - нажата кнопка `Сохранить` в блоке "Технические работы";
  - после завершения запроса кнопка вернулась в активное состояние без error-алерта (признак успешного save).
- После UI-теста значение `maintenance_message` откатили на `probe` (чтобы не оставлять тестовый текст).

### 2026-02-16 — hotfix backend startup crash (NameError Any)

- Симптом:
  - продовый backend не поднимался, публичные endpoint (`/health`, `/app/info`) отдавали `502` через reverse-proxy;
  - в контейнерных логах повторялась ошибка запуска: `NameError: name 'Any' is not defined` в `bloobcat/routes/payment.py`.
- Корневая причина:
  - в `payment.py` использовалась аннотация `Any` (`def _meta_bool(value: Any, ...)`), но импорт `Any` из `typing` отсутствовал.
- Исправление:
  - в `TVPN_BACK_END/bloobcat/routes/payment.py` добавлен импорт `from typing import Any`.
- Проверка:
  - `python -m py_compile bloobcat/routes/payment.py` проходит без ошибок.

### 2026-02-20 — self-heal FK для notification_marks (ON DELETE CASCADE)

- Симптом:
  - удаление пользователей через Directus падало с ошибкой: `FOREIGN KEY constraint violation` на `notification_marks_user_id_fkey`.
- Причина:
  - FK constraint `notification_marks_user_id_fkey` был создан без `ON DELETE CASCADE`;
  - Aerich записал миграцию `81_20260220_fix_notification_marks_cascade.py` как "applied", но SQL на проде не выполнился;
  - из-за этого удаление cascade'ом не работало.
- Исправление:
  - в `bloobcat/db/fk_guards.py` добавлена `ensure_notification_marks_fk_cascade()` по аналогии с `ensure_active_tariffs_fk_cascade()`;
  - функция при старте приложения проверяет constraint через `information_schema.table_constraints / key_column_usage`;
  - если constraint существует, но без `ON DELETE CASCADE` — автоматически пересоздаёт его с CASCADE (self-heal);
  - функция вызывается в `lifespan` сразу после `ensure_active_tariffs_fk_cascade()`.
- DB-003 (2026-02-20) — доработки после review:
  - **notification_marks guard** сделан schema-aware + constraint-agnostic (как active_tariffs): добавлен `_resolve_notification_marks_schema()`, поиск FK по `table_name`/`column_name`/`ccu`, без завязки на имя constraint;
  - **__main__**: лог "FK self-heal guard выполнен" выводится только когда оба guard'а вернули `True` (раньше был ложнопозитив при early return);
  - **тесты**: `tests/test_resilience_hardening.py` — добавлены `test_ensure_notification_marks_fk_cascade_*` (repairs_non_cascade, repairs_missing_constraint, skips_when_single_cascade, returns_false_when_table_not_found);
  - оба guard'а возвращают `bool` (True = ran, False = early return).

### 2026-02-20 — registration gate hardening (only family/ref/qr bypass)

- **fix**: ужесточена логика авто-регистрации, чтобы новый пользователь без валидного deep-link не создавался неявно.
- Изменения:
  - добавлен модуль `bloobcat/funcs/start_params.py` с единым whitelist-предикатом `is_registration_exception_start_param(...)`;
  - в `bloobcat/routes/auth.py` `should_register` теперь true только при `registerIntent=true` или whitelist `start_param` (`family/ref/qr`);
  - в `bloobcat/funcs/validate.py`:
    - referer-fallback больше не читает `utm` как стартовый параметр;
    - создание пользователя допускается только для whitelist `start_param`, иначе `403 User not registered`.
- Эффект:
  - обычное открытие без start-пейлоада больше не может случайно обойти welcome-регистрацию;
  - deep-link сценарии `family/ref/qr` сохранены.
- Тесты:
  - `tests/test_auth_registration_modes.py`: добавлен кейс `campaign-abc` (не whitelist) -> `requires_registration=true`;
  - `tests/test_resilience_hardening.py`: добавлен кейс `validate` с не-whitelist `start_param` -> `403 User not registered`;
  - прогон: `py -3.12 -m pytest tests/test_auth_registration_modes.py tests/test_resilience_hardening.py -q` -> `13 passed`.


### 2026-02-20 ? Directus Content v2 (Premium Ops + readable tables)

- ?????????? redesign `Content` ? hybrid-??????:
  - role-level presets ??? ???????? ????????;
  - ???????? ????????? ?????? `??????? Ops` (`tvpn-content-ops`, `/admin/tvpn-content-ops`);
  - ??????? ????????? cleanup legacy role bookmarks ?? keep-set.
- `scripts/directus_super_setup.py`:
  - nav-group collapse: `grp_main=open`, ????????? ???????? ?????? `closed`;
  - ???????? rollout cleanup user-level presets ?? env-????? `DIRECTUS_CONTENT_UX_CLEAN_USER_PRESETS=1`;
  - ???????? ???? `enable_extension_tvpn_content_ops` ????? role presets;
  - ????????? ??????? default/bookmarks ??? redesign scope ????????? ? self-heal `id` ? tabular/cards.
- `admin-widgets` endpoint ????????:
  - `GET /content-ops/summary`
  - `GET /content-ops/queues`
  - `GET /content-ops/search`
  - ????????? ?????? ??? optional partner-path (??? 500 ??? ?????????? ??????).
- ???????? ?????? `directus/extensions/tvpn-content-ops`:
  - KPI, ???????, ?????????? ?????, launch-grid;
  - route-scoped full-width fix (??? inline mutation shared containers);
  - ?????????? ??????? Graphite + Cyan/Amber, ??????? desktop/mobile.
- ??????:
  - `python -m py_compile scripts/directus_super_setup.py` ? OK
  - `npm run build` ? `directus/extensions/admin-widgets` ? OK
  - `npm run build` ? `directus/extensions/tvpn-content-ops` ? OK

## 2026-03-03

### Governance gate: runtime-visible payment/status hardening continuity

- Minor backend version bump applied per policy:
  - `pyproject.toml`: `0.21.0 -> 0.22.0`.
- Captured invariant set for completed runtime changes:
  - strict Standard vs Family day-counter separation remains authoritative;
  - base purchase while family is active updates frozen base days only, not family expiry;
  - family renewal is additive and must never shorten paid period;
  - `/pay/status` ownership checks are fail-closed;
  - metadata-less `/pay/status` allow-path is valid only when an owned processed row exists;
  - fast fallback remains bounded with `skip_remnawave_sync` and short RemnaWave timeout.
- Verification evidence (continuity record, not re-run in this docs/version batch):
  - backend target suites -> PASS

## 2026-03-06

### Payment create idempotency for first-click fetch retries

- **fix**: `/pay` now accepts a client-provided request identifier and uses it as the YooKassa idempotency key.
  - `bloobcat/routes/payment.py`:
    - added `client_request_id` input handling with fail-closed validation for empty/oversized/invalid values;
    - when present, the normalized request id is copied into payment metadata and used as the YooKassa `idempotence_key`;
    - legacy behavior remains as fallback for callers that do not send the new field.
  - version:
    - `pyproject.toml`: `0.32.0 -> 0.33.0`.
- Verification:
  - `py -3.12 -m pytest tests/test_payments_no_yookassa.py -q` -> `11 passed`
  - `py -3.12 -m py_compile bloobcat/routes/payment.py` -> `PASS`
- Cross-check:
  - `pnpm --dir TelegramVPN exec vitest run src/pages/Welcome/WelcomePage.test.tsx src/hooks/useTvpnUserSync.test.ts src/pages/Subscription/SubscriptionPage.pendingPayment.test.tsx` (workdir `..`) -> `49 passed`
- Residual risk:
  - idempotent reuse now depends on the client re-sending the same `client_request_id`; callers that do not adopt it still fall back to the old random-key behavior.

### 2026-03-06

### Deploy unblock: pay() YooKassa idempotency test monkeypatch parity

- **fix**: hardened `tests/test_payments_no_yookassa.py` so the partial-payment idempotency test patches the same `Payment.create` resolution paths as the already-passing auto-payment partial test.
- Root cause:
  - `test_pay_partial_uses_client_request_id_as_yookassa_idempotence_key` only patched `payment_module.Payment.create`;
  - under the local test stub/import layout, `pay()` could still resolve the default stub from function globals and return `test_payment_id`.
- Change:
  - patched both `payment_module.Payment.create` and `pay.__globals__["Payment"].create` in the failing test.
- Verification:
  - `py -3.12 -m pytest tests/test_payments_no_yookassa.py::test_pay_partial_uses_client_request_id_as_yookassa_idempotence_key -q` -> `1 passed`
  - `py -3.12 -m pytest tests/test_payments_no_yookassa.py -q` -> `11 passed`
  - `py -3.12 -m pytest tests -q` -> `212 passed`
- Notes:
  - runtime `/pay` behavior did not change in this batch; this was a deploy-blocking test harness fix only.

### 2026-03-06

### Directus users delete: family_invites pre-delete cleanup

- **fix**: Directus-side deletion of `users` now removes dependent `family_invites` rows before the actual delete executes.
- Root cause:
  - the remnawave-sync `items.delete` cleanup transaction already handled `active_tariffs`, `notification_marks`, `subscription_freezes`, and `users.referred_by`;
  - it did not clean `family_invites.owner_id`, so deleting a user from Directus could fail on `family_invites_owner_id_fkey` before backend `pre-delete` logic had a chance to run.
- Change:
  - `directus/extensions/hooks/remnawave-sync/index.js`:
    - added schema checks for `family_invites.owner_id`;
    - deletes matching `family_invites` rows inside the same cleanup transaction.
  - `directus/extensions/remnawave-sync/src/index.js` and `directus/extensions/remnawave-sync/dist/index.js`:
    - kept the shipped extension variants aligned with the runtime hook.
  - `tests/test_directus_postgres_reliability_artifacts.py`:
    - added artifact assertions for `family_invites` cleanup;
    - extended the proxy transaction/oversized-id coverage to include `family_invites`.
  - version:
    - `pyproject.toml`: `0.33.0 -> 0.34.0`.
- Verification:
  - `py -3.12 -m pytest tests/test_directus_postgres_reliability_artifacts.py -q` -> `25 passed`
  - `node --check directus/extensions/hooks/remnawave-sync/index.js` -> `PASS`
  - `node --check directus/extensions/remnawave-sync/src/index.js` -> `PASS`
  - `node --check directus/extensions/remnawave-sync/dist/index.js` -> `PASS`
- Residual risk:
  - this closes the current `family_invites.owner_id` blocker; any future Directus-managed tables referencing `users` still need to be added to the same pre-delete cleanup matrix.

### Directus super-setup deploy blocker: remove false `bloobcat_db` missing branch

- **fix**: the auto-deploy workflow no longer aborts FK repair because of a redundant `docker compose config --services` probe.
- Root cause:
  - the workflow had already started and queried `bloobcat_db`, but the later FK repair block re-checked service presence with `docker compose $COMPOSE_ARGS config --services | grep -qx "bloobcat_db"`;
  - inside the remote deploy script this check could false-negative and trigger the `Required DB service 'bloobcat_db' not found in compose config` abort before any real `compose_exec bloobcat_db psql ...` call.
- Change:
  - `auto-deploy.yml`:
    - removed the `HAS_DB_SERVICE` / `config --services` gate from the FK repair block;
    - FK repair now runs directly through `compose_exec bloobcat_db` and still fails closed via `FK_FIX_OK`.
  - `tests/test_directus_postgres_reliability_artifacts.py`:
    - replaced the old artifact assertions with checks that require direct `compose_exec bloobcat_db` usage and forbid the removed missing-service branch.
- Verification:
  - `py -3.12 -m pytest tests/test_directus_postgres_reliability_artifacts.py -q` -> `25 passed`
- Residual risk:
  - a separate log anomaly still showed `GITHUB_EVENT_NAME` as `unknown`; it did not block this deploy path, but should be investigated later if prerelease reinstall gating behaves unexpectedly.

### Auto-deploy follow-up: fix remote-script syntax regression

- **fix**: restored valid shell structure in the remote deploy script and made the FK repair heredoc safe inside YAML.
- Root cause:
  - the previous workflow patch dropped the `else/fi` that closes the `FULL_REINSTALL_ACTIVE` decision block;
  - the nested FK SQL payload still depended on an indented heredoc terminator, which can break when the script is transported through YAML and SSH.
- Change:
  - `auto-deploy.yml`:
    - restored `else ... fi` around `FULL_REINSTALL_ACTIVE`;
    - changed the FK repair payload to `<<-'FK_REPAIR_SQL_PAYLOAD'` and kept the direct `compose_exec bloobcat_db` repair path.
  - `tests/test_directus_postgres_reliability_artifacts.py`:
    - updated the artifact assertion to expect the new `<<-` heredoc contract.
- Verification:
  - `bash -n TVPN_BACK_END/.tmp-remote-deploy-script.sh` -> `PASS`
  - `py -3.12 -m pytest tests/test_directus_postgres_reliability_artifacts.py -q` -> `25 passed`
- Residual risk:
  - the separate `GITHUB_EVENT_NAME=unknown` symptom is still unaddressed here.

### 2026-04-06

### Quiet hours notifications and YooKassa live rollout

- **fix**: introduced Moscow quiet-hours delivery for planned and retry Telegram user notifications while keeping subscription and trial state transitions on their original schedule.
- Scope:
  - `bloobcat/scheduler.py`
  - `bloobcat/tasks/quiet_hours.py`
  - `bloobcat/tasks/quiet_hours_notifications.py`
  - `bloobcat/tasks/subscription_expiring_catchup.py`
  - `bloobcat/tasks/retry_trial_notifications.py`
  - `bloobcat/tasks/retry_trial_extension_notifications.py`
  - `bloobcat/tasks/retry_trial_endings.py`
  - `bloobcat/tasks/cleanup_missed_cancellations.py`
  - `bloobcat/tasks/winback_discounts.py`
  - `bloobcat/bot/notifications/subscription/expiration.py`
  - `bloobcat/bot/notifications/trial/expiring.py`
  - `tests/test_quiet_hours_notifications.py`
  - `pyproject.toml`
- Runtime decisions:
  - added a dedicated quiet-hours helper layer for `00:00-08:00` Moscow time and normalized affected user-facing ETAs to the first allowed `08:00` slot;
  - moved subscription-expiring `3d/2d`, expired-subscription notice, and trial 1-day marketing reminder to the morning window, while keeping noon reminders unchanged;
  - split “change state now / notify user later” for trial endings and auto-renew cancellation after failed charges by reusing `notification_marks` as pending delivery storage instead of adding a new table;
  - night retry passes for missed trial `2h/24h` notifications and missed trial-extension notifications now skip sending and wait for the next post-quiet-hours retry;
  - winback offer creation still happens on the nightly schedule, but user-facing offer delivery is deferred with a pending mark when the run lands inside quiet hours;
  - subscription-expiring copy now uses calendar-day difference instead of seconds-to-midnight math, so the text remains correct after shifting `3d/2d` reminders to the morning;
  - trial “1 day left” copy no longer promises a hard `00:00` send time;
  - version: `0.53.0 -> 0.54.0`.
- Verification:
  - `py -3.12 -m pytest TVPN_BACK_END/tests/test_quiet_hours_notifications.py -q` -> `15 passed`
  - `py -3.12 -m pytest TVPN_BACK_END/tests -q` -> `281 passed, 1 warning`
  - `py -3.12 -m compileall TVPN_BACK_END` -> `PASS`
- Production rollout:
  - pushed commit `aa4dd2f` to `origin/main`;
  - production backend repo `/root/TVPN_BACK_END` now runs commit `aa4dd2f`;
  - rotated production `YOOKASSA_SECRET_KEY` from test to live in server-side `.env` only; the value was not written to git, docs, or logs;
  - `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate bloobcat` -> `PASS`;
  - `curl -fsS http://localhost:33083/health` -> `PASS` (`version=0.54.0`);
  - `curl -fsS https://api.stratavpn.com/health` -> `PASS` (`version=0.54.0`);
  - `curl -sS -o /dev/null -w '%{http_code}' https://api.stratavpn.com/pay/webhook/yookassa/s3cr3t` -> `405` on `GET`, confirming the webhook path still resolves as a POST-only route;
  - runtime check inside the `bloobcat` container confirms that the live YooKassa secret is loaded.
- Notes:
  - no DB migration or schema change was introduced in this batch;
  - local ignored `.env` was intentionally left unchanged.

### 2026-04-06

### YooKassa shop ID rotation to 1314424

- **ops**: updated production `YOOKASSA_SHOP_ID` on the backend VPS from the previous shop ID to `1314424`.
- Runtime notes:
  - the first automated substitution accidentally dropped the `YOOKASSA_SHOP_ID=` line from `/root/TVPN_BACK_END/.env`, which caused `YookassaSettings.shop_id` startup validation to fail;
  - the env line was restored manually, the container was force-recreated, and runtime config was rechecked inside the running `bloobcat` container.
- Verification:
  - backend VPS `.env` now contains `YOOKASSA_SHOP_ID=1314424`
  - `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate bloobcat` -> `PASS`
  - `curl -fsS http://localhost:33083/health` -> `PASS` (`version=0.54.0`)
  - `curl -fsS https://api.stratavpn.com/health` -> `PASS` (`version=0.54.0`)
  - `docker compose ... exec -T bloobcat python ...` runtime check -> `PASS` (`shop_id=1314424`, live YooKassa secret still loaded)
- Outcome:
  - production backend is healthy again after the temporary restart loop
  - YooKassa runtime now uses the new production shop ID `1314424`

### 2026-04-09

### Family quota reservations include owner devices and active invites

- **fix**: unified family quota calculations across `/family/*` and `/user`, so owner-connected devices and active invite reservations are counted immediately instead of only after invite acceptance.
- Scope:
  - `bloobcat/routes/family_quota.py`
  - `bloobcat/routes/family_invites.py`
  - `bloobcat/routes/user.py`
  - `tests/test_user_family_summary.py`
  - `tests/test_family_membership_admin_logs.py`
  - `tests/test_user_recreate_cleanup.py`
  - `pyproject.toml`
- Runtime decisions:
  - added a shared family quota helper that computes:
    - `reservedDevices = ownerConnectedDevices + memberAllocatedDevices + inviteReservedDevices`
    - `availableDevices = max(0, familyLimit - reservedDevices)`
    - `ownerQuotaLimit = max(0, familyLimit - memberAllocatedDevices - inviteReservedDevices)`
  - `create_invite` now rejects requests when the new invite would overflow the reserved family capacity, not just the member allocation sum;
  - owner effective RemnaWave HWID limit is resynced after invite creation and invite revoke so owner quota reflects active invite reservations immediately;
  - `/user` for active family owners now returns `family_owner.active_invites_devices_total`, and both `devices_limit` and `family_owner.owner_remaining_devices` now mean the owner's remaining quota after active members and active invites;
  - full-suite validation exposed test pollution from `tests/test_user_family_summary.py`, where a stubbed `bloobcat.services.subscription_overlay` module stayed in `sys.modules`; module teardown now restores the original module so later tests load the real overlay implementation again;
  - backend version bumped `0.55.0 -> 0.56.0`.
- Verification:
  - `py -3.12 -m py_compile TVPN_BACK_END/bloobcat/routes/family_quota.py TVPN_BACK_END/bloobcat/routes/family_invites.py TVPN_BACK_END/bloobcat/routes/user.py TVPN_BACK_END/tests/test_user_family_summary.py TVPN_BACK_END/tests/test_family_membership_admin_logs.py` -> PASS
  - `py -3.12 -m pytest TVPN_BACK_END/tests/test_user_family_summary.py -q` -> PASS
  - `py -3.12 -m pytest TVPN_BACK_END/tests/test_family_membership_admin_logs.py -q` -> PASS
  - `py -3.12 -m pytest TVPN_BACK_END/tests/test_user_recreate_cleanup.py -q` -> PASS (`3 passed, 1 warning`)
  - `py -3.12 -m pytest TVPN_BACK_END/tests -q` -> PASS (`288 passed, 1 warning`)
  - `py -3.12 -m compileall -q TVPN_BACK_END` -> PASS
- Notes:
  - no DB schema change or migration was needed; the change stays within route logic, API payload semantics, and tests.

### 2026-04-09

### Production rollout for family quota backend `0.56.0`

- **ops**: rolled the backend family quota changes to production on `72.56.235.167`.
- Runtime notes:
  - uploaded runtime files only:
    - `bloobcat/routes/family_quota.py`
    - `bloobcat/routes/family_invites.py`
    - `bloobcat/routes/user.py`
    - `pyproject.toml`
  - created backups under `/root/TVPN_BACK_END/.deploy-backups/20260409-0248/`;
  - rebuilt and restarted only the `bloobcat` service via `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build bloobcat`;
  - no database migration or Directus restart was needed.
- Verification:
  - backend VPS `docker compose -f docker-compose.yml -f docker-compose.prod.yml ps bloobcat` -> `PASS`
  - backend VPS `curl -fsS http://127.0.0.1:33083/health` -> `PASS` (`version=0.56.0`)
  - external `curl.exe -sS https://api.stratavpn.com/health` -> `PASS` (`version=0.56.0`)

### 2026-04-09

### Auto-payment evening reminders before scheduled auto-renew attempts

- **feat**: added user-facing Telegram reminders on the evening before each scheduled auto-renew attempt while keeping the actual billing schedule unchanged.
- Scope:
  - `bloobcat/routes/payment.py`
  - `bloobcat/bot/notifications/subscription/expiration.py`
  - `bloobcat/tasks/auto_payment_reminders.py`
  - `bloobcat/scheduler.py`
  - `bloobcat/db/users.py`
  - `tests/test_quiet_hours_notifications.py`
  - `tests/test_payments_no_yookassa.py`
  - `pyproject.toml`
- Runtime decisions:
  - added `AutoPaymentPreview` and `build_auto_payment_preview(...)` so reminder copy and the real `create_auto_payment(...)` path share the same source of truth for:
    - `months`
    - `device_count`
    - `total_amount`
    - `amount_external`
    - `amount_from_balance`
    - `discount_percent`
    - `lte_gb_total`
    - `lte_cost`
  - `notify_auto_payment(...)` now accepts optional explicit quote fields for reminder messages and keeps legacy fallback behavior for older call sites;
  - scheduled reminder tasks for every active auto-renew attempt (`4d`, `3d`, `2d`) at `20:00 MSK` on the previous evening;
  - added `NotificationMarks` type `auto_payment_reminder` and an evening catch-up dispatcher (`600s`) that retries only inside the same `20:00-23:59 MSK` window and skips stale sends after midnight;
  - fixed the unrelated but real `notify_expiring_subscription` bug where `user_expired_at` was referenced before definition;
  - fixed a stale-freeze recreate edge case by allowing full scrub fallback only for the new-user recreate path, while preserving strict `created_at` boundary behavior in the generic helper;
  - backend version bump: `0.56.0 -> 0.57.0`.
- Verification:
  - `py -3.12 -m pytest TVPN_BACK_END/tests/test_quiet_hours_notifications.py -q` -> `PASS` (`19 passed, 1 warning`)
  - `py -3.12 -m pytest TVPN_BACK_END/tests/test_payments_no_yookassa.py -q` -> `PASS` (`25 passed, 1 warning`)
  - `py -3.12 -m pytest TVPN_BACK_END/tests/test_user_recreate_cleanup.py -q` -> `PASS` (`3 passed, 1 warning`)
  - `py -3.12 -m pytest TVPN_BACK_END/tests -q` -> `PASS` (`299 passed, 1 warning`)
  - `py -3.12 -m compileall TVPN_BACK_END` -> `PASS`
- Notes:
  - no migration was needed;
  - no public API contract changed;
  - this entry covers local implementation and validation only, not production rollout.

### 2026-04-09

### Rescue app link in expiring and ended access notifications

- **feat**: appended the external rescue URL `https://app.stratavpn.com` to targeted subscription/trial Telegram notifications so users can reach the service even if Telegram is unavailable without VPN.
- Scope:
  - `bloobcat/bot/notifications/rescue_link.py`
  - `bloobcat/bot/notifications/subscription/expiration.py`
  - `bloobcat/bot/notifications/subscription/key.py`
  - `bloobcat/bot/notifications/trial/expiring.py`
  - `bloobcat/bot/notifications/trial/end.py`
  - `bloobcat/bot/notifications/trial/pre_expiring_3d.py`
  - `tests/test_payments_no_yookassa.py`
  - `pyproject.toml`
- Runtime decisions:
  - introduced a shared rescue-link helper with fixed RU/EN copy and the fixed external host instead of reusing `telegram_settings.webapp_url` or `miniapp_url`;
  - appended the rescue paragraph only to the targeted user-facing notifications:
    - subscription expiring (`7d`, `3d`, `1d`)
    - subscription expired
    - trial expiring tonight
    - trial ended
    - trial marketing reminder (`notify_trial_three_days_left`)
  - kept existing inline buttons and routes unchanged;
  - preserved `parse_mode="HTML"` in the trial marketing reminder and verified the appended plain-text URL remains safe under HTML mode;
  - backend version bump: `0.57.0 -> 0.58.0`.
- Verification:
  - `py -3.12 -m pytest TVPN_BACK_END/tests/test_payments_no_yookassa.py -q` -> `PASS` (`33 passed, 1 warning`)
  - `py -3.12 -m pytest TVPN_BACK_END/tests -q` -> `PASS` (`307 passed, 1 warning`)
  - `py -3.12 -m compileall TVPN_BACK_END` -> `PASS`
- Notes:
  - no migration, env change, or public API change was needed;
  - scheduler and quiet-hours behavior remained untouched because only notification copy changed.

### 2026-04-09

### Family invite join-flow: authenticated preview modes and safe member switching

- **fix**: strengthened family invite handling so the backend is now the source of truth for self-invite rejection, owner-blocked joins, same-family idempotency, and member-to-member family transitions.
- Scope:
  - `bloobcat/routes/family_invites.py`
  - `tests/test_family_invite_join_flow.py`
  - `pyproject.toml`
- Runtime decisions:
  - added `_InviteJoinContext` plus the shared `_resolve_invite_join_context(...)` helper to derive one of the fixed join modes:
    - `self_invite`
    - `owner_blocked`
    - `already_in_same_family`
    - `join_ready`
    - `switch_family_ready`
    - `switch_family_cleanup_required`
  - kept the existing public `GET /family/invites/{token}` validator for token existence/expiry and added a new authenticated preview route `GET /family/invites/{token}/preview` that returns:
    - invite owner
    - invite allocation
    - current family owner
    - current family allocation
    - connected device count
    - required device cleanup count
    - resolved join mode
  - `POST /family/invites/{token}/accept` now:
    - rejects self-invite
    - rejects a family owner joining another family
    - returns an idempotent success for same-family accepts
    - rejects over-limit transitions with `Device cleanup required`
    - allows active member-to-member switching when device count fits the new invite allocation
  - successful member switching now:
    - removes the old membership
    - creates or reactivates the new membership
    - resyncs the old owner, new owner, and member HWID limits
    - records audit entries and keeps notification hooks intact
  - failure cleanup now restores the previous member state as far as possible, including the old membership and prior limit, without introducing a new DB schema or migration;
  - backend version bumped `0.58.0 -> 0.59.0`.
- Verification:
  - `py -3.12 -m pytest TVPN_BACK_END/tests/test_family_invite_join_flow.py -q` -> PASS (`6 passed`)
  - `py -3.12 -m pytest TVPN_BACK_END/tests -q` -> PASS (`313 passed, 1 warning`)
  - `py -3.12 -m compileall TVPN_BACK_END` -> PASS
- Residual risk:
  - the transition path is now regression-tested and includes explicit rollback logic, but it still spans multiple side effects without a single transaction boundary; the strongest remaining check is a production smoke for a real cross-family member switch.

### 2026-04-16

### User-aware subscription plans for promo discounts

- **feat/fix**: made `/subscription/plans` user-aware so the frontend can render promo-backed payable prices immediately after redeem, without duplicating discount rules in the client.
- Scope:
  - `bloobcat/routes/subscription.py`
  - `tests/test_subscription_plans_discount_pricing.py`
  - `pyproject.toml`
- Runtime decisions:
  - `/subscription/plans` now requires the same authenticated user context as the rest of the subscription surface and passes that user into plan-building;
  - each returned plan now keeps:
    - `priceRub` as the current payable amount for this user;
    - `originalPriceRub` only when a personal promo discount actually reduces the payable amount;
    - `personalDiscountPercent` only when such a discount applies;
  - tariff value badge `discountText` remains based on the original tariff card price relative to the one-month plan, not on the discounted payable amount, so promo discounts do not distort the plan-comparison badge;
  - reading `/subscription/plans` applies `apply_personal_discount(...)` in read-only mode and does not consume `remaining_uses`;
  - backend version bumped `0.59.0 -> 0.60.0`.
- Verification:
  - `rtk .venv/bin/python -m pytest tests/test_subscription_plans_discount_pricing.py -q` -> `PASS` (`4 passed`)
  - `rtk .venv/bin/python -m py_compile bloobcat/routes/subscription.py bloobcat/routes/promo.py` -> `PASS`
- Residual risk:
  - callers that used `/subscription/plans` without a valid authenticated user context must now satisfy the same auth contract as the rest of the subscription API;
  - manual product smoke is still needed to verify parity between the refreshed card prices and the next payment step in the live Mini App flow.

### 2026-04-16

### Subscription pricing test override hardening

- **test/ci**: hardened the new subscription pricing test harness after the first production CI run failed before deploy on the full backend suite.
- Scope:
  - `tests/test_subscription_plans_discount_pricing.py`
  - `memory-base/log.md`
- Decisions:
  - `_build_app_for_user(...)` now overrides the exact `validate` dependency objects referenced inside `bloobcat.routes.subscription` and `bloobcat.routes.promo`, instead of importing `validate` from `bloobcat.funcs.validate` separately;
  - this avoids object-identity drift in the full test process where older tests temporarily replace `bloobcat.funcs.validate` inside `sys.modules`;
  - runtime subscription logic and HTTP auth contract were left unchanged; only the test harness was corrected.
- Verification:
  - `rtk .venv/bin/python -m pytest tests/test_subscription_plans_discount_pricing.py -q` -> `PASS` (`4 passed`)
  - `rtk .venv/bin/python -m pytest tests -q` -> `PASS` (`317 passed`)
- Residual risk:
  - the backend deploy workflow still needs a second push/run because the first CI attempt failed before the SSH deploy stage;
  - the new tests now match the existing router-bound dependency pattern, but any future test that hot-swaps route modules through `sys.modules` should keep object identity in mind when using `dependency_overrides`.

### 2026-04-21

### Winback markdown retry regression hardening

- **fix**: removed Markdown-dependent formatting from the winback offer notification so user names with Telegram markdown metacharacters no longer trigger terminal `TelegramBadRequest` parse failures and infinite retry churn for pending winback marks.
- Scope:
  - `bloobcat/bot/notifications/winback/discount_offer.py`
  - `tests/test_payments_no_yookassa.py`
  - `tests/test_quiet_hours_notifications.py`
  - `pyproject.toml`
- Runtime decisions:
  - `notify_winback_discount_offer(...)` now sends plain text without `parse_mode="Markdown"` and no longer injects Markdown-only emphasis into the English offer body;
  - the existing retry contract remains intact: generic `False` delivery results still keep the pending winback mark for later retry, while blocked/missing-user cleanup behavior remains unchanged;
  - backend version bumped `0.64.0 -> 0.65.0`.
- Verification:
  - `rtk env PYTHONPATH=. '/Users/waizor/Projects/active/vpn/Strata Project/Strata_Backend/.venv/bin/python' -m pytest tests/test_payments_no_yookassa.py -q` -> `PASS` (`34 passed`)
  - `rtk env PYTHONPATH=. '/Users/waizor/Projects/active/vpn/Strata Project/Strata_Backend/.venv/bin/python' -m pytest tests/test_quiet_hours_notifications.py -q` -> `PASS` (`26 passed`)
  - `rtk env PYTHONPATH=. '/Users/waizor/Projects/active/vpn/Strata Project/Strata_Backend/.venv/bin/python' -m py_compile bloobcat/bot/notifications/winback/discount_offer.py` -> `PASS`
  - `rtk env PYTHONPATH=. '/Users/waizor/Projects/active/vpn/Strata Project/Strata_Backend/.venv/bin/python' -m pytest tests -q` -> `PASS` (`340 passed`)
- Residual risk:
  - this batch intentionally hardens only the winback notification path called out by Codex review; other Telegram messages that still rely on Markdown formatting are unchanged;
  - final production confirmation still requires the merge-to-`main` deploy path and post-deploy smoke on the live winback/Directus flows.

## 2026-04-24 (Vectra brand naming pass)

- **fix/brand**: replaced legacy Strata/Triad/Bloop/Bloob user-facing naming with `Vectra` / `Vectra Connect` across backend-facing user copy and admin surfaces.
- Scope:
  - Telegram `/start` text;
  - trial/family/subscription/referral notification copy;
  - welcome-VPN payload title, server remark, bot/support fallback URLs and announce text;
  - family/referral/partner bot-name fallbacks;
  - Directus `admin-widgets`, `tvpn-home`, `tvpn-promo-studio` source + rebuilt dist bundles;
  - pgAdmin display name, migration/readme/docstrings and promo-code default prefix (`VECTRA`);
  - backend version `0.72.0 -> 0.73.0`.
- Compatibility decisions:
  - Python package, service/container names and import paths containing `bloobcat` remain unchanged to preserve runtime contracts;
  - production `*.stratavpn.com` domains and rescue-link URLs remain unchanged until DNS/deploy/bot aliases are formally migrated.
- Verification:
  - active-source old-brand scan -> `PASS` for exact legacy product names;
  - Directus builds: `admin-widgets`, `tvpn-home`, `tvpn-promo-studio` -> `PASS`;
  - `PYTHONPATH=Vectra_Backend Vectra_Backend/.venv/bin/python -m py_compile <touched backend python files>` -> `PASS`;
  - `PYTHONPATH=Vectra_Backend Vectra_Backend/.venv/bin/python -m pytest Vectra_Backend/tests/test_user_family_summary.py Vectra_Backend/tests/test_payments_no_yookassa.py -q` -> `PASS` (`40` tests).

### 2026-04-25

### LTE re-enabled for tariff constructor defaults

- **fix/subscription**: restored LTE availability as the default for the new tariff constructor after it had been disabled in tariff seed/config.
- Scope:
  - `bloobcat/db/tariff.py`
  - `bloobcat/services/subscription_limits.py`
  - `bloobcat/services/admin_integration.py`
  - `bloobcat/settings.py`
  - `scripts/seed_tariffs.py`
  - `migrations/models/96_20260425143000_reenable_lte_for_tariff_builder.py`
  - `directus/extensions/tvpn-tariff-studio/**`
  - `directus/extensions/remnawave-sync/**`
  - `tests/test_subscription_plans_discount_pricing.py`
  - backend version `0.74.0 -> 0.75.0`
- Runtime decisions:
  - default `Tariffs` rows now have `lte_enabled=True` and `lte_price_per_gb=1.5`;
  - seed for `1/3/6/12` month tariffs now creates LTE-enabled offers with max `500 GB` and step `1 GB`;
  - migration `96_20260425143000_reenable_lte_for_tariff_builder.py` re-enables LTE for existing duration tariffs and restores missing LTE price/max/step defaults;
  - Directus Tariff Studio treats LTE as enabled by default and supports fractional LTE price input (`step=0.1`);
  - `remnawave-sync` now carries computed `lte_enabled` and `lte_price_per_gb` back into Directus payloads alongside derived pricing internals.
- Verification:
  - `PYTHONPATH=Vectra_Backend Vectra_Backend/.venv/bin/python -m py_compile Vectra_Backend/bloobcat/settings.py Vectra_Backend/bloobcat/services/subscription_limits.py Vectra_Backend/bloobcat/db/tariff.py Vectra_Backend/bloobcat/services/admin_integration.py Vectra_Backend/scripts/seed_tariffs.py Vectra_Backend/migrations/models/96_20260425143000_reenable_lte_for_tariff_builder.py` -> `PASS`
  - `/opt/homebrew/opt/node@24/bin/node --check Vectra_Backend/directus/extensions/remnawave-sync/src/index.js && /opt/homebrew/opt/node@24/bin/node --check Vectra_Backend/directus/extensions/remnawave-sync/dist/index.js && /opt/homebrew/opt/node@24/bin/node --check Vectra_Backend/directus/extensions/hooks/remnawave-sync/index.js && /opt/homebrew/opt/node@24/bin/node --check Vectra_Backend/directus/extensions/tvpn-tariff-studio/src/index.js && /opt/homebrew/opt/node@24/bin/node --check Vectra_Backend/directus/extensions/tvpn-tariff-studio/dist/index.js` -> `PASS`
  - `cd Vectra_Backend/directus/extensions/tvpn-tariff-studio && PATH=/opt/homebrew/opt/node@24/bin:$PATH npm ci && PATH=/opt/homebrew/opt/node@24/bin:$PATH npm run build` -> `PASS` (audit findings inherited from Directus extension toolchain); `node_modules` removed after build.
  - `PYTHONPATH=Vectra_Backend Vectra_Backend/.venv/bin/python -m pytest Vectra_Backend/tests/test_subscription_plans_discount_pricing.py Vectra_Backend/tests/test_payments_no_yookassa.py Vectra_Backend/tests/test_lte_usage_limiter.py -q` -> `PASS` (`43 passed`)
- Residual risk:
  - existing active subscribers are not force-granted new `lte_gb_total` quota by this patch; do that separately if required, because it must be paired with RemnaWave LTE-squad reconciliation.

### Subscription quote user-facing copy cleanup

- **fix(subscription)**: removed backend wording from the public `/subscription/quote` copy returned to the Mini App.
- Scope:
  - `bloobcat/routes/subscription.py`
  - `tests/test_subscription_plans_discount_pricing.py`
  - backend version `0.76.0 -> 0.77.0`
- Runtime decision:
  - quote copy is now `Стоимость обновлена и будет проверена перед оплатой`, keeping source-of-truth validation while avoiding admin/developer wording in the customer UI.
- Verification:
  - `PYTHONPATH=Vectra_Backend Vectra_Backend/.venv/bin/python -m py_compile Vectra_Backend/bloobcat/routes/subscription.py` -> `PASS`
  - `PYTHONPATH=Vectra_Backend Vectra_Backend/.venv/bin/python -m pytest Vectra_Backend/tests/test_subscription_plans_discount_pricing.py -q` -> `PASS` (`7 passed`)
