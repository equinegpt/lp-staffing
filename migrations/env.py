# migrations/env.py
import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool
from dotenv import load_dotenv

# --- Load .env explicitly from project root
env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=env_path, override=True)

db_url = os.getenv("DATABASE_URL")
if not db_url or not db_url.strip():
    raise RuntimeError(f"DATABASE_URL not set or blank. .env at {env_path} must define it.")

# Alembic Config object (only used for logging here)
config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = None  # no autogenerate in this skeleton

def run_migrations_offline():
    context.configure(
        url=db_url,
        compare_type=True,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    engine = create_engine(db_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
