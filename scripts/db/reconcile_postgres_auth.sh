#!/bin/sh

set -eu

log() {
  printf '%s\n' "[reconcile-postgres-auth] $*"
}

fail() {
  printf '%s\n' "[reconcile-postgres-auth] ERROR: $*" >&2
  exit "${2:-1}"
}

trim_token() {
  value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"

  printf '%s' "$value"
}

require_non_empty_env() {
  var_name="$1"
  eval "var_value=\${$var_name-}"
  if [ -z "${var_value}" ]; then
    fail "Required env var is missing or empty: ${var_name}" 2
  fi
}

resolve_project_root() {
  script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
  CDPATH= cd -- "$script_dir/../.." && pwd
}

PROJECT_ROOT="$(resolve_project_root)"

DB_SERVICE="${DB_SERVICE:-bloobcat_db}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-postgres}"

require_non_empty_env POSTGRES_PASSWORD

case "$DB_USER" in
  '')
    fail "DB_USER is required" 2
    ;;
esac

if ! printf '%s\n' "$DB_USER" | grep -Eq '^[A-Za-z_][A-Za-z0-9_]*$'; then
  fail "DB_USER must match ^[A-Za-z_][A-Za-z0-9_]*$" 2
fi

if ! command -v docker >/dev/null 2>&1; then
  fail "docker is not installed or not in PATH" 5
fi

if ! docker compose version >/dev/null 2>&1; then
  fail "docker compose is unavailable" 5
fi

if [ -n "${COMPOSE_FILES:-}" ]; then
  compose_files_csv="$COMPOSE_FILES"
else
  if [ -f "$PROJECT_ROOT/docker-compose.prod.yml" ]; then
    compose_files_csv="docker-compose.yml,docker-compose.prod.yml"
  else
    compose_files_csv="docker-compose.yml"
  fi
fi

OLD_IFS="$IFS"
IFS=','
set -- $compose_files_csv
IFS="$OLD_IFS"

COMPOSE_FILE_LIST=""
for compose_file_raw in "$@"; do
  compose_file="$(trim_token "$compose_file_raw")"
  [ -n "$compose_file" ] || continue
  case "$compose_file" in
    /*) compose_path="$compose_file" ;;
    *) compose_path="$PROJECT_ROOT/$compose_file" ;;
  esac

  if [ ! -f "$compose_path" ]; then
    fail "Compose file not found: $compose_path" 2
  fi

  COMPOSE_FILE_LIST="${COMPOSE_FILE_LIST}
$compose_path"
done

if [ -z "$COMPOSE_FILE_LIST" ]; then
  fail "No valid compose files were resolved" 2
fi

run_compose() {
  compose_user_args=""
  for arg in "$@"; do
    compose_user_args="${compose_user_args}
$arg"
  done

  set --
  compose_old_ifs="$IFS"
  IFS='
'
  for compose_path in $COMPOSE_FILE_LIST; do
    [ -n "$compose_path" ] || continue
    set -- "$@" -f "$compose_path"
  done

  for arg in $compose_user_args; do
    [ -n "$arg" ] || continue
    set -- "$@" "$arg"
  done
  IFS="$compose_old_ifs"

  docker compose "$@"
}

log "Reconciling postgres password for role '$DB_USER'"
escaped_postgres_password=$(printf '%s' "$POSTGRES_PASSWORD" | sed "s/'/''/g")
if ! run_compose exec -T "$DB_SERVICE" \
  psql -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 \
  -c "ALTER ROLE $DB_USER WITH PASSWORD '$escaped_postgres_password';" >/dev/null </dev/null; then
  fail "ALTER ROLE failed inside service '$DB_SERVICE'" 3
fi

log "Probing TCP password authentication (select 1)"
if ! run_compose exec -T "$DB_SERVICE" sh -eu -c \
  'PGPASSWORD="$1" psql -h 127.0.0.1 -U "$2" -d "$3" -v ON_ERROR_STOP=1 -tA -c "select 1" >/dev/null' \
  sh "$POSTGRES_PASSWORD" "$DB_USER" "$DB_NAME" </dev/null; then
  fail "TCP password auth probe failed" 4
fi

log "Postgres credential reconciliation completed successfully"
