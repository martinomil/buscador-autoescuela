"""Razonamiento cualitativo (reasoning/pros/cons/risks) sobre una autoescuela.

IMPORTANTE: la puntuacion (0-100) es siempre deterministica (ver
app.scoring) y este modulo NUNCA la modifica. Aqui solo se le pide al LLM
que explique en lenguaje natural una puntuacion ya calculada, aportando
matices (pros, contras, riesgos) que una formula rigida no puede capturar,
sin perder la trazabilidad del score en sí.

Soporta los mismos dos proveedores que app.llm.extractor (LLM_PROVIDER).
"""
from __future__ import annotations

import json
import logging

import anthropic
import requests

from app import config

logger = logging.getLogger(__name__)

EVALUATION_TOOL_NAME = "evaluate_autoescuela"

EVALUATION_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {
            "type": "string",
            "description": "1-3 frases en español explicando la puntuacion en el contexto de las prioridades del usuario.",
        },
        "pros": {"type": "array", "items": {"type": "string"}, "description": "Puntos a favor, en español. Lista vacia si no hay ninguno claro."},
        "cons": {"type": "array", "items": {"type": "string"}, "description": "Puntos en contra, en español. Lista vacia si no hay ninguno claro."},
        "risks": {"type": "array", "items": {"type": "string"}, "description": "Riesgos o incertidumbres, en español. Lista vacia si no hay ninguno claro."},
    },
    "required": ["reasoning", "pros", "cons", "risks"],
}

SYSTEM_PROMPT = f"""Eres un asistente que ayuda a evaluar autoescuelas para alguien que quiere aprobar el examen practico del permiso B lo antes posible.

Prioridades del usuario, de mas a menos importante:
1. Empezar las practicas cuanto antes.
2. Poder hacer muchas practicas por semana.
3. Que el tiempo entre empezar las practicas y el examen sea corto.
4. Precio razonable (NO es la prioridad principal: una autoescuela algo mas cara que permite mas practica semanal puede ser mejor opcion).

Se te da una puntuacion ya calculada (0-100) y su desglose por componente, junto con los datos extraidos de la respuesta de la autoescuela. Tu trabajo NO es cambiar la puntuacion ni inventar datos nuevos: es explicar en 1-3 frases, en español, por que tiene ese resultado segun las prioridades de arriba, y dar listas breves de pros, contras y riesgos basandote SOLO en los datos proporcionados. Si un dato figura como "unknown" o "desconocido" en el desglose, puedes mencionar la incertidumbre que eso genera, pero no inventes una cifra que no este en los datos.

Responde SIEMPRE llamando a la herramienta {EVALUATION_TOOL_NAME}."""


class EvaluationError(Exception):
    """Fallo al obtener una explicacion estructurada del LLM."""


def build_user_message(fields: dict, score_result: dict) -> str:
    lines = ["DATOS EXTRAIDOS DE LA AUTOESCUELA (campos no mencionados se omiten):"]
    for name, value in sorted(fields.items()):
        if value not in (None, "", []):
            lines.append(f"- {name}: {value}")

    lines.append("")
    lines.append(f"PUNTUACION CALCULADA: {score_result['score']}/100 ({score_result['verdict']})")
    lines.append("DESGLOSE POR COMPONENTE:")
    for component in score_result["breakdown"].values():
        lines.append(f"- {component['label']}: {component['points']}/{component['max']} (dato {component['status']})")

    return "\n".join(lines)


def _get_anthropic_client() -> anthropic.Anthropic:
    if not config.ANTHROPIC_API_KEY:
        raise EvaluationError(
            "Falta ANTHROPIC_API_KEY en .env. Consigue una clave en "
            "https://console.anthropic.com/ o usa LLM_PROVIDER=ollama."
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
                "name": EVALUATION_TOOL_NAME,
                "description": "Registra la explicacion cualitativa de la puntuacion de la autoescuela.",
                "input_schema": EVALUATION_SCHEMA,
            }
        ],
        tool_choice={"type": "tool", "name": EVALUATION_TOOL_NAME},
        messages=[{"role": "user", "content": user_message}],
    )
    tool_use = next((block for block in message.content if getattr(block, "type", None) == "tool_use"), None)
    if tool_use is None:
        raise EvaluationError("El modelo no devolvio una respuesta estructurada (tool_use)")
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
                    "name": EVALUATION_TOOL_NAME,
                    "description": "Registra la explicacion cualitativa de la puntuacion de la autoescuela.",
                    "parameters": EVALUATION_SCHEMA,
                },
            }
        ],
        "stream": False,
    }
    try:
        response = requests.post(url, json=payload, timeout=120)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise EvaluationError(
            f"No se pudo contactar con Ollama en {config.OLLAMA_BASE_URL}. "
            f"¿Esta 'ollama serve' en marcha y el modelo {model!r} descargado? Detalle: {exc}"
        ) from exc

    data = response.json()
    tool_calls = data.get("message", {}).get("tool_calls")
    if not tool_calls:
        raise EvaluationError(f"El modelo local {model!r} no devolvio una llamada a herramienta (tool_calls).")

    arguments = tool_calls[0]["function"]["arguments"]
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    return arguments


def evaluate(
    fields: dict,
    score_result: dict,
    model: str | None = None,
    client: anthropic.Anthropic | None = None,
    provider: str | None = None,
) -> dict:
    """Devuelve {"reasoning": str, "pros": [...], "cons": [...], "risks": [...]}."""
    provider = (provider or config.LLM_PROVIDER or "anthropic").lower()
    user_message = build_user_message(fields, score_result)

    if provider == "anthropic":
        raw = _call_anthropic(user_message, model or config.LLM_MODEL_CHEAP, client)
    elif provider == "ollama":
        raw = _call_ollama(user_message, model or config.LLM_MODEL_OLLAMA)
    else:
        raise EvaluationError(f"LLM_PROVIDER desconocido: {provider!r} (usa 'anthropic' u 'ollama')")

    return {
        "reasoning": raw.get("reasoning") or "",
        "pros": raw.get("pros") or [],
        "cons": raw.get("cons") or [],
        "risks": raw.get("risks") or [],
    }
