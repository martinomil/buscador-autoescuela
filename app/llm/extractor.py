"""Extraccion estructurada de una respuesta de autoescuela mediante Claude.

Regla de oro: NUNCA inventar. Se usa "tool use" (function calling) de la API
de Anthropic para forzar una salida estructurada, y ningun campo de datos es
obligatorio en el schema: si la autoescuela no menciona un dato, el modelo
debe omitir ese campo por completo (se guarda como None), nunca adivinarlo.
"""
from __future__ import annotations

import logging

import anthropic

from app import config

logger = logging.getLogger(__name__)

EXTRACTION_TOOL_NAME = "extract_autoescuela_reply"

# Campos de datos: opcionales en el schema a proposito (el modelo los omite
# si no hay informacion, en vez de rellenarlos con una suposicion).
EXTRACTION_FIELDS = (
    "waiting_time_to_start",
    "earliest_start_date",
    "practice_price",
    "practice_duration_minutes",
    "practices_per_week_min",
    "practices_per_week_max",
    "estimated_time_to_exam",
    "earliest_exam_date",
    "enrollment_fee",
    "exam_fee",
    "other_fees",
    "availability",
    "requirements",
    "relevant_conditions",
    "accepts_already_passed_theory",
    "information_is_uncertain",
)

# Metadatos: siempre presentes (aunque sea con valores vacios/False).
META_FIELDS = ("follow_up_needed", "missing_info", "notes")

_DURATION_SCHEMA = {
    "type": "object",
    "description": "Duracion aproximada tal como la indica la autoescuela.",
    "properties": {
        "value": {"type": "number"},
        "unit": {"type": "string", "enum": ["days", "weeks", "months"]},
    },
    "required": ["value", "unit"],
}

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "waiting_time_to_start": _DURATION_SCHEMA,
        "earliest_start_date": {
            "type": "string",
            "description": (
                "Fecha o periodo en el que podrian empezar, tal cual lo dice la "
                "autoescuela (p.ej. 'diciembre', 'en 2 semanas', 'inmediatamente'). "
                "No inventes un año si no se menciona."
            ),
        },
        "practice_price": {"type": "number", "description": "Precio de UNA practica, en euros."},
        "practice_duration_minutes": {"type": "integer"},
        "practices_per_week_min": {
            "type": "integer",
            "description": "Si dan un rango (p.ej. '3-4 practicas/semana') este es el minimo; si dan un solo numero, usa ese mismo valor.",
        },
        "practices_per_week_max": {
            "type": "integer",
            "description": "Igual que practices_per_week_min pero el maximo del rango (o el mismo valor si solo dan un numero).",
        },
        "estimated_time_to_exam": _DURATION_SCHEMA,
        "earliest_exam_date": {"type": "string"},
        "enrollment_fee": {"type": "number", "description": "Coste de la matricula, en euros."},
        "exam_fee": {"type": "number", "description": "Coste del examen practico, en euros, si se menciona por separado."},
        "other_fees": {"type": "string", "description": "Cualquier otro coste mencionado, en texto libre."},
        "availability": {"type": "string", "description": "Disponibilidad general descrita, en texto libre (p.ej. 'alta demanda ahora mismo')."},
        "requirements": {"type": "string", "description": "Requisitos mencionados (p.ej. documentacion, certificado medico)."},
        "relevant_conditions": {"type": "string", "description": "Cualquier otra condicion relevante no cubierta por los campos anteriores."},
        "accepts_already_passed_theory": {
            "type": "boolean",
            "description": "true/false SOLO si la autoescuela confirma o descarta explicitamente aceptar alumnos con el teorico ya aprobado.",
        },
        "information_is_uncertain": {
            "type": "boolean",
            "description": "true si la respuesta usa lenguaje aproximado o incierto (p.ej. 'aproximadamente', 'puede variar', 'no estoy seguro').",
        },
        "follow_up_needed": {
            "type": "boolean",
            "description": "true si, tras esta respuesta, quedan preguntas relevantes (de las que enviamos) sin responder.",
        },
        "missing_info": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Lista breve en español de que informacion sigue faltando. Lista vacia si no falta nada importante.",
        },
        "notes": {
            "type": "string",
            "description": (
                "Observaciones breves en español sobre matices, ambiguedades o contexto "
                "util que no encaje en los campos anteriores. Cadena vacia si no hay nada que anadir."
            ),
        },
    },
    "required": ["follow_up_needed", "missing_info", "notes"],
}

SYSTEM_PROMPT = f"""Eres un asistente que extrae informacion estructurada de respuestas por email de autoescuelas de Galicia (España) a una consulta sobre clases practicas del permiso B.

REGLA MAS IMPORTANTE: Solo extraes informacion EXPLICITAMENTE indicada en el email de la autoescuela. Si un dato no se menciona o no queda claro, OMITE ese campo por completo en tu respuesta. Nunca adivines, estimes ni infieras un valor que no este dicho. Por ejemplo, "tenemos bastante lista de espera" NO permite rellenar waiting_time_to_start con un numero concreto: omite ese campo y explica la situacion en notes y/o missing_info.

Los emails pueden estar en español, gallego o ingles; pueden venir con firma, ser muy cortos o muy largos, contener HTML residual, y usar formatos como precios "30€", "30 euros", "30 €/clase", tiempos "3 semanas", "mes y medio", "hasta diciembre", y rangos "3-4 practicas" (usa min=3, max=4; si solo dan un numero, usa ese mismo valor en min y max).

Si la informacion es aproximada o incierta (p.ej. "unas 3 semanas", "puede variar"), extrae el mejor valor numerico posible pero marca information_is_uncertain=true y explica el matiz en notes.

Se te da el email original que enviamos (con las preguntas) y la respuesta de la autoescuela. Usa las preguntas para decidir follow_up_needed y missing_info: si alguna pregunta relevante sigue sin responderse, listala brevemente en missing_info (en español) y pon follow_up_needed=true. Si la respuesta cubre razonablemente lo preguntado, follow_up_needed=false y missing_info=[].

Responde SIEMPRE llamando a la herramienta {EXTRACTION_TOOL_NAME} con los datos estructurados."""


class ExtractionError(Exception):
    """Fallo al obtener una extraccion estructurada del LLM."""


def build_user_message(original_question_text: str, reply_text: str) -> str:
    return (
        f"EMAIL QUE ENVIAMOS (preguntas):\n{original_question_text or '(no disponible)'}\n\n"
        f"---\n\nRESPUESTA DE LA AUTOESCUELA:\n{reply_text}"
    )


def _get_client() -> anthropic.Anthropic:
    if not config.ANTHROPIC_API_KEY:
        raise ExtractionError(
            "Falta ANTHROPIC_API_KEY en .env. Consigue una clave en "
            "https://console.anthropic.com/ y añadela antes de ejecutar la extraccion."
        )
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def extract_reply(
    original_question_text: str,
    reply_text: str,
    model: str | None = None,
    client: anthropic.Anthropic | None = None,
) -> dict:
    """Llama al LLM y devuelve un dict con EXTRACTION_FIELDS + META_FIELDS.

    Los campos de datos no mencionados en la respuesta vienen como None
    (nunca se inventan). `client` es inyectable para tests.
    """
    client = client or _get_client()
    model = model or config.LLM_MODEL_CHEAP

    message = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[
            {
                "name": EXTRACTION_TOOL_NAME,
                "description": "Registra los datos estructurados extraidos de la respuesta de la autoescuela.",
                "input_schema": EXTRACTION_SCHEMA,
            }
        ],
        tool_choice={"type": "tool", "name": EXTRACTION_TOOL_NAME},
        messages=[{"role": "user", "content": build_user_message(original_question_text, reply_text)}],
    )

    tool_use = next((block for block in message.content if getattr(block, "type", None) == "tool_use"), None)
    if tool_use is None:
        raise ExtractionError("El modelo no devolvio una respuesta estructurada (tool_use)")

    raw = tool_use.input
    result = {field: raw.get(field) for field in EXTRACTION_FIELDS}
    result["follow_up_needed"] = bool(raw.get("follow_up_needed", False))
    result["missing_info"] = raw.get("missing_info") or []
    result["notes"] = raw.get("notes") or ""
    return result
