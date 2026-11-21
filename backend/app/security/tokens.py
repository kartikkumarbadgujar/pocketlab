import hashlib
import secrets
from datetime import datetime, timedelta, timezone
import jwt
from app.config import settings


def issue_access_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user_id), "type": "access", "iat": now, "exp": now + timedelta(minutes=settings.access_token_minutes)}, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def issue_refresh_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(48)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def refresh_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days)


def decode_access_token(raw: str) -> int:
    payload = jwt.decode(raw, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return int(payload["sub"])
