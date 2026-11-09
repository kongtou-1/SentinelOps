import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import jwt
from fastapi import HTTPException
from pwdlib import PasswordHash

from app.core.config import get_settings
from app.core.permissions import ROLE_PERMISSIONS as ROLE_PERMISSIONS
from app.models import AuthSession, User

password_hasher = PasswordHash.recommended()
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(32))


def check_permission(user: User, permission: str) -> User:
    from sqlalchemy.orm import object_session

    from app.services.access_control import require

    require(object_session(user), user, permission)
    return user


def unauthorized() -> HTTPException:
    return HTTPException(
        401, "Invalid credentials or token", headers={"WWW-Authenticate": "Bearer"}
    )


def hash_jti(jti: str) -> str:
    return hashlib.sha256(jti.encode()).hexdigest()


def token_pair(user: User, session: AuthSession, refresh_jti: str) -> dict:
    settings = get_settings()
    now = datetime.now(UTC)
    base = {
        "sub": str(user.id),
        "sid": str(session.id),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
    }
    access_expires = now + timedelta(minutes=settings.access_token_minutes)
    access = jwt.encode(
        {**base, "type": "access", "exp": access_expires},
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    refresh = jwt.encode(
        {**base, "type": "refresh", "jti": refresh_jti, "exp": session.expires_at},
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": settings.access_token_minutes * 60,
    }


def decode_token(token: str, token_type: Literal["access", "refresh"]) -> dict:
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "iat", "sub", "sid", "type", "iss", "aud"]},
        )
        if claims["type"] != token_type:
            raise ValueError("Wrong token type")
        UUID(claims["sub"])
        UUID(claims["sid"])
        if token_type == "refresh" and not isinstance(claims.get("jti"), str):
            raise ValueError("Missing refresh identifier")
        return claims
    except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError):
        raise unauthorized() from None
