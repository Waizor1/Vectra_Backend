RESCUE_APP_URL = "https://app.vectra-pro.net"

_RU_RESCUE_COPY = (
    "Если не получается открыть бота, заранее сохраните эту ссылку — "
    "она поможет получить разовый ключ для настройки Happ "
    "и продолжить управление подпиской:"
)

_EN_RESCUE_COPY = (
    "If the bot is hard to open, save this link in advance — "
    "it will help you get a one-time key for Happ setup "
    "and continue managing your subscription:"
)


def build_rescue_link_paragraph(lang: str) -> str:
    intro = _RU_RESCUE_COPY if lang == "ru" else _EN_RESCUE_COPY
    return f"{intro}\n{RESCUE_APP_URL}"


def append_rescue_link(text: str, *, lang: str) -> str:
    return f"{text}\n\n{build_rescue_link_paragraph(lang)}"
