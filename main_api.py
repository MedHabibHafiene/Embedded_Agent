#!/usr/bin/env python3
"""
Backward-compatible launcher for the FastAPI backend.

The application itself now lives in app/main.py; this entry point is kept so
`python main_api.py` (and any scripts/docs referencing it) keep working.

Run:
    python main_api.py                       # 0.0.0.0:8000 (configurable via .env)
    uvicorn app.main:app --reload            # development
"""

import uvicorn

from config import API_HOST, API_PORT

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=API_HOST, port=API_PORT)
