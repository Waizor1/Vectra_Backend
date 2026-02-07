## Directus post-setup (readonly поля)

Скрипт: `scripts/directus_post_setup.py`

Что делает:
- Логинится в Directus.
- Проставляет `meta.readonly=true` для критичных полей (users, active_tariffs, promo_*, prize_wheel_*, connections и др.).
- Безопасен при отсутствии коллекции/поля: пропускает (в т.ч. 403/404).

Переменные:
- `DIRECTUS_URL`
- `DIRECTUS_ADMIN_EMAIL`
- `DIRECTUS_ADMIN_PASSWORD`

Примечание:
- Скрипт сам подхватывает `.env` через `python-dotenv`, если модуль установлен.
- Если графики дашборда пустые для ролей Manager/Viewer, запусти `scripts/directus_insights_permissions.py`.
