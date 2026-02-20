from __future__ import annotations


def normalize_start_param(raw: str | None) -> str:
    return (raw or "").strip()


def _has_suffix(param: str, prefix: str) -> bool:
    return param.startswith(prefix) and len(param) > len(prefix)


def is_family_start_param(raw: str | None) -> bool:
    param = normalize_start_param(raw)
    return _has_suffix(param, "family_")


def is_qr_start_param(raw: str | None) -> bool:
    param = normalize_start_param(raw)
    return _has_suffix(param, "qr_")


def is_ref_start_param(raw: str | None) -> bool:
    param = normalize_start_param(raw)
    if not param:
        return False
    if param.isdigit():
        return True
    if _has_suffix(param, "ref_"):
        return True
    if _has_suffix(param, "ref-"):
        return True
    if "-" in param:
        _, ref_part = param.rsplit("-", 1)
        return ref_part.isdigit()
    return False


def is_registration_exception_start_param(raw: str | None) -> bool:
    return (
        is_family_start_param(raw)
        or is_qr_start_param(raw)
        or is_ref_start_param(raw)
    )
