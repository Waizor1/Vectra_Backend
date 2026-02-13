# Ошибки деплоя (Auto Deploy Backend)

## Где смотреть ошибку

- **GitHub Actions**: репозиторий TVPN_BACK_END → вкладка **Actions** → последний failed run → шаг, на котором упало (красный крестик).
- Скопируй текст ошибки из лога шага или приложи скрин — по нему можно точно указать причину.

---

### 9. **Process completed with exit code 137** (частая причина падения)

- **Что значит**: процесс был принудительно завершён (сигнал SIGKILL, 137 = 128 + 9). Обычно это **нехватка памяти (OOM)** на сервере или реже — таймаут/ограничение ресурсов.
- **Где происходит**: код 137 приходит с **VPS** (внутри шага "Deploy to server"): чаще всего падает `docker compose up -d --build` или один из контейнеров (сборка образа съедает много RAM).
- **Что сделать**:
  1. **На VPS** проверить память и OOM:
     - `free -h` — сколько RAM и swap;
     - `dmesg | tail -100` или `journalctl -k -b | grep -i oom` — был ли убийца процессов по памяти.
  2. **Увеличить swap** на сервере, если мало RAM (например 2–4 GB swap), чтобы сборка не убивалась.
  3. **Снизить потребление при сборке**: в `docker-compose` или Dockerfile по возможности использовать multi-stage build, не тянуть лишние зависимости; при необходимости собирать образ с ограничением параллелизма (например `DOCKER_BUILDKIT=1 docker compose build --parallel 1`).
  4. **Проверить**, не падает ли конкретный сервис (например Directus или bloobcat) при старте — тогда смотреть логи контейнера и исправлять конфиг/код.
- **Временный обход**: запустить деплой вручную на VPS (`git pull`, `docker compose up -d --build`) и следить за `free -h` и логами во время сборки.

## Типичные причины падения

### 1. `PROJECT_PATH is empty` / exit 1 после "PROJECT_PATH"

- **Причина**: секрет `PROJECT_PATH` не задан или пустой в настройках репозитория (Settings → Secrets and variables → Actions).
- **Решение**: задать секрет `PROJECT_PATH` — полный путь к папке бэкенда на VPS (например `/home/deploy/tvpn-backend`).

### 2. SSH: Permission denied / Connection refused

- **Причина**: неверный `SSH_PRIVATE_KEY`, не добавлен публичный ключ на сервер, или неверные `SERVER_HOST`/`SERVER_USER`.
- **Решение**: проверить секреты `SSH_PRIVATE_KEY`, `SERVER_HOST`, `SERVER_USER`; на VPS в `~/.ssh/authorized_keys` должен быть публичный ключ, соответствующий приватному из секрета.

### 3. `docker-compose.yml not found in $PROJECT_PATH`

- **Причина**: в `PROJECT_PATH` на сервере нет файла `docker-compose.yml` (не тот каталог или не сделан `git pull`).
- **Решение**: убедиться, что на сервере в `PROJECT_PATH` лежит репозиторий с `docker-compose.yml`; при необходимости выполнить там `git pull` вручную.

### 4. Падение на шаге `docker compose up -d --build`

- **Причина**: ошибка сборки/запуска контейнеров (образ, порты, .env, зависимости).
- **Решение**: смотреть вывод `docker compose` в логе шага "Deploy to server". Проверить на VPS: `docker compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache` и логи контейнеров.

### 5. Таймаут "Ждем готовности Directus..." (цикл 1..45)

- **Причина**: Directus не отвечает на `http://localhost:8055/server/health` в течение ~90 секунд (контейнер не поднялся, порт не проброшен, паника в приложении).
- **Решение**: на сервере проверить `docker compose ps`, логи Directus (`docker compose logs directus`), доступность порта и переменные окружения Directus.

### 6. `DIRECTUS_ADMIN_EMAIL not set` / `DIRECTUS_ADMIN_PASSWORD not set`

- **Причина**: скрипт `directus_super_setup.py` запускается внутри контейнера `bloobcat`, но переменные `DIRECTUS_ADMIN_EMAIL` и `DIRECTUS_ADMIN_PASSWORD` не переданы в этот контейнер.
- **Решение**: в `docker-compose.yml` (или `docker-compose.prod.yml`) в сервисе `bloobcat` добавить `environment` (или `env_file`) с переменными:
  - `DIRECTUS_ADMIN_EMAIL`
  - `DIRECTUS_ADMIN_PASSWORD`
  Либо задать их в `.env` на сервере и подключить через `env_file` у сервиса `bloobcat`.

### 7. Ошибки внутри `directus_super_setup.py` (403, 502, 503, 404)

- **Причина**: Directus доступен, но нет прав у админа, перегрузка или изменился API.
- **Решение**: проверить логи скрипта в выводе шага; при 403 — проверить логин/пароль и права админа в Directus; при 502/503 — увеличить таймаут или повторить деплой после стабилизации Directus.

### 8. `git pull --ff-only` failed

- **Причина**: на сервере есть локальные коммиты или изменения, которые конфликтуют с удалённой веткой.
- **Решение**: на VPS зайти в `PROJECT_PATH`, выполнить `git status`, при необходимости `git stash` и снова `git pull`, либо разрешить конфликты и закоммитить; после этого перезапустить деплой.

---

После исправления можно перезапустить workflow: Actions → выбранный workflow → "Re-run failed jobs" или "Re-run all jobs".
