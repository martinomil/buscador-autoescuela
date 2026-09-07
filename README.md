# Buscador de autoescuela (permiso B)

Herramienta personal para gestionar la búsqueda de autoescuela: contactar por
email, leer respuestas, extraer datos estructurados con IA, y generar un
ranking según tus prioridades (velocidad hasta el examen > precio).

## Arquitectura

- **Python** para toda la lógica (backend, CLI, integración Gmail/LLM).
- **SQLite + SQLAlchemy** como base de datos. Los campos extraídos por IA se
  guardan como pares `(field_name, value)` en la tabla `field_values` en vez
  de una columna por campo, para poder añadir nuevos campos de extracción sin
  migraciones de esquema.
- **Gmail API (OAuth)** para enviar y leer correos — sin guardar contraseñas.
- **Anthropic Claude** como LLM: modelo barato (`claude-haiku-*`) para
  extracción rutinaria, modelo más potente (`claude-sonnet-*`) reservado para
  casos ambiguos y para el razonamiento cualitativo del ranking.
- **Streamlit** como interfaz (dashboard, tabla, ficha de autoescuela,
  ranking) — mínimo código para una herramienta de uso personal.

### Estructura de carpetas

```
app/                  Lógica de negocio (sin UI)
  config.py           Carga de variables de entorno (.env)
  db.py                Engine/sesión SQLAlchemy
  models.py            Modelos: Autoescuela, EmailMessage, ExtractionResult,
                        FieldValue, Evaluation
  importer.py          Importación de autoescuelas desde CSV
  repository.py        Consultas comunes (listar, contar por estado...)
  cli.py                CLI de administración (python -m app.cli ...)
  logging_setup.py      Configuración de logging
  llm/                  (Fase 4) extracción y scoring con LLM
  gmail_client.py       (Fase 2) autenticación y envío/lectura Gmail
streamlit_app/         (Fase 6) interfaz
templates/              Plantillas de email editables (texto plano)
tests/                   Tests + emails ficticios de ejemplo
data/                    Base de datos SQLite (no se sube a git)
```

## Puesta en marcha

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
# Edita .env: al menos ANTHROPIC_API_KEY cuando llegues a la Fase 4.
```

### Comandos disponibles (Fase 1)

```powershell
# Crear las tablas de la base de datos
python -m app.cli init-db

# Importar autoescuelas desde un CSV (columnas: name, email obligatorias;
# city, phone, website, address, notes opcionales)
python -m app.cli import-csv ruta\al\fichero.csv

# Listar autoescuelas registradas
python -m app.cli list
python -m app.cli list --status not_contacted
```

### Tests

```powershell
python -m pytest -q
```

## Credenciales que necesitarás más adelante

- **Google Cloud OAuth (Fase 2)**: crear un proyecto en Google Cloud Console,
  habilitar la Gmail API, crear una credencial "OAuth client ID" de tipo
  "Desktop app", descargar el JSON y guardarlo como `credentials.json` en la
  raíz del proyecto (ya está en `.gitignore`). Instrucciones detalladas se
  añadirán en la Fase 2.
- **ANTHROPIC_API_KEY (Fase 4)**: clave de la API de Anthropic (console.anthropic.com).

## Estado del proyecto (fases)

- [x] Fase 1 — Estructura, base de datos, modelo de Autoescuela, importación CSV
- [ ] Fase 2 — Integración Gmail (OAuth) + envío de emails
- [ ] Fase 3 — Lectura y almacenamiento de respuestas
- [ ] Fase 4 — Extracción estructurada mediante LLM
- [ ] Fase 5 — Ranking y evaluación
- [ ] Fase 6 — Interfaz (Streamlit)
- [ ] Fase 7 — Follow-ups y mejoras
