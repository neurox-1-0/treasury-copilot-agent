"""
services/hitl-api/auth/tokens.py
================================

Lightweight, dependency-free JWT helper using HMAC-SHA256.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "treasury-copilot-jwt-secret-key-2026-key")
ALGORITHM = "HS256"
DEFAULT_EXPIRE_SECONDS = 8 * 3600  # 8 hours


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (4 - (len(data) % 4))
    return base64.urlsafe_b64decode(data + padding)


def create_access_token(
    data: dict[str, Any], expires_delta: int | None = None
) -> str:
    """
    Create a signed JWT access token.
    """
    to_encode = data.copy()
    expire = int(time.time()) + (expires_delta or DEFAULT_EXPIRE_SECONDS)
    to_encode.update({"exp": expire, "iat": int(time.time())})

    header = {"alg": ALGORITHM, "typ": "JWT"}
    
    header_bytes = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_bytes = _b64url_encode(json.dumps(to_encode, separators=(",", ":")).encode("utf-8"))

    signing_input = f"{header_bytes}.{payload_bytes}".encode("utf-8")
    signature = hmac.new(SECRET_KEY.encode("utf-8"), signing_input, hashlib.sha256).digest()
    signature_bytes = _b64url_encode(signature)

    return f"{header_bytes}.{payload_bytes}.{signature_bytes}"


def decode_access_token(token: str) -> dict[str, Any] | None:
    """
    Decode and verify a signed JWT token. Returns payload dict or None if invalid/expired.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        header_b64, payload_b64, signature_b64 = parts
        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")

        expected_sig = hmac.new(
            SECRET_KEY.encode("utf-8"), signing_input, hashlib.sha256
        ).digest()
        actual_sig = _b64url_decode(signature_b64)

        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        payload_bytes = _b64url_decode(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))

        # Check expiration
        exp = payload.get("exp")
        if exp and int(time.time()) > exp:
            return None

        return payload

    except Exception:
        return None
