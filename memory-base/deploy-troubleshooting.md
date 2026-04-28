# Ошибки деплоя (Auto Deploy Backend)

## Где смотреть ошибку

- **GitHub Actions**: репозиторий Vectra_Backend → вкладка **Actions** → последний failed run → шаг, на котором упало (красный крестик).
- Скопируй текст ошибки из лога шага или приложи скрин — по нему можно точно указать причину.

### Политика prerelease full reinstall (стабилизированный baseline)

- **Опорный успешный прогон**: backend deploy run `22546902029`.
- **Базовый default**: `PRERELEASE_FULL_REINSTALL=false`.
- **Политика по умолчанию**: deploy работает в non-destructive режиме — **без reset/reinstall по умолчанию**.
- **Когда допускается full reinstall**: только как incident-driven opt-in для prerelease (временное ручное включение при подтвержденном инциденте).
- **Тумблер**: repository variable `PRERELEASE_FULL_REINSTALL` (Settings → Secrets and variables → Actions → Variables).
- **Strict-значение для включения**: только точное строковое значение `true`; любые другие значения считаются `false`.
- **Условия активации (все одновременно)**:
  1. событие workflow = `push`;
  2. ветка деплоя = `main`;
  3. `PRERELEASE_FULL_REINSTALL` строго равно `true`.
- **Что делает при активации**:
  - запускает `docker compose down -v --remove-orphans`;
  - дополнительно удаляет docker volumes с label текущего compose-проекта (`com.docker.compose.project=<project>`).
- **Ключевые маркеры в логах**:
  - `🧪 PRERELEASE FULL REINSTALL MODE: ACTIVE (event=push, branch=main, toggle=true)`
  - `🧹 Running prerelease full reinstall for compose project '...'`
  - `🗑️  Removing project volume: ...` (если есть volume для удаления)
  - `ℹ️  No project-scoped volumes found for '...'` (если удалять нечего)
  - если режим не включился: `ℹ️  PRERELEASE FULL REINSTALL MODE: INACTIVE (...)` с причиной.
- **Возврат к baseline после инцидента**: выставить `PRERELEASE_FULL_REINSTALL=false` и выполнять последующие деплои в стандартном non-destructive режиме.

---

### A. **Process completed with exit code 137** (частая причина падения)

- **Что значит**: процесс был принудительно завершён (сигнал SIGKILL, 137 = 128 + 9). Обычно это **нехватка памяти (OOM)** на сервере или реже — таймаут/ограничение ресурсов.
- **Где происходит**: код 137 приходит с **VPS** (внутри шага "Deploy to server"): чаще всего падает `docker compose up -d --build` или один из контейнеров (сборка образа съедает много RAM).
- **Что сделать**:
  1. **На VPS** проверить память и OOM:
     - `free -h` — сколько RAM и swap;
     - `dmesg | tail -100` или `journalctl -k -b | grep -i oom` — был ли убийца процессов по памяти.
  2. **Увеличить swap** на сервере, если мало RAM (например 2–4 GB swap), чтобы сборка не убивалась.
  3. **Снизить потребление при сборке**: в `docker-compose` или Dockerfile по возможности использовать multi-stage build, не тянуть лишние зависимости; при необходимости собирать образ с ограничением параллелизма (например `DOCKER_BUILDKIT=1 docker compose build --parallel 1`).
  4. **Проверить**, не падает ли конкретный сервис (например Directus или bloobcat) при старте — тогда смотреть логи контейнера и исправлять конфиг/код.
- **Временный обход (только при инциденте, reconcile-first)**:
  1. **ВНИМАНИЕ (destructive):** `git reset --hard` и `git clean -ffd` необратимо удаляют локальные изменения и неотслеживаемые файлы.
  2. **Preflight (обязательно перед destructive-шагами):**
     - проверить, что вы в нужном репозитории: `git rev-parse --is-inside-work-tree >/dev/null`;
     - посмотреть текущее состояние: `git status --short`;
     - запускать destructive sync только после ручного подтверждения оператора.
  3. Детерминированный git-sync (под explicit ack):
     - `test "${TVPN_GIT_SYNC_ACK:-}" = "YES" || { echo "Refusing destructive git sync: set TVPN_GIT_SYNC_ACK=YES after manual review"; exit 1; } && git fetch --prune origin <branch> && git rev-parse --verify --quiet "origin/<branch>^{commit}" >/dev/null && git checkout -f -B <branch> origin/<branch> && git reset --hard origin/<branch> && git clean -ffd && test -z "$(git status --porcelain --untracked-files=no)" && test "$(git rev-parse HEAD)" = "$(git rev-parse origin/<branch>)"`.
  4. Дальше соблюдать текущий стабильный порядок deploy (reconcile-first): поднять `bloobcat_db` → выполнить `reconcile_postgres_auth.sh` (без передачи секретов в CLI) → затем запускать полный `docker compose ... up -d --build`.
  Во время сборки следить за `free -h` и логами контейнеров.

## Типичные причины ошибок (блокирующих и warning)

### 1. `PROJECT_PATH is empty` / exit 1 после "PROJECT_PATH"

- **Причина**: секрет `PROJECT_PATH` не задан или пустой в настройках репозитория (Settings → Secrets and variables → Actions).
- **Решение**: задать секрет `PROJECT_PATH` — полный путь к папке бэкенда на VPS (например `/home/deploy/tvpn-backend`).

### 2. SSH: Permission denied / Connection refused

- **Причина**: неверный `SSH_PRIVATE_KEY`, не добавлен публичный ключ на сервер, или неверные `SERVER_HOST`/`SERVER_USER`.
- **Решение**: проверить секреты `SSH_PRIVATE_KEY`, `SERVER_HOST`, `SERVER_USER`; на VPS в `~/.ssh/authorized_keys` должен быть публичный ключ, соответствующий приватному из секрета.

### 3. `docker-compose.yml not found in $PROJECT_PATH`

- **Причина**: в `PROJECT_PATH` на сервере нет файла `docker-compose.yml` (не тот каталог или не выполнен git-sync).
- **Решение**: убедиться, что на сервере в `PROJECT_PATH` лежит репозиторий с `docker-compose.yml`; при необходимости выполнить полный sync-чек вручную.
  **ВНИМАНИЕ:** `git reset --hard` и `git clean -ffd` — destructive команды.
  **Preflight перед запуском:** `git rev-parse --is-inside-work-tree >/dev/null && git status --short`.
  **Guarded sync:**
  `test "${TVPN_GIT_SYNC_ACK:-}" = "YES" || { echo "Refusing destructive git sync: set TVPN_GIT_SYNC_ACK=YES after manual review"; exit 1; } && git fetch --prune origin <branch> && git rev-parse --verify --quiet "origin/<branch>^{commit}" >/dev/null && git checkout -f -B <branch> origin/<branch> && git reset --hard origin/<branch> && git clean -ffd && test -z "$(git status --porcelain --untracked-files=no)" && test "$(git rev-parse HEAD)" = "$(git rev-parse origin/<branch>)"`.

### 4. Падение на шаге `docker compose up -d --build`

- **Причина**: ошибка сборки/запуска контейнеров (образ, порты, .env, зависимости).
- **Решение**: смотреть вывод `docker compose` в логе шага "Deploy to server". Проверить на VPS: `if [ -f docker-compose.prod.yml ]; then docker compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache; else docker compose -f docker-compose.yml build --no-cache; fi` и логи контейнеров.

### 5. Таймаут "Waiting for bloobcat health..." (цикл 1..90)

- **Причина**: bloobcat не отвечает на `http://localhost:33083/health` в течение ~180 секунд (контейнер не поднялся, приложение не стартовало, ошибка конфигурации/зависимостей).
- **Блокирующий статус**: **да**. При этом таймауте workflow завершает деплой с `exit 1`.
- **Решение**: на сервере проверить `docker compose ps`, логи bloobcat (`docker compose logs bloobcat`), доступность порта `33083` и переменные окружения приложения.

### 6. Таймаут "Ждем готовности Directus..." (цикл 1..60)

- **Причина**: Directus не отвечает на `http://localhost:8055/server/health` в течение ~120 секунд (контейнер не поднялся, порт не проброшен, паника в приложении).
- **Порядок в workflow**: этот health-check запускается **после успешного bloobcat health-check**.
- **Блокирующий статус**: **да**. При таймауте Directus деплой завершается с `exit 1`.
- **Решение**: на сервере проверить `docker compose ps`, логи Directus (`docker compose logs directus`), доступность порта и переменные окружения Directus.

### 7. `DIRECTUS_ADMIN_EMAIL not set` / `DIRECTUS_ADMIN_PASSWORD not set`

- **Причина**: скрипт `directus_super_setup.py` запускается внутри контейнера `bloobcat`, но переменные `DIRECTUS_ADMIN_EMAIL` и `DIRECTUS_ADMIN_PASSWORD` не переданы в этот контейнер.
- **Блокирующий статус**: **да**. После исчерпания retry для `directus super-setup` workflow завершает деплой с `exit 1` (fail-closed).
- **Решение**: в `docker-compose.yml` (или `docker-compose.prod.yml`) в сервисе `bloobcat` добавить `environment` (или `env_file`) с переменными:
  - `DIRECTUS_ADMIN_EMAIL`
  - `DIRECTUS_ADMIN_PASSWORD`
  Либо задать их в `.env` на сервере и подключить через `env_file` у сервиса `bloobcat`, затем перезапустить деплой.

### 8. Ошибки внутри `directus_super_setup.py` (403, 502, 503, 404)

- **Причина**: Directus доступен, но нет прав у админа, перегрузка или изменился API.
- **Блокирующий статус**: **да**. Если `directus_super_setup.py` не проходит после всех retry, deploy прерывается (`exit 1`) и требует ручного исправления причины.
- **Решение**: проверить логи скрипта в выводе шага; при 403 — проверить логин/пароль и права админа в Directus; при 502/503 — устранить деградацию Directus и запустить деплой повторно.

### 9. Ошибки детерминированного git-sync

- **`fatal: not a git repository` / отсутствует `.git`**
  - **Причина**: `PROJECT_PATH` указывает не на корень репозитория (или `.git` удалён).
  - **Что делать**: проверить `PROJECT_PATH`; убедиться, что в каталоге есть `.git`. Если каталогу нельзя доверять — пересоздать директорию и заново клонировать репозиторий.

- **`pathspec 'origin/<branch>' did not match` / `couldn't find remote ref <branch>`**
  - **Причина**: удалённая ветка не существует, либо в workflow/секретах указан неверный branch.
  - **Что делать**: проверить имя ветки в workflow и на remote (`git ls-remote --heads origin`). Исправить branch и повторить запуск.

- **HEAD mismatch после `reset --hard` (локальный `HEAD` ≠ `origin/<branch>`)**
  - **Причина**: checkout/reset выполнен не на ту ветку или fetch не обновил origin refs.
  - **Что делать**: повторить последовательность строго в этом порядке:
    **ВНИМАНИЕ:** `git reset --hard` и `git clean -ffd` — destructive команды.
    **Preflight:** `git rev-parse --is-inside-work-tree >/dev/null && git status --short`.
    `test "${TVPN_GIT_SYNC_ACK:-}" = "YES" || { echo "Refusing destructive git sync: set TVPN_GIT_SYNC_ACK=YES after manual review"; exit 1; } && git fetch --prune origin <branch> && git rev-parse --verify --quiet "origin/<branch>^{commit}" >/dev/null && git checkout -f -B <branch> origin/<branch> && git reset --hard origin/<branch> && git clean -ffd && test -z "$(git status --porcelain --untracked-files=no)" && test "$(git rev-parse HEAD)" = "$(git rev-parse origin/<branch>)"`.
    Если команда не завершилась с ошибкой — sync корректный, `HEAD` совпадает с `origin/<branch>`.

- **Dirty tracked tree после sync (`git status --porcelain` не пуст)**
  - **Причина**: локальные tracked-файлы изменяются post-checkout хуками, правами/CRLF или внешними скриптами.
  - **Что делать**: посмотреть `git status --porcelain` и `git diff`; устранить источник изменений. До деплоя рабочее дерево должно быть чистым.

### 10. Directus unhealthy из-за рассинхрона DB auth (`28P01` / password authentication failed)

- **Симптом**:
  - bloobcat healthy, а Directus не проходит `http://localhost:8055/server/health`.
  - В логах Directus после старта встречается `28P01` или `password authentication failed`.

- **Почему это теперь обрабатывается иначе**:
  - В workflow включён порядок **reconcile-first**: сначала поднимается только `bloobcat_db`, затем выполняется `scripts/db/reconcile_postgres_auth.sh`, и только потом запускаются `bloobcat/directus`.
  - Модель пароля теперь **single authority**: `POSTGRES_PASSWORD` (из `.env`) синхронно используется для PostgreSQL, Directus и backend DB DSN.

- **Что проверить (non-destructive first)**:
  1. Убедиться, что `POSTGRES_PASSWORD` не пуст:
     - `grep -qE '^POSTGRES_PASSWORD=.+' .env && echo 'POSTGRES_PASSWORD is set (non-empty)' || echo 'POSTGRES_PASSWORD is missing or empty'`
  2. Проверить готовность БД:
     - `docker compose <args> exec -T bloobcat_db pg_isready -U postgres -d postgres`
  3. Запустить reconcile вручную:
      - `COMPOSE_FILES="docker-compose.yml[,docker-compose.prod.yml]" sh scripts/db/reconcile_postgres_auth.sh`
      - `POSTGRES_PASSWORD` должен резолвиться из окружения/`.env`; не передавайте plaintext-значение в командной строке.
  4. Повторить health checks:
     - `curl -sf http://localhost:33083/health`
     - `curl -sf http://localhost:8055/server/health`

- **Когда допустим destructive fallback**:
  - Только вручную (manual dispatch), только при явном opt-in:
    - `allow_destructive_db_reset_once=true`
    - `destructive_reset_ack=<ack token>`
  - И только при DB-specific evidence:
    - либо сигнатура `28P01/password authentication failed` в логах Directus за окно ожидания health,
    - либо подтверждённый auth-probe mismatch (`psql SELECT 1` с Directus DB env).

- **Какие safeguard'ы есть у `scripts/db/destructive_reset_once.sh`**:
  - one-time marker (повторный запуск блокируется);
  - lock-файл (защита от параллельных reset);
  - scoped-volume выбор по compose labels (отказ при 0 или >1 кандидате);
  - best-effort logical backup перед удалением volume;
  - обязательный post-fallback цикл: `pg_isready` -> reconcile -> health re-check.

- **Важно**:
  - Если DB-evidence нет, workflow намеренно **не** запускает destructive reset и завершается ошибкой (fail-closed).
  - Если Directus падает не из-за DB auth, искать причину в конфиге/ресурсах/расширениях, а не в reset volume.

### 11. Инцидент класса: corruption SQL в FK fix (`DO $$ ... $$`)

- **Симптом**:
  - deploy падает на шаге repair FK (`active_tariffs.user_id -> users.id`), часто с SQL syntax/error вокруг dollar-quoting;
  - или FK repair silently не применяется, и позже удаление пользователя снова упирается в FK.

- **Root cause**:
  - payload на `DO $$ ... $$` мог искажаться при shell/YAML escaping, из-за чего SQL-блок в `psql` выполнялся не детерминированно.

- **Текущая стратегия (фикс класса инцидента)**:
  - использовать детерминированный payload с явным тегом `$tvpn_fk$` вместо голого `$$`;
  - при отсутствии `bloobcat_db` в контексте деплоя шаг должен завершаться ошибкой (**fail-closed**), без продолжения в потенциально битом состоянии.

- **Operator checks**:
  1. В логах deploy проверить маркеры FK repair и отсутствие syntax-error на SQL шаге.
  2. Проверить, что в workflow используется тег `$tvpn_fk$` (детерминированный payload).
  3. На VPS подтвердить наличие/готовность `bloobcat_db` перед FK repair:
     - `docker compose ps bloobcat_db`
     - `docker compose exec -T bloobcat_db pg_isready -U postgres -d postgres`
  4. После deploy подтвердить правило удаления FK:
     - `docker compose exec -T bloobcat_db psql -U postgres -d bloobcat_db -c "SELECT rc.delete_rule FROM information_schema.referential_constraints rc JOIN information_schema.key_column_usage kcu ON rc.constraint_name = kcu.constraint_name AND rc.constraint_schema = kcu.constraint_schema WHERE kcu.table_name='active_tariffs' AND kcu.column_name='user_id';"`
     - ожидаемо: `delete_rule = CASCADE`.

---

После исправления можно перезапустить workflow: Actions → выбранный workflow → "Re-run failed jobs" или "Re-run all jobs".
