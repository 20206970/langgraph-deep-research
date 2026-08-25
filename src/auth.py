"""Password and JWT helpers for authenticated application APIs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pydantic import BaseModel, Field, field_validator

from src.config import AuthConfig, get_config


_bearer_scheme = HTTPBearer(auto_error=False)


class CredentialRequest(BaseModel):
    """Credentials accepted by registration and login endpoints."""

    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    password: str = Field(min_length=8, max_length=72)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip()

    @field_validator("password")
    @classmethod
    def validate_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("password must be at most 72 UTF-8 bytes")
        return value


class CurrentUser(BaseModel):
    """Authenticated user identity available to protected endpoints."""

    user_id: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=64)


class TokenResponse(BaseModel):
    """Bearer token returned after successful registration or login."""

    access_token: str
    token_type: str = "bearer"
    user: CurrentUser


def hash_password(password: str) -> str:
    """Hash a validated password with bcrypt's adaptive work factor."""

    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Return false for malformed stored hashes without leaking details."""

    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (TypeError, ValueError):
        return False


def create_access_token(user: CurrentUser, config: AuthConfig) -> str:
    """Create a short-lived signed token whose subject is the stable user ID."""

    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.user_id,
        "username": user.username,
        "iat": now,
        "exp": now + timedelta(minutes=config.access_token_minutes),
    }
    return jwt.encode(payload, config.jwt_secret, algorithm=config.jwt_algorithm)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode_access_token(token: str, config: AuthConfig) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, config.jwt_secret, algorithms=[config.jwt_algorithm])
    except InvalidTokenError as error:
        raise _unauthorized() from error
    if not isinstance(payload.get("sub"), str) or not payload["sub"].strip():
        raise _unauthorized()
    return payload


def require_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> CurrentUser:
    """Resolve a token subject to a current repository user on every request."""

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()

    payload = _decode_access_token(credentials.credentials, get_config().auth)
    repository = getattr(request.app.state, "repository", None)
    if repository is None:
        raise _unauthorized()
    user = repository.get_user(str(payload["sub"]))
    if user is None:
        raise _unauthorized()
    return CurrentUser(user_id=user["user_id"], username=user["username"])
