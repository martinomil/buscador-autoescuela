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
  gmail_reader.py         Detecta, parsea y asocia respuestas nuevas (Fase 3)
  llm/
    extractor.py           Prompt + schema + llamada al LLM (extraccion pura)
    pipeline.py             Orquesta: coge respuestas nuevas, extrae, guarda (Fase 4)
    evaluator.py            Prompt + schema para el razonamiento cualitativo (Fase 5)
  scoring.py              Puntuacion deterministica 0-100, sin LLM (Fase 5)
  ranking_service.py       Orquesta: score + narrativa LLM + tabla de ranking (Fase 5)
  formatting.py            Helpers de formato (desconocido -> "—", duraciones, precios)
  value_parsing.py         Parseo flexible de valores (JSON o texto), compartido CLI/UI
streamlit_app/           Interfaz (Fase 6)
  Home.py                  Dashboard + acciones rapidas
  pages/
    1_Autoescuelas.py        Tabla filtrable/ordenable
    2_Autoescuela_Detalle.py  Ficha completa + edicion manual + historial
    3_Ranking.py              Ranking ordenable
    4_Enviar_Emails.py        Preview / prueba / envio real (mismo flujo que el CLI)
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
#    --to). Se registra (kind="test") para poder auditarlo, pero NO cuenta
#    como contacto real: puedes repetirlo tantas veces como quieras sin que
#    afecte al estado de la autoescuela ni bloquee el envio real posterior.
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

- **Anti-duplicados**: antes de enviar el email inicial (real) a una
  autoescuela se comprueba si ya se le envió uno; si es así, se omite
  automáticamente en `send-batch`. Los envíos de prueba (`send-test`) no
  cuentan como contacto real, así que puedes repetirlos sin restricción.
- **Rate limiting**: pausa configurable entre envíos y límite máximo de
  envíos por ejecución (`EMAIL_SEND_DELAY_SECONDS`, `EMAIL_MAX_PER_RUN` en `.env`).
- **Plantilla editable**: `templates/email_inicial.txt` se puede editar
  libremente (asunto + cuerpo) sin tocar el código. Admite variables
  `{{name}}`, `{{city}}`, `{{email}}` si quieres personalizar el mensaje.
- **`send-batch` sin `--confirm`** siempre simula primero: nunca se envía
  nada por accidente en el primer uso.

## Leer respuestas (Fase 3)

```powershell
# Busca respuestas nuevas en Gmail y las guarda (no se envia nada, solo lectura)
python -m app.cli check-replies

# Ver el detalle y todo el historial de comunicaciones de una autoescuela
python -m app.cli show --autoescuela-id 1

# Respuestas recibidas que NO se pudieron asociar automaticamente a ninguna
# autoescuela (nunca se descartan, se guardan para revision manual)
python -m app.cli list-unmatched

# Asociar manualmente un mensaje sin asociar a la autoescuela correcta
python -m app.cli assign-email --message-id 7 --autoescuela-id 12
```

Cómo funciona `check-replies`:

- Busca en Gmail solo mensajes recibidos **después de la fecha del primer
  contacto** que hayas hecho (no escanea todo tu historial de correo), y
  descarta los que ya tenga guardados (por `gmail_message_id`), así que
  ejecutarlo varias veces es seguro y barato.
- **Asociación por hilo de Gmail** (la señal más fiable): si la respuesta
  llega en el mismo hilo que un email que enviamos, se asocia a esa
  autoescuela automáticamente.
- **Alternativa por remitente**: si el hilo no coincide (p. ej. respondieron
  desde otra dirección), se compara el remitente con el email registrado de
  cada autoescuela.
- **Nunca se pierde una respuesta**: si no se puede asociar de ninguna
  forma, se guarda igualmente (sin autoescuela asignada) y aparece en
  `list-unmatched` para que la asocies tú a mano con `assign-email`.
- Tolera respuestas en HTML: si el correo no trae parte de texto plano, se
  extrae un texto legible a partir del HTML.
- Al asociar una respuesta se actualiza automáticamente el estado de la
  autoescuela a `replied`, la fecha de última respuesta y el contador de
  respuestas (salvo que ya esté en un estado más avanzado como
  `interested`, `rejected` o `selected`, que no se pisan solos).

## Extracción con IA (Fase 4)

Hay dos proveedores intercambiables, elegidos con `LLM_PROVIDER` en `.env`:

### Opción gratis: Ollama (modelo local)

Si ya tienes [Ollama](https://ollama.com/) instalado (o lo instalas), puedes
usar un modelo local sin ningún coste ni API key. Configuración:

```env
LLM_PROVIDER=ollama
LLM_MODEL_OLLAMA=qwen3:8b
```

```powershell
ollama pull qwen3:8b   # ~5 GB de descarga, una sola vez
```

**Por qué `qwen3:8b`** y no otro modelo: se probaron `llama3.1`, `qwen2.5`,
`gemma2` y `qwen3:8b` con la misma bateria de casos (incluyendo respuestas
ambiguas donde NO se debe rellenar un dato). `gemma2` no soporta tool
calling. `llama3.1` y `qwen2.5` extraen bien los datos cuantitativos, pero en
las pruebas `llama3.1` llegó a inventar `accepts_already_passed_theory=false`
sin que el email dijera nada al respecto — justo el tipo de invención que
este proyecto prohibe. `qwen3:8b` fue el único que, en las mismas pruebas,
omitió correctamente ese campo (y otros no mencionados) en todos los casos.
Aun así, **ningún modelo local es tan fiable como Claude siguiendo
instrucciones estrictas de "no inventes"**: la app está diseñada para que
siempre puedas comparar el dato extraído con el texto original (`show`) y
corregirlo (`set-field`) — conviene revisar con algo mas de atencion cuando
uses el proveedor gratuito.

Necesitas una GPU o CPU razonable (qwen3:8b ocupa ~5 GB en disco y RAM/VRAM).
Si `ollama serve` no esta en marcha, `process-replies` fallará con un error
claro indicándolo.

### Opción de pago: Anthropic (Claude)

Más fiable, coste mínimo para este volumen. Necesitas una clave de la API de
Anthropic (**no** es lo mismo que tu suscripción Claude Pro, que es un
producto distinto sin acceso a la API): crea una cuenta en
[console.anthropic.com](https://console.anthropic.com/), genera una API key
y añádela en `.env`:

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

Con el volumen de este proyecto (decenas de emails) el coste esperado es de
céntimos.

```powershell
# Analiza con IA todas las respuestas nuevas (no reanaliza las ya procesadas)
python -m app.cli process-replies

# Limitar cuantas analiza en esta ejecucion, o forzar un modelo concreto
python -m app.cli process-replies --limit 5
python -m app.cli process-replies --model claude-haiku-4-5-20251001

# Reanalizar con el modelo mas potente (LLM_MODEL_SMART) las respuestas que
# el modelo barato marco como ambiguas (follow_up_needed=true)
python -m app.cli escalate-ambiguous

# Corregir a mano un dato mal extraido (queda marcado como source=manual y
# ya no se sobrescribe automaticamente en futuros analisis)
python -m app.cli set-field --autoescuela-id 12 --field practice_price --value 30

# Ver los datos extraidos de una autoescuela junto a su historial de emails
python -m app.cli show --autoescuela-id 12
```

Principios de diseño (ver `app/llm/extractor.py` y `app/llm/pipeline.py`):

- **Nunca se inventa un dato.** El schema que se le pasa al modelo no obliga
  a rellenar ningún campo de datos: si la autoescuela no menciona algo, el
  modelo lo omite y se guarda como `None`. La instrucción "no adivines" está
  reforzada explícitamente en el prompt con ejemplos.
- **Se distingue dato explícito de interpretación de la IA**: el modelo
  siempre debe indicar si la respuesta es ambigua (`follow_up_needed`,
  `missing_info`) o si el lenguaje usado es aproximado
  (`information_is_uncertain`), en vez de mezclarlo silenciosamente con los
  datos concretos.
- **Ahorro de coste**: solo se analizan respuestas nuevas
  (`EmailMessage.processed=False`); un email ya analizado nunca se vuelve a
  mandar al LLM salvo que uses `escalate-ambiguous` explícitamente. Con
  `LLM_PROVIDER=anthropic`, por defecto se usa el modelo barato
  (`LLM_MODEL_CHEAP`, Haiku); el modelo caro (`LLM_MODEL_SMART`, Sonnet) solo
  se usa si decides escalar. `escalate-ambiguous` esta pensado para el
  proveedor Anthropic (pasar de Haiku a Sonnet); con `LLM_PROVIDER=ollama` no
  aporta gran cosa salvo que tengas dos modelos locales de calidad distinta.
- **Validación defensiva por campo** (`_coerce_field` en `extractor.py`):
  cualquier valor que no encaje con el tipo esperado (p.ej. un modelo local
  envolviendo un número en un objeto por error) se descarta con un aviso en
  el log en vez de guardarse tal cual — util especialmente con el proveedor
  gratuito, que seguirá el schema con menos precision que Claude.
- **Las correcciones manuales nunca se pisan**: si corriges un campo con
  `set-field` (o más adelante desde la interfaz), pasa a `source=manual` y
  el pipeline lo respeta en análisis futuros — solo pisa campos en
  `source=ai`.
- **Se guarda todo**: cada llamada al LLM crea un `ExtractionResult` con la
  salida completa (para auditar qué dijo la IA y por qué), además de
  actualizar el valor "vigente" de cada campo en `FieldValue`.
- Si `follow_up_needed=true`, el estado de la autoescuela pasa a
  `follow_up_needed` automáticamente (salvo que ya esté en un estado
  avanzado como `interested`/`rejected`/`selected`).

## Ranking y evaluación (Fase 5)

```powershell
# Evalua una autoescuela concreta (requiere que ya tenga datos extraidos)
python -m app.cli evaluate --autoescuela-id 12

# Evalua todas las que tengan datos, omitiendo las que no han cambiado
# desde la ultima evaluacion (ahorra llamadas al LLM)
python -m app.cli evaluate-all
python -m app.cli evaluate-all --force   # reevalua igualmente

# Tabla comparativa, ordenable
python -m app.cli ranking
python -m app.cli ranking --sort-by precio
python -m app.cli ranking --sort-by inicio --status replied
```

**La puntuación (0-100) es siempre determinista** (`app/scoring.py`, sin
LLM, 100% auditable) — no depende de que el LLM "decida" un número:

| Componente | Puntos máx. | Basado en |
|---|---|---|
| Inicio de prácticas | 25 | `waiting_time_to_start` (menos tiempo = más puntos) |
| Frecuencia de prácticas | 25 | media de `practices_per_week_min/max` (más = más puntos) |
| Tiempo hasta examen | 20 | `estimated_time_to_exam` (menos tiempo = más puntos) |
| Precio | 15 | `practice_price` (menos = más puntos; prioridad baja a propósito) |
| Confianza/disponibilidad | 15 | resta puntos por cada dato desconocido o marcado como incierto |

**Un dato desconocido nunca se trata como "malo"**: su componente recibe una
puntuación neutra (ni alta ni baja) en vez de 0, y el "descuento" por falta
de información se aplica una sola vez, en el componente de
confianza/disponibilidad — así una autoescuela con datos excelentes pero
incompletos no queda penalizada dos veces por lo mismo. `show --autoescuela-id
ID` imprime el desglose completo (formato `+puntos / máximo`), marcando qué
componentes son `unknown`/`partial`.

El **razonamiento cualitativo** (`reasoning`, `pros`, `cons`, `risks`) sí usa
el LLM (`app/llm/evaluator.py`), pero solo para explicar en lenguaje natural
una puntuación ya calculada — nunca para cambiar el número. `evaluate-all`
solo vuelve a llamar al LLM si algún dato de la autoescuela cambió desde la
última evaluación (o si le pasas `--force`).

**Hallazgo real durante las pruebas** (con el proveedor gratuito Ollama):
en una respuesta que decía literalmente "no sabría decirte cuánto
exactamente" sobre el tiempo de espera, el modelo rellenó igualmente
`waiting_time_to_start` con `{value: 0, unit: "months"}` — inventando un
"inicio inmediato" que infló el componente más importante del score a su
máximo. Se reforzó el prompt para prohibir explícitamente el valor `0`
salvo que la autoescuela diga algo como "ahora mismo" o "inmediatamente", y
se verificó que el mismo caso ya se extrae correctamente (campo omitido).
Aun así, es un buen recordatorio de por qué conviene revisar los datos con
`show` antes de tomar una decisión, especialmente con el proveedor gratuito.

## Interfaz (Fase 6)

```powershell
streamlit run streamlit_app/Home.py
```

Se abre en el navegador en `http://localhost:8501`. Páginas disponibles
(barra lateral izquierda):

- **Home** — dashboard con contadores (total, contactadas, pendientes,
  respuestas, pendientes de seguimiento), las 5 mejores opciones actuales, y
  botones de acción rápida: buscar respuestas nuevas en Gmail, analizarlas
  con IA, y recalcular el ranking — sin salir del navegador.
- **Autoescuelas** — tabla completa con filtro por estado/localidad;
  columnas ordenables haciendo clic en la cabecera. Los datos desconocidos
  se muestran como `—`.
- **Autoescuela Detalle** — selector de autoescuela con toda su ficha,
  desglose de puntuación (con barra de progreso por componente), razonamiento
  y pros/contras/riesgos, **edición manual de cualquier dato extraído** (se
  marca como corrección manual y no se sobrescribe en análisis futuros), y el
  historial completo de comunicaciones (siempre con el texto original visible
  para comparar contra lo que entendió la IA).
- **Ranking** — las autoescuelas evaluadas ordenadas según el criterio que
  elijas, con tarjetas resumen; las que aún no tienen score aparecen aparte.
- **Enviar Emails** — mismo flujo de seguridad que el CLI: vista previa sin
  enviar nada → email de prueba a ti mismo → envío real solo tras marcar una
  casilla de confirmación explícita.

La interfaz reutiliza exactamente la misma lógica que el CLI (`app/*.py`,
`app/llm/*.py`) — no hay reglas ni cálculos duplicados en `streamlit_app/`,
solo presentación. Las páginas tienen tests automáticos (`tests/test_streamlit_pages.py`)
que las ejecutan contra una base de datos temporal, tanto vacía como con
datos, usando el framework de pruebas de Streamlit (`AppTest`).

## Estado del proyecto (fases)

- [x] Fase 1 — Estructura, base de datos, modelo de Autoescuela, importación CSV
- [x] Fase 2 — Integración Gmail (OAuth) + envío de emails
- [x] Fase 3 — Lectura y almacenamiento de respuestas
- [x] Fase 4 — Extracción estructurada mediante LLM
- [x] Fase 5 — Ranking y evaluación
- [x] Fase 6 — Interfaz (Streamlit)
- [ ] Fase 7 — Follow-ups y mejoras
