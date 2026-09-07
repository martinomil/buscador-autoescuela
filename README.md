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
  gmail_client.py       Autenticación OAuth + envío de emails (Gmail API)
  email_templates.py    Carga/renderizado de plantillas de email
  email_sender.py        Orquesta plantilla + Gmail + registro en BD + anti-duplicados
  llm/                  (Fase 4) extracción y scoring con LLM
streamlit_app/         (Fase 6) interfaz
templates/              Plantillas de email editables (texto plano)
  email_inicial.txt      Plantilla del primer contacto (editable sin tocar código)
tests/                   Tests + emails ficticios de ejemplo
data/                    Base de datos SQLite (no se sube a git)
credentials.json         Credenciales OAuth descargadas de Google Cloud (no se sube a git)
token.json                Token de sesión Gmail generado tras autenticarte (no se sube a git)
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

## Configurar Gmail API (OAuth)

No se guarda nunca tu contraseña de Gmail. La app usa OAuth: la primera vez
abre el navegador para que concedas permiso, y guarda un token local
(`token.json`, fuera de git) para las siguientes veces.

Pasos en [Google Cloud Console](https://console.cloud.google.com/):

1. **Crea un proyecto** (o usa uno existente): menú superior > "Nuevo proyecto".
2. **Habilita la Gmail API**: "APIs & Services" > "Library" > busca "Gmail API" > "Enable".
3. **Configura la pantalla de consentimiento OAuth**: "APIs & Services" >
   "OAuth consent screen".
   - Tipo de usuario: "External" (si no tienes Google Workspace).
   - Rellena nombre de la app y tu email de contacto/soporte.
   - En "Test users", añade tu propia cuenta de Gmail. Mientras la app esté
     en modo "Testing" (lo normal para uso personal), **solo las cuentas que
     añadas aquí podrán autenticarse**.
4. **Crea las credenciales**: "APIs & Services" > "Credentials" >
   "Create Credentials" > "OAuth client ID".
   - Tipo de aplicación: **"Desktop app"**.
   - Ponle un nombre (p. ej. "Buscador autoescuela") y crea.
   - Descarga el JSON generado.
5. **Guarda el fichero** descargado como `credentials.json` en la raíz del
   proyecto (ya está en `.gitignore`, nunca se sube a git).
6. **Autentícate y comprueba la conexión**:

   ```powershell
   python -m app.cli gmail-auth
   ```

   Se abrirá el navegador pidiendo iniciar sesión y conceder permiso. Como la
   app está en modo "Testing" y no verificada por Google, verás un aviso
   "Google no ha verificado esta aplicación" — es normal para una app
   personal tuya: pulsa "Avanzado" > "Ir a (nombre de la app) (no seguro)".
   Al terminar se crea `token.json` y no tendrás que volver a iniciar sesión
   hasta que el token caduque (se renueva solo).

Si más adelante ves errores de permisos al leer correos (Fase 3), borra
`token.json` y vuelve a ejecutar `gmail-auth` para regenerarlo con los scopes
correctos.

## Enviar emails (Fase 2) — flujo recomendado

**No se envía nada real hasta que tú lo pidas explícitamente.** Sigue este
orden la primera vez:

```powershell
# 1) Autenticarte y comprobar que la conexión con Gmail funciona
python -m app.cli gmail-auth

# 2) Ver exactamente qué se enviaría (destinatario, asunto, cuerpo) sin
#    enviar nada ni tocar la base de datos
python -m app.cli send-preview --ids 1

# 3) Enviar UN único email de prueba: usa el contenido real de la
#    autoescuela 1 pero lo redirige a tu propia cuenta (GMAIL_USER_EMAIL o
#    --to), y SÍ registra el EmailMessage asociado a esa autoescuela
python -m app.cli send-test --autoescuela-id 1

# 4) Revisa en tu bandeja de entrada que el email de prueba llegó bien, y
#    comprueba el registro en base de datos
python -m app.cli list

# 5) Cuando confíes en el resultado, envía de verdad a las autoescuelas
#    pendientes (por defecto solo las que tengan status=not_contacted).
#    Sin --confirm solo simula; con --confirm envía de verdad, con pausas
#    entre envíos (EMAIL_SEND_DELAY_SECONDS) y límite por ejecución
#    (EMAIL_MAX_PER_RUN), ambos configurables en .env.
python -m app.cli send-batch                 # simulacion
python -m app.cli send-batch --confirm       # envio real
python -m app.cli send-batch --ids 3,4,7 --confirm   # solo estas autoescuelas
```

Protecciones incluidas:

- **Anti-duplicados**: antes de enviar el email inicial a una autoescuela se
  comprueba si ya se le envió uno; si es así, se bloquea (o se omite en
  `send-batch`) salvo que uses `--force` explícitamente en `send-test`.
- **Rate limiting**: pausa configurable entre envíos y límite máximo de
  envíos por ejecución (`EMAIL_SEND_DELAY_SECONDS`, `EMAIL_MAX_PER_RUN` en `.env`).
- **Plantilla editable**: `templates/email_inicial.txt` se puede editar
  libremente (asunto + cuerpo) sin tocar el código. Admite variables
  `{{name}}`, `{{city}}`, `{{email}}` si quieres personalizar el mensaje.
- **`send-batch` sin `--confirm`** siempre simula primero: nunca se envía
  nada por accidente en el primer uso.

## Credenciales que necesitarás más adelante

- **ANTHROPIC_API_KEY (Fase 4)**: clave de la API de Anthropic (console.anthropic.com).

## Estado del proyecto (fases)

- [x] Fase 1 — Estructura, base de datos, modelo de Autoescuela, importación CSV
- [x] Fase 2 — Integración Gmail (OAuth) + envío de emails
- [ ] Fase 3 — Lectura y almacenamiento de respuestas
- [ ] Fase 4 — Extracción estructurada mediante LLM
- [ ] Fase 5 — Ranking y evaluación
- [ ] Fase 6 — Interfaz (Streamlit)
- [ ] Fase 7 — Follow-ups y mejoras
