"""SQLite local, Postgres en producción. SQLAlchemy en ambos."""
import logging
import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if not DATABASE_URL:
    # Fallback local: SQLite. Solo creamos `data/` cuando estamos en local
    # (en Vercel el filesystem es read-only excepto /tmp).
    local_data = ROOT / "data"
    local_data.mkdir(exist_ok=True)
    db_path = local_data / "agency.db"
    DATABASE_URL = f"sqlite:///{db_path}"
    logger.info(f"DATABASE_URL no set, usando SQLite en {db_path}")

# Neon te devuelve URLs con `postgresql://` o `postgres://`. SQLAlchemy 2.x prefiere `postgresql+psycopg://`
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

connect_args = {}
if DATABASE_URL.startswith("sqlite:"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)


def is_postgres() -> bool:
    return DATABASE_URL.startswith("postgresql")
