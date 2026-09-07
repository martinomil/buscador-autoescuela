"""Consultas comunes sobre autoescuelas, reutilizadas por el CLI y la interfaz."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Autoescuela


def list_autoescuelas(session: Session, status: str | None = None) -> list[Autoescuela]:
    stmt = select(Autoescuela).order_by(Autoescuela.name)
    if status:
        stmt = stmt.where(Autoescuela.status == status)
    return list(session.scalars(stmt).all())


def get_autoescuela(session: Session, autoescuela_id: int) -> Autoescuela | None:
    return session.get(Autoescuela, autoescuela_id)


def count_by_status(session: Session) -> dict[str, int]:
    autoescuelas = session.scalars(select(Autoescuela)).all()
    counts: dict[str, int] = {}
    for a in autoescuelas:
        counts[a.status] = counts.get(a.status, 0) + 1
    return counts
