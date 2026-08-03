"""JWT authentication utilities — token creation, user lookup, and dependency."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.hash import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User

bearer_scheme = HTTPBearer()


class InvalidTokenError(HTTPException):
    """Raised when a Bearer token is missing, expired, or invalid."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def create_access_token(user_id: UUID) -> str:
    """Create a signed JWT with ``sub`` and ``exp`` claims.

    Args:
        user_id: The user's UUID to encode in the ``sub`` claim.

    Returns:
        An HS256-signed JWT string.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "exp": now + timedelta(hours=settings.jwt_expiry_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    """Look up a user by *email* and verify *password* against bcrypt hash.

    Returns the ``User`` on success or ``None`` on any failure.
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        return None
    if not bcrypt.verify(password, user.hashed_password):
        return None
    if not user.is_active:
        return None
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency — validate Bearer JWT and return the authenticated ``User``.

    Raises ``401 Unauthorized`` if the token is missing, expired, or invalid,
    or if the referenced user does not exist.
    """
    token = credentials.credentials

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        user_id_str: str | None = payload.get("sub")
        if user_id_str is None:
            raise InvalidTokenError()
    except JWTError:
        raise InvalidTokenError()

    try:
        user_id = UUID(user_id_str)
    except ValueError:
        raise InvalidTokenError()

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise InvalidTokenError()

    return user
