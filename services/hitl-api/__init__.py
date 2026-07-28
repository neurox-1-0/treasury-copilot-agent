"""
services/hitl-api/__init__.py
==============================

Package marker so the HITL API can be imported as a Python package
when running from the project root:

    uvicorn services.hitl_api.main:app --port 8006 --reload

Without this file, ``from db.models import ...`` works only when the
working directory is ``services/hitl-api/``.  With this file and the
``PYTHONPATH`` workaround below, both styles work.

Running from the project root (recommended)
--------------------------------------------
Set PYTHONPATH so Python can find the ``db`` and ``schemas`` sub-packages::

    $env:PYTHONPATH = "services/hitl-api"
    uvicorn services.hitl_api.main:app --port 8006 --reload

Or simply run from the service directory (simplest for local dev)::

    cd services/hitl-api
    uvicorn main:app --port 8006 --reload
"""
