"""Extraccion estructurada de una respuesta de autoescuela mediante un LLM.

Regla de oro: NUNCA inventar. Se usa "tool use" / function calling para
forzar una salida estructurada, y ningun campo de datos es obligatorio en el
schema: si la autoescuela no menciona un dato, el modelo debe omitir ese
campo por completo (se guarda como None), nunca adivinarlo.

Soporta dos proveedores intercambiables (LLM_PROVIDER en .env):
- "anthropic" (de pago, mas fiable siguiendo el schema).
- "ollama" (gratis, modelo local, algo menos fiable con el formato exacto:
  por eso toda salida pasa por _coerce_field, que descarta silenciosamente
  cualquier valor con una forma inesperada en vez de guardarlo tal cual).
"""
from __future__ import annotations

import json
import logging

import anthropic
import requests

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


def default_model_for_provider(provider: str | None = None) -> str:
    """Modelo por defecto segun el proveedor (para elegir Y para loguear qué se uso)."""
    provider = (provider or config.LLM_PROVIDER or "anthropic").lower()
    return config.LLM_MODEL_OLLAMA if provider == "ollama" else config.LLM_MODEL_CHEAP


def build_user_message(original_question_text: str, reply_text: str) -> str:
    return (
        f"EMAIL QUE ENVIAMOS (preguntas):\n{original_question_text or '(no disponible)'}\n\n"
        f"---\n\nRESPUESTA DE LA AUTOESCUELA:\n{reply_text}"
    )


def _get_anthropic_client() -> anthropic.Anthropic:
    if not config.ANTHROPIC_API_KEY:
        raise ExtractionError(
            "Falta ANTHROPIC_API_KEY en .env. Consigue una clave en "
            "https://console.anthropic.com/ y añadela antes de ejecutar la extraccion."
        )
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _call_anthropic(user_message: str, model: str, client: anthropic.Anthropic | None) -> dict:
    client = client or _get_anthropic_client()

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
        messages=[{"role": "user", "content": user_message}],
    )

    tool_use = next((block for block in message.content if getattr(block, "type", None) == "tool_use"), None)
    if tool_use is None:
        raise ExtractionError("El modelo no devolvio una respuesta estructurada (tool_use)")
    return tool_use.input


def _call_ollama(user_message: str, model: str) -> dict:
    url = f"{config.OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": EXTRACTION_TOOL_NAME,
                    "description": "Registra los datos estructurados extraidos de la respuesta de la autoescuela.",
                    "parameters": EXTRACTION_SCHEMA,
                },
            }
        ],
        "stream": False,
    }

    try:
        response = requests.post(url, json=payload, timeout=120)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ExtractionError(
            f"No se pudo contactar con Ollama en {config.OLLAMA_BASE_URL}. "
            f"¿Esta 'ollama serve' en marcha y el modelo {model!r} descargado ('ollama pull {model}')? "
            f"Detalle: {exc}"
        ) from exc

    data = response.json()
    tool_calls = data.get("message", {}).get("tool_calls")
    if not tool_calls:
        raise ExtractionError(
            f"El modelo local {model!r} no devolvio una llamada a herramienta (tool_calls). "
            "Prueba con otro modelo que soporte tool calling (p.ej. llama3.1 o qwen2.5)."
        )

    arguments = tool_calls[0]["function"]["arguments"]
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    return arguments


# Que "forma" de valor se espera por campo, para poder descartar con
# seguridad una salida mal formada de un modelo menos fiable (frecuente con
# modelos locales pequeños) en vez de guardarla tal cual.
_FIELD_KINDS = {
    "waiting_time_to_start": "duration",
    "earliest_start_date": "string",
    "practice_price": "number",
    "practice_duration_minutes": "integer",
    "practices_per_week_min": "integer",
    "practices_per_week_max": "integer",
    "estimated_time_to_exam": "duration",
    "earliest_exam_date": "string",
    "enrollment_fee": "number",
    "exam_fee": "number",
    "other_fees": "string",
    "availability": "string",
    "requirements": "string",
    "relevant_conditions": "string",
    "accepts_already_passed_theory": "boolean",
    "information_is_uncertain": "boolean",
}

_VALID_DURATION_UNITS = {"days", "weeks", "months"}


def _coerce_field(field_name: str, value):
    """Valida/normaliza un valor segun el tipo esperado del campo.

    Si la forma no encaja (p.ej. un modelo local envuelve un numero en un
    objeto por error), se descarta devolviendo None y se registra un aviso,
    en vez de guardar un dato con forma inconsistente.
    """
    if value is None:
        return None

    kind = _FIELD_KINDS[field_name]
    try:
        if kind == "duration":
            if not isinstance(value, dict):
                raise ValueError("se esperaba un objeto {value, unit}")
            duration_value, unit = value.get("value"), value.get("unit")
            if isinstance(duration_value, bool) or not isinstance(duration_value, (int, float)):
                raise ValueError("value no numerico")
            if unit not in _VALID_DURATION_UNITS:
                raise ValueError(f"unit invalida: {unit!r}")
            return {"value": duration_value, "unit": unit}

        if kind in ("number", "integer"):
            if isinstance(value, dict):
                value = value.get("value")  # algunos modelos locales envuelven el numero
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("no numerico")
            return int(value) if kind == "integer" else float(value)

        if kind == "string":
            if isinstance(value, (dict, list)):
                raise ValueError("se esperaba texto")
            text = str(value).strip()
            return text or None

        if kind == "boolean":
            if not isinstance(value, bool):
                raise ValueError("no booleano")
            return value

    except ValueError as exc:
        logger.warning("Campo %r con forma inesperada (%s): %r, se descarta", field_name, exc, value)
        return None

    return None


def extract_reply(
    original_question_text: str,
    reply_text: str,
    model: str | None = None,
    client: anthropic.Anthropic | None = None,
    provider: str | None = None,
) -> dict:
    """Llama al LLM y devuelve un dict con EXTRACTION_FIELDS + META_FIELDS.

    Los campos de datos no mencionados en la respuesta vienen como None
    (nunca se inventan). `client` es inyectable para tests (solo se usa con
    provider="anthropic"). `provider` por defecto viene de LLM_PROVIDER en .env.
    """
    provider = (provider or config.LLM_PROVIDER or "anthropic").lower()
    user_message = build_user_message(original_question_text, reply_text)

    if provider == "anthropic":
        raw = _call_anthropic(user_message, model or config.LLM_MODEL_CHEAP, client)
    elif provider == "ollama":
        raw = _call_ollama(user_message, model or config.LLM_MODEL_OLLAMA)
    else:
        raise ExtractionError(f"LLM_PROVIDER desconocido: {provider!r} (usa 'anthropic' u 'ollama')")

    result = {field: _coerce_field(field, raw.get(field)) for field in EXTRACTION_FIELDS}
    result["follow_up_needed"] = bool(raw.get("follow_up_needed", False))
    result["missing_info"] = raw.get("missing_info") or []
    result["notes"] = raw.get("notes") or ""
    return result
