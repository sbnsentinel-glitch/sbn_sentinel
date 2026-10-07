from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from typing import Generator
from app.core.config import settings

# Database URL is environment-driven.
# Default (dev): sqlite:///./sentinel.db
# Production:    set SQLALCHEMY_DATABASE_URL=postgresql://user:pass@host/db
SQLALCHEMY_DATABASE_URL = settings.SQLALCHEMY_DATABASE_URL

# connect_args required by SQLite; not used for PostgreSQL
_connect_args = {"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def ensure_schema_compatibility():
    try:
        from app.models import (  # noqa
            event, signal, user, otp, governance_storage, intelligence,
            organization, encounter, clinic, billing, insurance, connector,
            audit, telemetry, settings, rule, evidence
        )
        from app.services.cursor_store import CursorModel  # noqa: F401
    except Exception:
        pass
    # F-28: Transitioned to Alembic migrations for production PostgreSQL.
    # However, SQLite (used for tests) requires create_all to initialize memory DBs.
    if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
        Base.metadata.create_all(bind=engine)


ensure_schema_compatibility()


def get_db() -> Generator:
    """
    Dependency injector that provides a database session per request.
    Ensures the session is always closed after the request completes.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
