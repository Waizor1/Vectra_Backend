## Directus Extensions

Эта папка содержит расширения Directus для миграции админки:
- hooks:
  - `remnawave-sync`: синхронизация LTE/RemnaWave и удаление пользователей через backend.
  - `promo-code-hmac`: генерация HMAC промокодов.
- endpoints:
  - `admin-widgets`: API для графиков дашборда (аналог FastAdmin widgets).
  - `tariff-studio`: server-side proxy для backend preview/validation тарифов (`/tariff-studio/quote-preview`) без выдачи admin integration token в браузер.
