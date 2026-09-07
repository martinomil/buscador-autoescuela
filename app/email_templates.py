"""Carga y renderizado de plantillas de email (ficheros de texto plano).

Formato del fichero de plantilla:

    Subject: Asunto del correo
    <linea en blanco>
    Cuerpo del correo, puede usar {{placeholders}} que se sustituyen
    con render_template().

Las plantillas viven en templates/ y se pueden editar sin tocar el codigo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")
SUBJECT_PREFIX = "Subject:"


@dataclass
class EmailTemplate:
    subject: str
    body: str


def load_template(path: str | Path) -> EmailTemplate:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No existe la plantilla: {path}")

    text = path.read_text(encoding="utf-8")
    first_line, sep, rest = text.partition("\n")
    if not first_line.startswith(SUBJECT_PREFIX):
        raise ValueError(
            f"La plantilla {path} debe empezar con una linea '{SUBJECT_PREFIX} ...'"
        )
    subject = first_line[len(SUBJECT_PREFIX):].strip()
    body = rest.lstrip("\n") if sep else ""
    return EmailTemplate(subject=subject, body=body)


def _placeholders_in(text: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(text))


def render_template(template: EmailTemplate, context: dict) -> EmailTemplate:
    needed = _placeholders_in(template.subject) | _placeholders_in(template.body)
    missing = sorted(needed - context.keys())
    if missing:
        raise ValueError(
            f"Faltan valores para las variables de la plantilla: {missing}"
        )

    def _replace(text: str) -> str:
        return PLACEHOLDER_RE.sub(lambda m: str(context.get(m.group(1), "")), text)

    return EmailTemplate(subject=_replace(template.subject), body=_replace(template.body))
