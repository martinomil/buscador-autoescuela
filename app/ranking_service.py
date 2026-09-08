"""Orquesta la evaluacion: score deterministico + narrativa LLM + Evaluation.

Principio de coste (igual que en Fase 4): no se vuelve a evaluar una
autoescuela cuyos datos no han cambiado desde la ultima evaluacion.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.evaluator import evaluate as llm_evaluate
from app.models import Autoescuela, Evaluation, FieldValue
from app.scoring import duration_to_days, compute_score

logger = logging.getLogger(__name__)


class RankingError(Exception):
    pass


def get_fields_dict(session: Session, autoescuela_id: int) -> dict:
    stmt = select(FieldValue).where(FieldValue.autoescuela_id == autoescuela_id)
    return {fv.field_name: fv.value for fv in session.scalars(stmt).all()}


def get_latest_evaluation(session: Session, autoescuela_id: int) -> Evaluation | None:
    stmt = (
        select(Evaluation)
        .where(Evaluation.autoescuela_id == autoescuela_id)
        .order_by(Evaluation.created_at.desc())
    )
    return session.scalars(stmt).first()


def needs_reevaluation(session: Session, autoescuela_id: int) -> bool:
    """True si nunca se evaluo, o si algun dato se actualizo despues de la ultima evaluacion."""
    last_eval = get_latest_evaluation(session, autoescuela_id)
    if last_eval is None:
        return True

    stmt = (
        select(FieldValue.updated_at)
        .where(FieldValue.autoescuela_id == autoescuela_id)
        .order_by(FieldValue.updated_at.desc())
    )
    latest_field_update = session.scalars(stmt).first()
    if latest_field_update is None:
        return False
    return latest_field_update > last_eval.created_at


def evaluate_autoescuela(session: Session, autoescuela_id: int, model: str | None = None) -> Evaluation:
    autoescuela = session.get(Autoescuela, autoescuela_id)
    if autoescuela is None:
        raise RankingError(f"No existe ninguna autoescuela con id={autoescuela_id}")

    fields = get_fields_dict(session, autoescuela_id)
    if not fields:
        raise RankingError(
            f"Autoescuela {autoescuela_id} no tiene datos extraidos todavia "
            "(usa 'process-replies' antes de evaluarla)"
        )

    score_result = compute_score(fields)
    narrative = llm_evaluate(fields, score_result, model=model)

    evaluation = Evaluation(
        autoescuela_id=autoescuela_id,
        score=score_result["score"],
        verdict=score_result["verdict"],
        reasoning=narrative["reasoning"],
        pros=narrative["pros"],
        cons=narrative["cons"],
        risks=narrative["risks"],
        breakdown=score_result["breakdown"],
        model_used=model,
    )
    session.add(evaluation)
    session.flush()

    logger.info(
        "Evaluacion completada para autoescuela_id=%s: score=%s verdict=%s",
        autoescuela_id, score_result["score"], score_result["verdict"],
    )
    return evaluation


def evaluate_all(session: Session, model: str | None = None, force: bool = False) -> dict:
    autoescuelas = session.scalars(select(Autoescuela)).all()

    evaluated: list[Autoescuela] = []
    skipped: list[Autoescuela] = []
    no_data: list[Autoescuela] = []
    errors: list[Autoescuela] = []

    for autoescuela in autoescuelas:
        has_fields = session.scalars(
            select(FieldValue.id).where(FieldValue.autoescuela_id == autoescuela.id)
        ).first() is not None
        if not has_fields:
            no_data.append(autoescuela)
            continue

        if not force and not needs_reevaluation(session, autoescuela.id):
            skipped.append(autoescuela)
            continue

        try:
            evaluate_autoescuela(session, autoescuela.id, model=model)
            session.commit()
            evaluated.append(autoescuela)
        except Exception:
            logger.exception("Error evaluando autoescuela_id=%s", autoescuela.id)
            session.rollback()
            errors.append(autoescuela)

    return {"evaluated": evaluated, "skipped": skipped, "no_data": no_data, "errors": errors}


# --- Tabla de ranking (seccion 10 del brief) ---

SORT_KEYS = ("score", "inicio", "precio", "frecuencia", "examen", "localidad")

# Al ordenar, un dato desconocido se manda al final (nunca se interpreta
# como "mejor" ni "peor" por accidente al faltar).
_UNKNOWN_SORT_VALUE = float("inf")


def build_ranking_rows(session: Session) -> list[dict]:
    autoescuelas = session.scalars(select(Autoescuela)).all()
    rows = []
    for autoescuela in autoescuelas:
        fields = get_fields_dict(session, autoescuela.id)
        evaluation = get_latest_evaluation(session, autoescuela.id)

        practices_min = fields.get("practices_per_week_min")
        practices_max = fields.get("practices_per_week_max")
        practices_values = [v for v in (practices_min, practices_max) if v is not None]
        avg_practices = sum(practices_values) / len(practices_values) if practices_values else None

        rows.append({
            "id": autoescuela.id,
            "name": autoescuela.name,
            "city": autoescuela.city,
            "status": autoescuela.status,
            "score": evaluation.score if evaluation else None,
            "verdict": evaluation.verdict if evaluation else None,
            "waiting_time_to_start": fields.get("waiting_time_to_start"),
            "practice_price": fields.get("practice_price"),
            "practice_duration_minutes": fields.get("practice_duration_minutes"),
            "practices_per_week_min": practices_min,
            "practices_per_week_max": practices_max,
            "estimated_time_to_exam": fields.get("estimated_time_to_exam"),
            "_sort_inicio_days": duration_to_days(fields.get("waiting_time_to_start")),
            "_sort_examen_days": duration_to_days(fields.get("estimated_time_to_exam")),
            "_sort_frecuencia": avg_practices,
        })
    return rows



# Estados que, aunque tengan una puntuacion alta, no deben aparecer como
# recomendados: la autoescuela ya ha rechazado o descartado explicitamente
# al usuario, asi que se envian siempre al final del ranking sin importar
# el criterio de orden elegido.
DEMOTED_STATUSES = {"rejected", "bounced"}


def sort_ranking_rows(rows: list[dict], sort_by: str = "score") -> list[dict]:
    if sort_by not in SORT_KEYS:
        raise RankingError(f"Criterio de orden desconocido: {sort_by!r} (usa uno de {SORT_KEYS})")

    if sort_by == "score":
        key = lambda r: -(r["score"] if r["score"] is not None else -1)
    elif sort_by == "inicio":
        key = lambda r: r["_sort_inicio_days"] if r["_sort_inicio_days"] is not None else _UNKNOWN_SORT_VALUE
    elif sort_by == "precio":
        key = lambda r: r["practice_price"] if r["practice_price"] is not None else _UNKNOWN_SORT_VALUE
    elif sort_by == "frecuencia":
        key = lambda r: -(r["_sort_frecuencia"] if r["_sort_frecuencia"] is not None else -1)
    elif sort_by == "examen":
        key = lambda r: r["_sort_examen_days"] if r["_sort_examen_days"] is not None else _UNKNOWN_SORT_VALUE
    else:  # localidad
        key = lambda r: (r["city"] or "", r["name"])

    demoted_key = lambda r: (1 if r.get("status") in DEMOTED_STATUSES else 0, key(r))
    return sorted(rows, key=demoted_key)
