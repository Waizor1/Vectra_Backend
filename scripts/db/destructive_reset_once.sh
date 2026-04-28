#!/bin/sh

set -eu

log() {
  printf '%s\n' "[destructive-reset-once] $*"
}

warn() {
  printf '%s\n' "[destructive-reset-once] WARNING: $*" >&2
}

fail() {
  printf '%s\n' "[destructive-reset-once] ERROR: $*" >&2
  exit "${2:-1}"
}

trim_token() {
  value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

resolve_project_root() {
  script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
  CDPATH= cd -- "$script_dir/../.." && pwd
}

PROJECT_ROOT="$(resolve_project_root)"

DB_SERVICE="${DB_SERVICE:-bloobcat_db}"
DB_VOLUME_NAME="${DB_VOLUME_NAME:-bloobcat-db-volume}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-postgres}"
FOLLOWUP_SERVICES="${FOLLOWUP_SERVICES:-bloobcat directus}"
EXPECTED_ACK_TOKEN="${DESTRUCTIVE_RESET_ACK_TOKEN:-RESET_DB_VOLUME_ONCE}"
DRY_RUN_ENABLED="${DRY_RUN:-false}"

state_root=""
if [ -n "${XDG_STATE_HOME:-}" ]; then
  state_root="$XDG_STATE_HOME/tvpn"
elif [ -n "${HOME:-}" ]; then
  state_root="$HOME/.local/state/tvpn"
else
  state_root="/var/tmp/tvpn"
fi

MARKER_PATH_INPUT="${DESTRUCTIVE_RESET_MARKER_PATH:-$state_root/destructive_db_reset_once.marker}"
case "$MARKER_PATH_INPUT" in
  /*) MARKER_PATH="$MARKER_PATH_INPUT" ;;
  *) MARKER_PATH="$PROJECT_ROOT/$MARKER_PATH_INPUT" ;;
esac

LOCK_PATH_INPUT="${DESTRUCTIVE_RESET_LOCK_PATH:-$state_root/destructive_db_reset_once.lock}"
case "$LOCK_PATH_INPUT" in
  /*) LOCK_PATH="$LOCK_PATH_INPUT" ;;
  *) LOCK_PATH="$PROJECT_ROOT/$LOCK_PATH_INPUT" ;;
esac

case "$DRY_RUN_ENABLED" in
  true|false) ;;
  *) fail "DRY_RUN must be either 'true' or 'false'" 10 ;;
esac

if [ -z "${COMPOSE_PROJECT_NAME:-}" ]; then
  fail "COMPOSE_PROJECT_NAME is required for destructive volume scoping" 10
fi

case "$DB_VOLUME_NAME" in
  ''|*[!A-Za-z0-9_.-]* )
    fail "DB_VOLUME_NAME must contain only [A-Za-z0-9_.-]" 10
    ;;
esac

if [ "$DRY_RUN_ENABLED" != "true" ]; then
  if [ "${ALLOW_DESTRUCTIVE_DB_RESET_ONCE:-}" != "true" ]; then
    fail "Refusing destructive DB reset. Set ALLOW_DESTRUCTIVE_DB_RESET_ONCE=true to continue." 10
  fi

  if [ "${DESTRUCTIVE_RESET_ACK:-}" != "$EXPECTED_ACK_TOKEN" ]; then
    fail "Refusing destructive DB reset. Set DESTRUCTIVE_RESET_ACK=$EXPECTED_ACK_TOKEN to continue." 10
  fi
else
  log "DRY_RUN=true: destructive actions will not execute"
fi

if ! command -v docker >/dev/null 2>&1; then
  fail "docker is not installed or not in PATH" 12
fi

if ! docker compose version >/dev/null 2>&1; then
  fail "docker compose is unavailable" 12
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
    fail "Compose file not found: $compose_path" 12
  fi

  COMPOSE_FILE_LIST="${COMPOSE_FILE_LIST}
$compose_path"
done

if [ -z "$COMPOSE_FILE_LIST" ]; then
  fail "No valid compose files were resolved" 12
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

LOCK_HELD="false"
cleanup_lock() {
  if [ "$LOCK_HELD" = "true" ]; then
    rm -rf "$LOCK_PATH" >/dev/null 2>&1 || true
  fi
}

lock_dir_parent="$(dirname -- "$LOCK_PATH")"
mkdir -p "$lock_dir_parent"
if ! mkdir "$LOCK_PATH" 2>/dev/null; then
  fail "Could not acquire lock: $LOCK_PATH (another reset may be running)" 11
fi
LOCK_HELD="true"
trap 'cleanup_lock' EXIT INT TERM HUP

if [ -f "$MARKER_PATH" ]; then
  fail "One-time marker already exists: $MARKER_PATH" 11
fi

{
  printf 'pid=%s\n' "$$"
  printf 'acquired_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"
} > "$LOCK_PATH/metadata"

log "Attempting best-effort logical backup before destructive reset"
umask 077
BACKUP_DIR_INPUT="${DB_RESET_BACKUP_DIR:-$PROJECT_ROOT/backups/db-reset}"
case "$BACKUP_DIR_INPUT" in
  /*) BACKUP_DIR="$BACKUP_DIR_INPUT" ;;
  *) BACKUP_DIR="$PROJECT_ROOT/$BACKUP_DIR_INPUT" ;;
esac
mkdir -p "$BACKUP_DIR" || warn "Cannot create backup directory: $BACKUP_DIR"

backup_ts="$(date -u +%Y%m%dT%H%M%SZ 2>/dev/null || date +%Y%m%dT%H%M%SZ)"
backup_file="$BACKUP_DIR/postgres_pre_reset_${backup_ts}.sql"

if [ "$DRY_RUN_ENABLED" = "true" ]; then
  log "[dry-run] Would run logical backup for $DB_SERVICE to $backup_file"
else
  if run_compose up -d "$DB_SERVICE" >/dev/null 2>&1 && \
    run_compose exec -T "$DB_SERVICE" sh -eu -c \
      'pg_dump -U "$1" -d "$2" --clean --if-exists --no-owner --no-privileges' \
      sh "$DB_USER" "$DB_NAME" > "$backup_file"; then
    log "Backup completed: $backup_file"
  else
    warn "Backup attempt failed; continuing by operator intent"
    rm -f "$backup_file" >/dev/null 2>&1 || true
  fi
fi

volume_candidates="$(docker volume ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" --filter "label=com.docker.compose.volume=$DB_VOLUME_NAME")"

volume_count=0
target_volume=""
volume_old_ifs="$IFS"
IFS='
'
for volume_name in $volume_candidates; do
  [ -n "$volume_name" ] || continue
  volume_count=$((volume_count + 1))
  target_volume="$volume_name"
done
IFS="$volume_old_ifs"

if [ "$volume_count" -eq 0 ]; then
  fail "No scoped DB volume found for project '$COMPOSE_PROJECT_NAME' and volume '$DB_VOLUME_NAME'" 13
fi

if [ "$volume_count" -gt 1 ]; then
  fail "Multiple scoped DB volumes found; refusing destructive removal due to ambiguity" 14
fi

log "Stopping DB service before volume removal"
if [ "$DRY_RUN_ENABLED" = "true" ]; then
  log "[dry-run] Would stop and remove service: $DB_SERVICE"
else
  run_compose stop "$DB_SERVICE" >/dev/null 2>&1 || true
  run_compose rm -f -s "$DB_SERVICE" >/dev/null 2>&1 || true
fi

if [ "$DRY_RUN_ENABLED" = "true" ]; then
  log "[dry-run] Would remove DB volume: $target_volume"
else
  log "Removing DB volume: $target_volume"
  if ! docker volume rm "$target_volume" >/dev/null; then
    fail "Failed to remove volume: $target_volume" 15
  fi
fi

available_services="$(run_compose config --services 2>/dev/null || true)"
services_to_up="$DB_SERVICE"
for candidate in $FOLLOWUP_SERVICES; do
  for existing in $available_services; do
    if [ "$candidate" = "$existing" ]; then
      services_to_up="$services_to_up $candidate"
      break
    fi
  done
done

log "Recreating DB and selected services"
if [ "$DRY_RUN_ENABLED" = "true" ]; then
  log "[dry-run] Would recreate services: $services_to_up"
else
  # shellcheck disable=SC2086
  if ! run_compose up -d $services_to_up; then
    fail "Failed to recreate services: $services_to_up" 16
  fi
fi

marker_dir="$(dirname -- "$MARKER_PATH")"
if [ "$DRY_RUN_ENABLED" = "true" ]; then
  log "[dry-run] Would write one-time marker to $MARKER_PATH"
  log "Dry-run completed; no destructive changes were applied"
else
  mkdir -p "$marker_dir"
  tmp_marker="$marker_dir/.destructive_db_reset_once.marker.$$"
  {
    printf 'created_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"
    printf 'db_service=%s\n' "$DB_SERVICE"
    printf 'db_volume_name=%s\n' "$DB_VOLUME_NAME"
    printf 'compose_project_name=%s\n' "$COMPOSE_PROJECT_NAME"
    printf 'compose_files=%s\n' "$compose_files_csv"
  } > "$tmp_marker"
  mv -f "$tmp_marker" "$MARKER_PATH"
  log "One-time destructive reset completed; marker written to $MARKER_PATH"
fi
