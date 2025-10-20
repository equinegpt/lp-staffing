from __future__ import annotations
import os, secrets
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# This file lives at app/core/config.py, so root is 2 levels up.
ROOT_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT_DIR / "static"
TEMPLATES_DIR = ROOT_DIR / "templates"

load_dotenv(dotenv_path=ROOT_DIR / ".env", override=False)  # OK if missing on Render

def env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if (v is not None and v != "") else default

DATABASE_URL = env("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is required")

# Normalize to psycopg v3 driver for SQLAlchemy
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
if DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

ADMIN_API_KEY      = env("ADMIN_API_KEY", "")
ADMIN_WEB_PASSWORD = env("ADMIN_WEB_PASSWORD", "")
ADMIN_WEB_SECRET   = env("ADMIN_WEB_SECRET") or os.environ.setdefault(
    "ADMIN_WEB_SECRET", secrets.token_urlsafe(32)
)
