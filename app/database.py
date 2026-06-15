import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/scadenziario.db")

# SQLite requires check_same_thread=False when the engine is shared across threads
# (FastAPI's default behaviour with sync sessions). Ignored by other databases.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Return the current UTC time (timezone-aware). Used as default for DateTime columns."""
    return datetime.now(timezone.utc)


def get_db():
    """
    FastAPI dependency: provides one DB session per request, then closes it.
    Usage in a route: db: Session = Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
