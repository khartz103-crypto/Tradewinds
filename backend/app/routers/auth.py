"""Authentication routes — login, current-user lookup."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import authenticate_user, create_access_token, get_current_user
from app.database import get_db
from app.models.user import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Credentials for logging in."""

    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """JWT token returned on successful login."""

    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """Public user information returned by the /me endpoint."""

    id: str
    email: str
    is_admin: bool
    is_active: bool

    model_config = {"from_attributes": True}


@router.post("/login", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """Authenticate with email + password and return a JWT bearer token."""
    user = await authenticate_user(db, email=body.email, password=body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token(user.id)
    return TokenResponse(access_token=token, token_type="bearer")


@router.get("/me", response_model=UserResponse)
async def me(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """Return the currently authenticated user's information."""
    return UserResponse(
        id=str(current_user.id),
        email=current_user.email,
        is_admin=current_user.is_admin,
        is_active=current_user.is_active,
    )
