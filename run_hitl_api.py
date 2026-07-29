"""
run_hitl_api.py
================

Startup helper for the HITL API service.

Why this exists
---------------
The HITL API uses relative imports (``from db.models import ...``) that only
work when the working directory is ``services/hitl-api/``.  Running
``uvicorn services.hitl-api.main:app`` from the project root fails with
``ModuleNotFoundError: No module named 'db'``.

This script adds ``services/hitl-api/`` to ``sys.path`` before importing
the application, making both of the following work:

    # Option A (recommended for development):
    python run_hitl_api.py

    # Option B (original; run from the service directory):
    cd services/hitl-api
    uvicorn main:app --port 8006 --reload

Usage
-----
::

    # From the project root with the venv active:
    (.venv) python run_hitl_api.py

    # Custom port:
    (.venv) HITL_PORT=8007 python run_hitl_api.py

Environment variables
---------------------
HITL_PORT
    Port to listen on.  Default: ``8006``.
HITL_HOST
    Host to bind.  Default: ``0.0.0.0``.
HITL_RELOAD
    Set to ``"1"`` to enable hot-reload (development only). Default: ``"1"``.
DATABASE_URL
    SQLAlchemy async database URL.  Default: ``sqlite+aiosqlite:///agent_audit.db``
    (relative to the current working directory — run from the project root
    so it uses the same file as the agent).
"""

import os
import sys
from pathlib import Path

# Add services/hitl-api/ to Python path so relative imports work
_SERVICE_DIR = Path(__file__).parent / "services" / "hitl-api"
sys.path.insert(0, str(_SERVICE_DIR))

import uvicorn  # noqa: E402 — must come after sys.path manipulation

if __name__ == "__main__":
    host = os.getenv("HITL_HOST", "0.0.0.0")
    port = int(os.getenv("HITL_PORT", "8006"))
    reload = os.getenv("HITL_RELOAD", "1") == "1"

    print(f"[run_hitl_api] Starting HITL API on {host}:{port} (reload={reload})")
    print(f"[run_hitl_api] Database: {os.getenv('DATABASE_URL', 'sqlite+aiosqlite:///agent_audit.db')}")
    print(f"[run_hitl_api] HITL API docs: http://localhost:{port}/docs")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=reload,
        reload_dirs=[str(_SERVICE_DIR)],
        log_level="info",
    )
