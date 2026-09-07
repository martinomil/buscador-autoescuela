import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # permite `from app import ...`

import streamlit as st

from app import config
from app.db import get_session
from app.email_sender import has_sent_kind, preview_initial_email, send_batch, send_initial_email
from app.repository import get_autoescuela, list_autoescuelas

st.set_page_config(page_title="Enviar emails", page_icon="✉️", layout="wide")
st.title("✉️ Enviar emails")

with get_session() as session:
    pendientes = list_autoescuelas(session, status="not_contacted")

if not pendientes:
    st.info("No hay autoescuelas pendientes de contactar (status=not_contacted).")
    st.stop()

st.write(f"**{len(pendientes)}** autoescuela(s) pendientes de primer contacto.")

id_to_name = {a.id: a.name for a in pendientes}
selected_ids = st.multiselect(
    "Selecciona destinatarias (vacío = todas las pendientes)",
    options=list(id_to_name.keys()),
    format_func=lambda i: id_to_name[i],
)
targets = [a for a in pendientes if not selected_ids or a.id in selected_ids]
st.caption(f"{len(targets)} autoescuela(s) seleccionadas.")

st.divider()
st.subheader("1. Vista previa (no envía nada)")
preview_id = st.selectbox(
    "Ver el email exacto que recibiría...", options=[a.id for a in targets], format_func=lambda i: id_to_name[i]
)
if preview_id:
    with get_session() as session:
        preview_target = get_autoescuela(session, preview_id)
        preview = preview_initial_email(preview_target)
    st.text_input("Para", value=preview["to"], disabled=True)
    st.text_input("Asunto", value=preview["subject"], disabled=True)
    st.text_area("Cuerpo", value=preview["body"], height=220, disabled=True)

st.divider()
st.subheader("2. Enviarte un email de prueba a ti mismo")
st.caption(
    "Usa el contenido real de la autoescuela seleccionada arriba, pero lo redirige a tu "
    "propia cuenta. No cuenta como contacto real: puedes repetirlo sin restricción."
)
test_to = st.text_input("Enviar la prueba a", value=config.GMAIL_USER_EMAIL)
if st.button("Enviar prueba"):
    try:
        from app.gmail_client import get_gmail_service

        with get_session() as session:
            service = get_gmail_service()
            target = get_autoescuela(session, preview_id)
            email_message = send_initial_email(session, service, target, override_to=test_to)
            session.commit()
        st.success(f"Email de prueba enviado a {test_to} (EmailMessage id={email_message.id}).")
    except FileNotFoundError as exc:
        st.error(str(exc))
    except Exception as exc:  # noqa: BLE001
        st.error(str(exc))

st.divider()
st.subheader("3. Envío real")
st.warning(
    f"Esto enviará emails reales por Gmail a las {len(targets)} autoescuela(s) seleccionadas "
    "(o ya contactadas se omiten automáticamente)."
)

with get_session() as session:
    ya_contactadas = [a for a in targets if has_sent_kind(session, a.id, "initial")]
if ya_contactadas:
    st.info(f"{len(ya_contactadas)} de las seleccionadas ya fueron contactadas y se omitirán.")

confirm = st.checkbox("Confirmo que quiero enviar de verdad a las autoescuelas seleccionadas")
if st.button("📤 Enviar ahora", disabled=not confirm, type="primary"):
    try:
        from app.gmail_client import get_gmail_service

        with get_session() as session:
            service = get_gmail_service()
            targets_in_session = [get_autoescuela(session, a.id) for a in targets]
            with st.spinner(f"Enviando (pausa de {config.EMAIL_SEND_DELAY_SECONDS}s entre envíos)..."):
                result = send_batch(session, service, targets_in_session)
        st.success(
            f"Enviados: {len(result['sent'])} | Omitidos (ya contactados): {len(result['skipped'])} | "
            f"Errores: {len(result['errors'])}"
        )
        if result["errors"]:
            st.error("Fallaron: " + ", ".join(a.name for a in result["errors"]))
    except FileNotFoundError as exc:
        st.error(str(exc))
