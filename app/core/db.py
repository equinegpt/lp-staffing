# app/core/db.py
from __future__ import annotations
import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

# Pull from env if set (Render), otherwise fall back to your config default.
try:
    from app.core.config import DATABASE_URL as CONFIG_DATABASE_URL  # optional fallback
except Exception:
    CONFIG_DATABASE_URL = "sqlite:///./dev.db"

DATABASE_URL = os.getenv("DATABASE_URL", CONFIG_DATABASE_URL)

# Sync engine (your code uses sa.text(...) and engine.connect())
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)

def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Optional schema bootstrap + seed (kept from your version) ---
from app.core.constants import ALLOWED_ROLES, ALLOWED_LOCS

def bootstrap_schema() -> None:
    """Create tables + seed basic data. Safe to run repeatedly."""
    with engine.begin() as c:
        try:
            c.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
        except Exception:
            pass

        c.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS role (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          code  TEXT UNIQUE NOT NULL,
          label TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")

        c.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS location (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          code TEXT UNIQUE NOT NULL,
          name TEXT NOT NULL,
          timezone TEXT NOT NULL DEFAULT 'Australia/Melbourne',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")

        c.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS staff (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          given_name  TEXT NOT NULL,
          family_name TEXT NOT NULL,
          display_name TEXT NOT NULL,
          mobile TEXT NOT NULL UNIQUE,
          email  TEXT,
          start_date DATE NOT NULL,
          end_date   DATE,
          status TEXT NOT NULL DEFAULT 'ACTIVE',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")

        c.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS staff_role_assignment (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          staff_id UUID NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
          role_id  UUID NOT NULL REFERENCES role(id),
          location_id UUID REFERENCES location(id),
          effective_start DATE NOT NULL,
          effective_end   DATE,
          priority INTEGER NOT NULL DEFAULT 0,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")

        c.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS device (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          staff_id UUID NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
          platform TEXT NOT NULL CHECK (platform IN ('iOS','Android')),
          token TEXT NOT NULL UNIQUE,
          last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")

        c.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_sra_staff_dates ON staff_role_assignment (staff_id, effective_start, effective_end)")
        c.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_sra_role       ON staff_role_assignment (role_id)")
        c.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_sra_location   ON staff_role_assignment (location_id)")

        # Seed/refresh roles
        c.exec_driver_sql("""
        INSERT INTO role (code, label) VALUES
          ('RIDER','Rider'),
          ('STRAPPER','Strapper'),
          ('MEDIA','Media'),
          ('TREADMILL','Treadmill'),
          ('WATERWALKERS','WaterWalkers'),
          ('FARRIER','Farrier'),
          ('VET','Vet')
        ON CONFLICT (code) DO UPDATE SET label = EXCLUDED.label
        """)

        # Seed/refresh locations
        c.exec_driver_sql("""
        INSERT INTO location (code, name, timezone) VALUES
          ('FARM','Farm','Australia/Melbourne'),
          ('FLEMINGTON','Flemington','Australia/Melbourne'),
          ('PAKENHAM','Pakenham','Australia/Melbourne')
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, timezone = EXCLUDED.timezone
        """)

        roles_csv = ", ".join([f"'{x}'" for x in ALLOWED_ROLES])
        locs_csv  = ", ".join([f"'{x}'" for x in ALLOWED_LOCS])

        c.exec_driver_sql(f"""
        DELETE FROM role r
         WHERE r.code NOT IN ({roles_csv})
           AND NOT EXISTS (SELECT 1 FROM staff_role_assignment a WHERE a.role_id = r.id)
        """)
        c.exec_driver_sql(f"""
        DELETE FROM location l
         WHERE l.code NOT IN ({locs_csv})
           AND NOT EXISTS (SELECT 1 FROM staff_role_assignment a WHERE a.location_id = l.id)
        """)

__all__ = ["engine", "SessionLocal", "get_session", "bootstrap_schema", "DATABASE_URL"]
