"""
services/hitl-api/auth/dependencies.py
======================================

FastAPI authentication and RBAC dependency injectors.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from auth.tokens import decode_access_token
from auth.users import User, get_user_by_username


async def get_current_user(request: Request) -> User:
    """
    Extract and verify Bearer token from Authorization header.
    Returns authenticated User or raises 401.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth_header.split(" ", 1)[1]
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username = payload["sub"]
    user = get_user_by_username(username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists",
        )

    return user


async def require_analyst(user: User = Depends(get_current_user)) -> User:
    """
    Ensure the user is at least a Treasury Analyst (ANALYST or ADMIN).
    """
    if user.role not in ("ANALYST", "ADMIN"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Action requires Treasury Analyst or Treasury Admin role",
        )
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """
    Ensure the user is a Treasury Admin (ADMIN).
    """
    if user.role != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Action requires Treasury Admin permission level",
        )
    return user
