"""Modelos SQLAlchemy para la base de datos de autoescuelas.

Diseno pensado para poder ampliarse sin migraciones dolorosas:
- Los datos extraidos por IA se guardan como pares (field_name, value) en
  FieldValue en lugar de una columna por campo, para poder anadir nuevos
  campos de extraccion sin tocar el esquema.
- Cada EmailMessage guarda el texto original completo, para poder comparar
  siempre "lo que dijo la autoescuela" con "lo que entendio la IA".
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# Estados posibles de una autoescuela. Se guarda como texto libre (no ENUM de
# base de datos) para poder anadir estados nuevos sin migrar el esquema.
AUTOESCUELA_STATUSES = (
    "not_contacted",
    "email_sent",
    "replied",
    "follow_up_needed",
    "interested",
    "rejected",
    "selected",
)

EMAIL_DIRECTIONS = ("outbound", "inbound")
EMAIL_KINDS = ("initial", "follow_up", "test", "other")
FIELD_VALUE_SOURCES = ("ai", "manual")


class Autoescuela(Base):
    __tablename__ = "autoescuelas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    website: Mapped[str | None] = mapped_column(String(300), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(30), nullable=False, default="not_contacted")

    first_contact_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reply_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    emails_sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replies_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    emails: Mapped[list["EmailMessage"]] = relationship(
        back_populates="autoescuela", cascade="all, delete-orphan"
    )
    field_values: Mapped[list["FieldValue"]] = relationship(
        back_populates="autoescuela", cascade="all, delete-orphan"
    )
    extraction_results: Mapped[list["ExtractionResult"]] = relationship(
        back_populates="autoescuela", cascade="all, delete-orphan"
    )
    evaluations: Mapped[list["Evaluation"]] = relationship(
        back_populates="autoescuela", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - solo para debug
        return f"<Autoescuela {self.name!r} ({self.city}) status={self.status}>"


class EmailMessage(Base):
    __tablename__ = "email_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Nullable: una respuesta entrante que no se ha podido asociar a ninguna
    # autoescuela automaticamente se guarda igualmente (nunca se descarta),
    # a la espera de asignacion manual.
    autoescuela_id: Mapped[int | None] = mapped_column(ForeignKey("autoescuelas.id"), nullable=True)

    direction: Mapped[str] = mapped_column(String(20), nullable=False)  # outbound | inbound
    kind: Mapped[str | None] = mapped_column(String(20), nullable=True)  # initial | follow_up | other

    gmail_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    sender: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)

    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)

    timestamp: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Marca si esta respuesta entrante ya ha sido procesada por el LLM.
    processed: Mapped[bool] = mapped_column(default=False)

    autoescuela: Mapped[Autoescuela | None] = relationship(back_populates="emails")
    extraction_results: Mapped[list["ExtractionResult"]] = relationship(back_populates="email_message")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<EmailMessage {self.direction} autoescuela_id={self.autoescuela_id} subject={self.subject!r}>"


class ExtractionResult(Base):
    """Resultado bruto de una llamada al LLM sobre un email concreto."""

    __tablename__ = "extraction_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_message_id: Mapped[int] = mapped_column(ForeignKey("email_messages.id"), nullable=False)
    autoescuela_id: Mapped[int] = mapped_column(ForeignKey("autoescuelas.id"), nullable=False)

    raw_output: Mapped[dict] = mapped_column(JSON, nullable=False)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)

    follow_up_needed: Mapped[bool] = mapped_column(default=False)
    follow_up_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    autoescuela: Mapped[Autoescuela] = relationship(back_populates="extraction_results")
    email_message: Mapped[EmailMessage] = relationship(back_populates="extraction_results")


class FieldValue(Base):
    """Valor actual de un campo extraido para una autoescuela.

    Una fila por (autoescuela_id, field_name). Si el valor se corrige a mano,
    `source` pasa a "manual" y se conserva el valor original de la IA en
    `ai_original_value`.
    """

    __tablename__ = "field_values"
    __table_args__ = (UniqueConstraint("autoescuela_id", "field_name", name="uq_field_per_autoescuela"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    autoescuela_id: Mapped[int] = mapped_column(ForeignKey("autoescuelas.id"), nullable=False)

    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[dict | list | str | float | int | bool | None] = mapped_column(JSON, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[str] = mapped_column(String(10), nullable=False, default="ai")  # ai | manual
    ai_original_value: Mapped[dict | list | str | float | int | bool | None] = mapped_column(JSON, nullable=True)

    extraction_result_id: Mapped[int | None] = mapped_column(ForeignKey("extraction_results.id"), nullable=True)

    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    autoescuela: Mapped[Autoescuela] = relationship(back_populates="field_values")


class Evaluation(Base):
    """Puntuacion/veredicto de una autoescuela segun las prioridades del usuario."""

    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    autoescuela_id: Mapped[int] = mapped_column(ForeignKey("autoescuelas.id"), nullable=False)

    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verdict: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    pros: Mapped[list | None] = mapped_column(JSON, nullable=True)
    cons: Mapped[list | None] = mapped_column(JSON, nullable=True)
    risks: Mapped[list | None] = mapped_column(JSON, nullable=True)
    breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    autoescuela: Mapped[Autoescuela] = relationship(back_populates="evaluations")
