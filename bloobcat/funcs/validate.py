from aiogram.utils.web_app import safe_parse_webapp_init_data
from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader
import jwt
from urllib.parse import urlparse, parse_qs

from bloobcat.db.users import Users
from bloobcat.db.partner_qr import PartnerQr
from bloobcat.settings import telegram_settings
from bloobcat.logger import get_logger
from bloobcat.funcs.auth_tokens import decode_access_token
from bloobcat.funcs.start_params import is_registration_exception_start_param
import uuid

logger = get_logger("validate")

oauth2_scheme = APIKeyHeader(name="Authorization", auto_error=False)


def _extract_start_param_from_request(request: Request | None) -> str:
    if request is None:
        return ""

    # Preferred explicit channel from frontend.
    try:
        raw = (request.headers.get("X-Start-Param") or "").strip()
        if raw:
            return raw
    except Exception:
        pass

    # Fallback for legacy clients: recover from Referer query.
    try:
        referer = (request.headers.get("Referer") or "").strip()
        if referer:
            q = parse_qs(urlparse(referer).query)
            for key in ("start", "start_param", "startParam"):
                values = q.get(key) or []
                if values and str(values[0]).strip():
                    return str(values[0]).strip()
    except Exception:
        pass

    return ""


async def _resolve_referral_from_start_param(param_raw: str | None, user_id: int | None = None) -> tuple[int, str | None]:
    param = (param_raw or "").strip()
    if not param:
        return 0, None

    referred_by = 0
    utm = None

    # Combined UTM and numeric referral (format: <utm>-<referrer_id>)
    if "-" in param:
        utm_part, ref_part = param.rsplit("-", 1)
        if ref_part.isdigit():
            referred_by = int(ref_part)
            utm = utm_part
            return referred_by, utm
        return 0, param

    if param.startswith("family_"):
        return 0, None

    if param.isdigit():
        return int(param), None

    if param.startswith("qr_"):
        utm = param
        token = param[3:]
        qr = None
        try:
            qr_uuid = uuid.UUID(token) if len(token) != 32 else uuid.UUID(hex=token)
            qr = await PartnerQr.get_or_none(id=qr_uuid)
        except Exception:
            qr = None
        if not qr:
            qr = await PartnerQr.get_or_none(slug=token)
        if qr:
            referred_by = int(qr.owner_id)
        logger.debug("Resolved partner QR token=%s user=%s referrer=%s", token, user_id, referred_by)
        return referred_by, utm

    return 0, param


async def _apply_referral_attribution_existing_user(
    db_user: Users, referred_by: int, utm: str | None
) -> Users:
    update_fields: list[str] = []

    # First-touch UTM: set it once, if currently empty.
    if utm:
        current_utm = (getattr(db_user, "utm", None) or "").strip()
        if not current_utm:
            db_user.utm = utm
            update_fields.append("utm")

    # Bind referral once if not already set. Relaxed: allow even for registered users
    # so that users who registered before clicking a referral link can still be attributed.
    if (
        referred_by
        and int(referred_by) != int(db_user.id)
        and int(getattr(db_user, "referred_by", 0) or 0) == 0
    ):
        referrer = await Users.get_or_none(id=int(referred_by))
        if referrer:
            db_user.referred_by = int(referred_by)
            update_fields.append("referred_by")
            logger.info(
                "referral_bind user=%s referrer=%s is_registered=%s",
                db_user.id,
                referred_by,
                getattr(db_user, "is_registered", None),
            )
        else:
            logger.warning(
                "referral_bind_skip_no_referrer user=%s referrer_id=%s",
                db_user.id,
                referred_by,
            )

    if update_fields:
        await db_user.save(update_fields=update_fields)
        logger.info(
            "Referral attribution applied in validate: user=%s fields=%s referred_by=%s utm=%s",
            db_user.id,
            ",".join(update_fields),
            int(getattr(db_user, "referred_by", 0) or 0),
            getattr(db_user, "utm", None),
        )

    return db_user


async def validate(init_data: str = Depends(oauth2_scheme), request: Request = None) -> Users:
    try:
        preview = (init_data or "")[:50]
        logger.debug(f"Получены данные для валидации: {preview}...")  # логируем только начало для безопасности
        header_start_param = _extract_start_param_from_request(request)
        
        if not init_data:
            logger.error("Отсутствует заголовок Authorization")
            raise HTTPException(status_code=403, detail="Missing Authorization header")

        if init_data.lower().startswith("bearer "):
            token = init_data.split(" ", 1)[1].strip()
            if not token:
                raise HTTPException(status_code=403, detail="Empty bearer token")
            try:
                payload = decode_access_token(token)
            except jwt.ExpiredSignatureError:
                raise HTTPException(status_code=403, detail="Token expired")
            except jwt.InvalidTokenError:
                raise HTTPException(status_code=403, detail="Invalid token")

            user_id = payload.get("sub") or payload.get("user_id")
            if not user_id:
                raise HTTPException(status_code=403, detail="Invalid token payload")

            try:
                user_id_int = int(user_id)
            except (TypeError, ValueError):
                raise HTTPException(status_code=403, detail="Invalid token subject")

            db_user = await Users.get_or_none(id=user_id_int)
            if not db_user:
                raise HTTPException(status_code=403, detail="User not found")

            # Reliability guard: even in bearer mode, allow client to forward start param.
            # This prevents missed attribution when /auth/telegram bootstrap is skipped/raced.
            if header_start_param:
                referred_by, utm = await _resolve_referral_from_start_param(
                    header_start_param, user_id=db_user.id
                )
                db_user = await _apply_referral_attribution_existing_user(
                    db_user, referred_by=referred_by, utm=utm
                )

            return db_user
            
        user = safe_parse_webapp_init_data(
            telegram_settings.token.get_secret_value(), init_data
        )

        # Проверка на None после парсинга - защита от некорректных данных
        if not user or not user.user:
            logger.error("Парсинг вернул пустой объект пользователя")
            raise HTTPException(status_code=403, detail="Invalid user data")

        logger.debug(f"Успешная валидация для пользователя {user.user.id}")

        # Fast-path под высокой нагрузкой:
        # если пользователь уже есть в БД и связан с RemnaWave и нет старт-параметра,
        # не запускаем повторный update_or_create/get_user.
        # При наличии start_param оставляем обычный путь, чтобы не потерять referral/utm-обработку.
        init_start_param = (getattr(user, "start_param", None) or "").strip()
        effective_start_param = init_start_param or header_start_param
        existing_user = await Users.get_or_none(id=user.user.id)
        if existing_user and not effective_start_param:
            return existing_user

        # Do not create a user implicitly on generic initData-only requests.
        # User row is created only when:
        # - explicit registration intent endpoint is called (/auth/telegram with registerIntent), or
        # - a deep-link start_param is present (family/ref/qr flows).
        if not existing_user and not is_registration_exception_start_param(effective_start_param):
            raise HTTPException(status_code=403, detail="User not registered")

        logger.debug(
            "Проверка start_param для пользователя %s: init=%r, header=%r",
            user.user.id,
            init_start_param,
            header_start_param,
        )
        referred_by, utm = await _resolve_referral_from_start_param(
            effective_start_param, user_id=user.user.id
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка валидации: {str(e)}")
        raise HTTPException(status_code=403, detail=str(e))
    
    # Явно передаем параметры по имени для большей ясности
    db_user, _ = await Users.get_user(
        telegram_user=user.user, 
        referred_by=referred_by, 
        utm=utm
    )
    
    if not db_user:
        logger.error("Не удалось получить пользователя из базы данных")
        raise HTTPException(status_code=500, detail="User not found in database")
    
    return db_user
