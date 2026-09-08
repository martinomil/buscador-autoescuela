import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # permite `from app import ...`

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from app.db import get_session
from app.gmail_reader import check_new_replies
from app.llm.pipeline import process_unprocessed_replies
from app.ranking_service import evaluate_all, sort_ranking_rows, build_ranking_rows
from app.repository import count_by_status, list_real_reply_messages

st.set_page_config(page_title="Buscador autoescuela", page_icon="🚗", layout="wide")
st_autorefresh(interval=60_000, key="home_autorefresh")
st.title("Buscador de autoescuela — Dashboard")
st.caption("Esta página se actualiza sola cada minuto.")

VERDICT_EMOJI = {
    "highly_recommended": "🟢",
    "recommended": "🟡",
    "possible": "🟠",
    "not_recommended": "🔴",
}

REPLIED_LIKE_STATUSES = {"replied", "follow_up_needed", "interested", "rejected", "selected"}


def _load_dashboard_data():
    with get_session() as session:
        counts = count_by_status(session)
        rows = build_ranking_rows(session)
    return counts, rows


counts, rows = _load_dashboard_data()
total = sum(counts.values())
pendientes = counts.get("not_contacted", 0)
contactadas = total - pendientes
respuestas = sum(counts.get(s, 0) for s in REPLIED_LIKE_STATUSES)
seguimiento = counts.get("follow_up_needed", 0)

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Total autoescuelas", total)
col2.metric("Contactadas", contactadas)
col3.metric("Pendientes de contactar", pendientes)

col4.metric("Respuestas recibidas", respuestas)
if col4.button("Ver respuestas", width="stretch", disabled=respuestas == 0):
    st.session_state["detail_panel"] = None if st.session_state.get("detail_panel") == "respuestas" else "respuestas"

col5.metric("Requieren seguimiento", seguimiento)
if col5.button("Ver seguimiento", width="stretch", disabled=seguimiento == 0):
    st.session_state["detail_panel"] = None if st.session_state.get("detail_panel") == "seguimiento" else "seguimiento"

detail_panel = st.session_state.get("detail_panel")
if detail_panel:
    panel_title = "📬 Respuestas recibidas" if detail_panel == "respuestas" else "🔁 Requieren seguimiento"
    panel_statuses = REPLIED_LIKE_STATUSES if detail_panel == "respuestas" else {"follow_up_needed"}

    st.subheader(panel_title)
    with get_session() as session:
        messages = list_real_reply_messages(session, panel_statuses)
        if not messages:
            st.caption("No hay nada que mostrar.")
        for m in messages:
            st.markdown(f"**{m.autoescuela.name}**")
            st.text(m.body_text or "(sin texto)")
    if st.button("Cerrar", key="close_detail_panel"):
        st.session_state["detail_panel"] = None

st.divider()

left, right = st.columns([2, 1])

with left:
    st.subheader("Mejores opciones actuales")
    scored = sort_ranking_rows([r for r in rows if r["score"] is not None], "score")[:5]
    if not scored:
        st.info(
            "Todavia no hay autoescuelas evaluadas. Usa las acciones de la derecha "
            "para procesar respuestas y calcular el ranking."
        )
    else:
        for r in scored:
            emoji = "❌" if r["status"] in ("rejected", "bounced") else VERDICT_EMOJI.get(r["verdict"], "")
            st.markdown(f"**{emoji} {r['name']}** ({r['city'] or '—'}) — {r['score']}/100")
        st.page_link("pages/3_Ranking.py", label="Ver ranking completo →")

with right:
    st.subheader("Acciones rápidas")

    if st.button("📬 Buscar respuestas nuevas en Gmail", width="stretch"):
        with st.spinner("Consultando Gmail..."):
            try:
                from app.gmail_client import get_gmail_service

                with get_session() as session:
                    service = get_gmail_service()
                    result = check_new_replies(session, service)
                st.success(
                    f"Nuevas asociadas: {len(result['new'])} | Rebotes: {len(result['bounced'])} | "
                    f"Sin asociar: {len(result['unmatched'])}"
                )
            except FileNotFoundError as exc:
                st.error(str(exc))

    if st.button("🤖 Analizar respuestas nuevas con IA", width="stretch"):
        with st.spinner("Analizando con el LLM..."):
            try:
                with get_session() as session:
                    result = process_unprocessed_replies(session)
                st.success(f"Analizadas: {len(result['processed'])} | Errores: {len(result['errors'])}")
            except Exception as exc:  # noqa: BLE001 - mostramos cualquier fallo del LLM tal cual
                st.error(str(exc))

    if st.button("🏆 Recalcular ranking", width="stretch"):
        with st.spinner("Evaluando..."):
            try:
                with get_session() as session:
                    result = evaluate_all(session)
                st.success(
                    f"Evaluadas: {len(result['evaluated'])} | Sin cambios: {len(result['skipped'])} | "
                    f"Sin datos: {len(result['no_data'])}"
                )
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    st.caption("Usa la barra lateral para navegar a la tabla, el detalle, el ranking o el envío de emails.")
