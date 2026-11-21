import hashlib
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import RefreshToken, User
from app.security.passwords import hash_password, verify_password
from app.security.tokens import issue_access_token, issue_refresh_token, refresh_expiry


def register(db: Session, email: str, password: str) -> User:
    email = email.lower().strip()
    if db.scalar(select(User).where(User.email == email)):
        raise ValueError("Email already registered")
    user = User(email=email, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def login(db: Session, email: str, password: str) -> tuple[str, str] | None:
    user = db.scalar(select(User).where(User.email == email.lower().strip()))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return None
    raw, digest = issue_refresh_token()
    db.add(RefreshToken(user_id=user.id, token_hash=digest, expires_at=refresh_expiry()))
    db.commit()
    return issue_access_token(user.id), raw


def rotate_refresh(db: Session, raw: str) -> tuple[str, str] | None:
    digest = hashlib.sha256(raw.encode()).hexdigest()
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == digest).with_for_update())
    expires_at=token.expires_at.replace(tzinfo=timezone.utc) if token and token.expires_at.tzinfo is None else token.expires_at if token else None
    if not token or token.revoked_at or expires_at <= datetime.now(timezone.utc):
        if token and token.revoked_at:
            # Reuse of an old token revokes the active family for this account.
            db.query(RefreshToken).filter(RefreshToken.user_id == token.user_id, RefreshToken.revoked_at.is_(None)).update({"revoked_at": datetime.now(timezone.utc)})
            db.commit()
        return None
    user = db.get(User, token.user_id)
    if not user or not user.is_active:
        return None
    next_raw, next_digest = issue_refresh_token()
    token.revoked_at = datetime.now(timezone.utc)
    token.replaced_by_hash = next_digest
    db.add(RefreshToken(user_id=user.id, token_hash=next_digest, expires_at=refresh_expiry()))
    db.commit()
    return issue_access_token(user.id), next_raw


def revoke_refresh(db: Session, raw: str, user_id: int) -> bool:
    digest = hashlib.sha256(raw.encode()).hexdigest()
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == digest))
    if token and token.user_id==user_id and not token.revoked_at:
        token.revoked_at = datetime.now(timezone.utc)
        db.commit()
        return True
    return False
