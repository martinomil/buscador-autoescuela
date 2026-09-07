"""Autenticacion OAuth y envio/lectura de correo via Gmail API.

No se almacena nunca la contraseña de Gmail. La autenticacion usa el flujo
OAuth "Desktop app": la primera vez se abre el navegador para que el usuario
conceda permiso, y el token resultante se guarda en GMAIL_TOKEN_FILE (fuera
de git) para no tener que volver a iniciar sesion cada vez.
"""
from __future__ import annotations

import base64
import logging
from email import policy
from email.message import EmailMessage as MimeMessage

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app import config

logger = logging.getLogger(__name__)

# gmail.send: enviar correos.
# gmail.readonly: leer respuestas (usado a partir de la Fase 3).
# Pedimos ambos scopes desde ya para no tener que repetir el consentimiento
# OAuth cuando se implemente la lectura de respuestas.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def get_credentials() -> Credentials:
    creds: Credentials | None = None
    token_file = config.GMAIL_TOKEN_FILE

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not config.GMAIL_CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"No se encuentra {config.GMAIL_CREDENTIALS_FILE}. "
                    "Descarga las credenciales OAuth desde Google Cloud Console "
                    "(ver README, seccion 'Configurar Gmail API') y guardalas con ese nombre."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(config.GMAIL_CREDENTIALS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")
        logger.info("Token de Gmail guardado en %s", token_file)

    return creds


def get_gmail_service():
    creds = get_credentials()
    return build("gmail", "v1", credentials=creds)


def get_own_email_address(service) -> str:
    profile = service.users().getProfile(userId="me").execute()
    return profile["emailAddress"]


def build_raw_message(to: str, subject: str, body_text: str) -> dict:
    """Construye el payload 'raw' (base64url) que espera la Gmail API.

    Usa email.message.EmailMessage con policy.default para que asuntos y
    cuerpo con tildes/enes se codifiquen correctamente (RFC 2047 / utf-8).
    """
    msg = MimeMessage(policy=policy.default)
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body_text)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return {"raw": raw}


def send_message(service, to: str, subject: str, body_text: str) -> dict:
    raw_message = build_raw_message(to, subject, body_text)
    sent = service.users().messages().send(userId="me", body=raw_message).execute()
    logger.info(
        "Email enviado a %s (message_id=%s, thread_id=%s)",
        to,
        sent.get("id"),
        sent.get("threadId"),
    )
    return sent
