"""Consultas comunes sobre autoescuelas, reutilizadas por el CLI y la interfaz."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.gmail_reader import apply_reply_side_effects
from app.models import Autoescuela, EmailMessage


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


def list_emails_for_autoescuela(session: Session, autoescuela_id: int) -> list[EmailMessage]:
    stmt = (
        select(EmailMessage)
        .where(EmailMessage.autoescuela_id == autoescuela_id)
        .order_by(EmailMessage.timestamp.is_(None), EmailMessage.timestamp, EmailMessage.created_at)
    )
    return list(session.scalars(stmt).all())


def list_unmatched_inbound(session: Session) -> list[EmailMessage]:
    stmt = (
        select(EmailMessage)
        .where(EmailMessage.autoescuela_id.is_(None), EmailMessage.direction == "inbound")
        .order_by(EmailMessage.created_at)
    )
    return list(session.scalars(stmt).all())


def assign_email_to_autoescuela(session: Session, email_message_id: int, autoescuela_id: int) -> EmailMessage:
    email_message = session.get(EmailMessage, email_message_id)
    if email_message is None:
        raise ValueError(f"No existe ningun EmailMessage con id={email_message_id}")

    autoescuela = session.get(Autoescuela, autoescuela_id)
    if autoescuela is None:
        raise ValueError(f"No existe ninguna autoescuela con id={autoescuela_id}")

    email_message.autoescuela_id = autoescuela_id
    if email_message.direction == "inbound":
        apply_reply_side_effects(autoescuela, email_message.timestamp)

    session.flush()
    return email_message
