import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


# Project root
ROOT_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT_DIR / ".env"

if not ENV_FILE.exists():
    ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


# Load local .env
# Do NOT override Hugging Face environment variables/secrets
load_dotenv(ENV_FILE, override=False)


# Database URL
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://school_user:school_password@localhost:5432/school_attendance",
)


# Normalize PostgreSQL URL formats
# This allows postgres:// and postgresql:// URLs to work with psycopg2.

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql+psycopg2://",
        1,
    )

elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://",
        "postgresql+psycopg2://",
        1,
    )


# SQLAlchemy engine configuration
engine_kwargs = {
    "pool_pre_ping": True,
}


if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {
        "check_same_thread": False
    }

else:
    engine_kwargs.update(
        {
            "pool_size": int(os.getenv("DB_POOL_SIZE", "5")),
            "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "10")),
            "pool_timeout": 15,
            "pool_recycle": 300,
            "pool_use_lifo": True,
        }
    )


# Create database engine
engine = create_engine(
    DATABASE_URL,
    **engine_kwargs,
)


# Database session
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    bind=engine,
)


# Declarative base
Base = declarative_base()


# FastAPI database dependency
def get_db():
    db = SessionLocal()

    try:
        yield db

    finally:
        db.close()