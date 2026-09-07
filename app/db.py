"""Configuracion del engine y sesiones de SQLAlchemy."""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import config
from app.models import Base


def make_engine(db_path=None):
    path = db_path or config.DATABASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}", future=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db(engine_=None) -> None:
    """Crea las tablas que no existan todavia."""
    Base.metadata.create_all(engine_ or engine)


@contextmanager
def get_session() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
