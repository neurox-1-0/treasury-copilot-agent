"""
services/hitl-api/tests/test_governance.py
============================================

Tests for role-based governance, goal parameter updates, and audit chain verification.
"""

import pytest
from fastapi.testclient import TestClient
from main import app
from db.models import seed_proposal


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.anyio
async def test_role_based_decision_and_admin_protection(client):
    proposal_id = "gov-prop-001"
    await seed_proposal(proposal_id=proposal_id)

    # 1. Unauthenticated decision fails
    res = client.post(f"/proposals/{proposal_id}/decision", json={"decision": "APPROVED"})
    assert res.status_code == 401

    # 2. Analyst login & decision succeeds
    analyst_token = client.post("/auth/login", json={"username": "analyst1", "password": "analyst123"}).json()["access_token"]
    res_analyst = client.post(
        f"/proposals/{proposal_id}/decision",
        json={"decision": "APPROVED", "humanNote": "Approved by analyst"},
        headers={"Authorization": f"Bearer {analyst_token}"},
    )
    assert res_analyst.status_code == 200

    # 3. Analyst trying to access admin endpoint fails (403)
    res_admin_fail = client.get(
        "/admin/goal-parameters",
        headers={"Authorization": f"Bearer {analyst_token}"},
    )
    assert res_admin_fail.status_code == 403

    # 4. Admin login & access admin endpoint succeeds (200)
    admin_token = client.post("/auth/login", json={"username": "admin1", "password": "admin123"}).json()["access_token"]
    res_admin_ok = client.get(
        "/admin/goal-parameters",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_admin_ok.status_code == 200
    params = res_admin_ok.json()
    assert "minimum_liquidity_buffer" in params

    # 5. Admin updates goal parameters
    res_update = client.put(
        "/admin/goal-parameters",
        json={"minimum_liquidity_buffer": "25000000.00", "max_payment_risk_days": 14},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_update.status_code == 200
    updated = res_update.json()
    assert updated["minimum_liquidity_buffer"] == "25000000.00"
    assert updated["max_payment_risk_days"] == 14

    # 6. Verify audit chain endpoint (Admin only)
    res_chain = client.get(
        "/admin/audit/verify-chain",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_chain.status_code == 200
    chain_data = res_chain.json()
    assert chain_data["valid"] is True
