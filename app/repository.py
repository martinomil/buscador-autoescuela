"""Consultas comunes sobre autoescuelas, reutilizadas por el CLI y la interfaz."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.gmail_reader import apply_reply_side_effects
from app.models import Autoescuela, EmailMessage, FieldValue


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


def list_real_reply_messages(session: Session, statuses: set[str] | None = None) -> list[EmailMessage]:
    """Respuestas entrantes reales (no rebotes ni tests) asociadas a una
    autoescuela, opcionalmente filtradas por el estado actual de esta."""
    stmt = (
        select(EmailMessage)
        .join(Autoescuela, EmailMessage.autoescuela_id == Autoescuela.id)
        .where(
            EmailMessage.direction == "inbound",
            EmailMessage.kind.is_(None),
            EmailMessage.autoescuela_id.is_not(None),
        )
        .order_by(EmailMessage.created_at.desc())
    )
    if statuses:
        stmt = stmt.where(Autoescuela.status.in_(statuses))
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


def list_field_values(session: Session, autoescuela_id: int) -> list[FieldValue]:
    stmt = (
        select(FieldValue)
        .where(FieldValue.autoescuela_id == autoescuela_id)
        .order_by(FieldValue.field_name)
    )
    return list(session.scalars(stmt).all())


def set_field_value_manual(session: Session, autoescuela_id: int, field_name: str, value, updated_by: str = "manual") -> FieldValue:
    """Corrige a mano el valor de un campo. Conserva el valor de IA anterior
    en ai_original_value y marca source='manual' para que la extraccion
    automatica no lo vuelva a pisar (ver app.llm.pipeline)."""
    stmt = select(FieldValue).where(
        FieldValue.autoescuela_id == autoescuela_id,
        FieldValue.field_name == field_name,
    )
    field_value = session.scalars(stmt).first()

    if field_value is None:
        field_value = FieldValue(autoescuela_id=autoescuela_id, field_name=field_name)
        session.add(field_value)
    elif field_value.source == "ai":
        field_value.ai_original_value = field_value.value

    field_value.value = value
    field_value.source = "manual"
    field_value.updated_by = updated_by

    session.flush()
    return field_value
