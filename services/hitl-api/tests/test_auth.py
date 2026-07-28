"""
services/hitl-api/tests/test_auth.py
======================================

Unit and integration tests for Auth & Governance Layer.
"""

import pytest
from fastapi.testclient import TestClient

from main import app
from auth.tokens import create_access_token, decode_access_token
from auth.users import authenticate_user
from agent.resilience import mask_account, _scrub_headers


def test_password_authentication():
    analyst = authenticate_user("analyst1", "analyst123")
    assert analyst is not None
    assert analyst.username == "analyst1"
    assert analyst.role == "ANALYST"

    admin = authenticate_user("admin1", "admin123")
    assert admin is not None
    assert admin.username == "admin1"
    assert admin.role == "ADMIN"

    invalid = authenticate_user("analyst1", "wrongpassword")
    assert invalid is None


def test_jwt_token_flow():
    token = create_access_token({"sub": "analyst1", "role": "ANALYST"})
    decoded = decode_access_token(token)
    assert decoded is not None
    assert decoded["sub"] == "analyst1"
    assert decoded["role"] == "ANALYST"

    # Bad token
    assert decode_access_token("invalid.jwt.token") is None


def test_sensitive_data_masking():
    assert mask_account("SAMP-0012345678") == "SAMP-******5678"
    assert mask_account("12345678") == "****5678"

    assert mask_account("123") == "123"

    headers = {"Authorization": "Bearer secret-token", "Content-Type": "application/json"}
    scrubbed = _scrub_headers(headers)
    assert scrubbed["Authorization"] == "[REDACTED]"
    assert scrubbed["Content-Type"] == "application/json"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_login_endpoint(client):
    res = client.post("/auth/login", json={"username": "analyst1", "password": "analyst123"})
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["role"] == "ANALYST"

    res_bad = client.post("/auth/login", json={"username": "analyst1", "password": "wrong"})
    assert res_bad.status_code == 401


def test_me_endpoint(client):
    token = client.post("/auth/login", json={"username": "admin1", "password": "admin123"}).json()["access_token"]
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    assert data["username"] == "admin1"
    assert data["role"] == "ADMIN"

    res_unauth = client.get("/auth/me")
    assert res_unauth.status_code == 401
