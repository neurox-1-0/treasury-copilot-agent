"""
$metadata document generation and CSRF token issuance.

Real SAP OData services expose /$metadata as an EDMX/XML document describing
entity types, properties, and keys. We generate a simplified JSON-shaped
equivalent (easier to consume for a demo/agent) but keep the same conceptual
structure: EntityType -> Properties -> Key.

Real SAP also protects modifying requests (POST/PATCH/DELETE) with CSRF
tokens: the client fetches one via a GET with header X-CSRF-Token: Fetch,
then must echo it back on the modifying request. We replicate this handshake.
"""

import secrets
from datetime import datetime, timedelta
from fastapi import Header, HTTPException, Response


# In-memory token store: {token: expiry}. A real system would use Redis/session store.
_csrf_tokens: dict[str, datetime] = {}

CSRF_TOKEN_TTL_MINUTES = 30


def issue_csrf_token() -> str:
    token = secrets.token_urlsafe(24)
    _csrf_tokens[token] = datetime.utcnow() + timedelta(minutes=CSRF_TOKEN_TTL_MINUTES)
    return token


def validate_csrf_token(x_csrf_token: str | None = Header(None)) -> None:
    """
    Dependency for modifying (POST/PATCH/DELETE) endpoints.
    Mirrors SAP Gateway's CSRF protection handshake.
    """
    if not x_csrf_token or x_csrf_token not in _csrf_tokens:
        raise HTTPException(
            status_code=403,
            detail="CSRF token missing or invalid. Fetch one first via GET with "
                   "header 'X-CSRF-Token: Fetch' on any read endpoint.",
        )
    if _csrf_tokens[x_csrf_token] < datetime.utcnow():
        del _csrf_tokens[x_csrf_token]
        raise HTTPException(status_code=403, detail="CSRF token expired. Fetch a new one.")


def maybe_issue_csrf_token(response: Response, x_csrf_token: str | None = Header(None)) -> None:
    """
    Attach a fresh CSRF token to any GET response when the client requests one
    via header 'X-CSRF-Token: Fetch' — matching real SAP Gateway behavior.
    """
    if x_csrf_token and x_csrf_token.lower() == "fetch":
        token = issue_csrf_token()
        response.headers["X-CSRF-Token"] = token


# ── Simplified $metadata document ───────────────────────────────

def build_metadata_document(services: dict[str, dict]) -> dict:
    """
    services = {
        "API_BUSINESS_PARTNER": {
            "entity_type": "A_BusinessPartner",
            "key": ["BusinessPartner"],
            "properties": {"BusinessPartner": "Edm.String", "BusinessPartnerName": "Edm.String", ...},
        },
        ...
    }
    Returns a simplified JSON representation of what a real EDMX $metadata
    document conveys: schema namespace, entity types, keys, and properties.
    """
    return {
        "Edmx": {
            "Version": "2.0",
            "DataServices": {
                "Schema": {
                    "Namespace": "com.sap.gateway.treasurycopilot",
                    "EntityType": [
                        {
                            "Name": svc["entity_type"],
                            "Key": {"PropertyRef": [{"Name": k} for k in svc["key"]]},
                            "Property": [
                                {"Name": name, "Type": edm_type}
                                for name, edm_type in svc["properties"].items()
                            ],
                        }
                        for svc in services.values()
                    ],
                    "EntityContainer": {
                        "EntitySet": [
                            {"Name": name, "EntityType": f"com.sap.gateway.treasurycopilot.{svc['entity_type']}"}
                            for name, svc in services.items()
                        ]
                    },
                }
            },
        }
    }
