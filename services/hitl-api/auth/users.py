"""
services/hitl-api/auth/users.py
================================

User repository and password hashing for HITL API governance.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict
from pydantic import BaseModel


class User(BaseModel):
    username: str
    role: str  # "ANALYST" or "ADMIN"
    display_name: str
    company_code: str = "1000"


def _hash_password(password: str) -> str:
    """SHA-256 password hash for internal comparison."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


# Default seeded users for demo & testing
_DEFAULT_USERS = [
    {
        "username": "analyst1",
        "password_hash": _hash_password("analyst123"),
        "role": "ANALYST",
        "display_name": "Dilshan Perera (Analyst)",
        "company_code": "1000",
    },
    {
        "username": "admin1",
        "password_hash": _hash_password("admin123"),
        "role": "ADMIN",
        "display_name": "Nimal Silva (Treasury Admin)",
        "company_code": "1000",
    },
]


def _get_user_db() -> dict[str, dict[str, Any]]:
    config_env = os.getenv("USERS_CONFIG", "")
    if config_env:
        try:
            users_list = json.loads(config_env)
            return {u["username"]: u for u in users_list}
        except Exception:
            pass
    return {u["username"]: u for u in _DEFAULT_USERS}


def authenticate_user(username: str, password_raw: str) -> User | None:
    """
    Authenticate a user by username and password.
    Returns User instance if valid, or None if invalid.
    """
    user_db = _get_user_db()
    user_record = user_db.get(username)
    if not user_record:
        return None

    expected_hash = user_record.get("password_hash", "")
    if _hash_password(password_raw) != expected_hash:
        return None

    return User(
        username=user_record["username"],
        role=user_record["role"],
        display_name=user_record.get("display_name", username),
        company_code=user_record.get("company_code", "1000"),
    )


def get_user_by_username(username: str) -> User | None:
    user_db = _get_user_db()
    user_record = user_db.get(username)
    if not user_record:
        return None
    return User(
        username=user_record["username"],
        role=user_record["role"],
        display_name=user_record.get("display_name", username),
        company_code=user_record.get("company_code", "1000"),
    )
