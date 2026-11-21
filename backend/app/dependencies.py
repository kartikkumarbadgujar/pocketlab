from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User
from app.security.tokens import decode_access_token

bearer = HTTPBearer(auto_error=False)


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User:
    if not credentials:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    try:
        uid = decode_access_token(credentials.credentials)
    except (jwt.InvalidTokenError, ValueError, KeyError):
        raise HTTPException(401, "Invalid or expired access token", headers={"WWW-Authenticate": "Bearer"})
    user = db.get(User, uid)
    if not user or not user.is_active:
        raise HTTPException(401, "Invalid or inactive user")
    return user


def require_runner(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    from app.config import settings
    import secrets
    if not credentials or not secrets.compare_digest(credentials.credentials, settings.runner_token):
        raise HTTPException(401, "Runner authentication required")
    return True
