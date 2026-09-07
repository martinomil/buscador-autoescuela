import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # permite `from app import ...`

import json

import streamlit as st

from app.db import get_session
from app.ranking_service import get_latest_evaluation
from app.repository import (
    get_autoescuela,
    list_autoescuelas,
    list_emails_for_autoescuela,
    list_field_values,
    set_field_value_manual,
)
from app.value_parsing import parse_flexible_value

st.set_page_config(page_title="Detalle autoescuela", page_icon="🔍", layout="wide")
st.title("🔍 Detalle de autoescuela")

with get_session() as session:
    autoescuelas = list_autoescuelas(session)

if not autoescuelas:
    st.info("No hay autoescuelas registradas todavia.")
    st.stop()

options = {f"[{a.id}] {a.name}": a.id for a in autoescuelas}
selected_label = st.selectbox("Selecciona una autoescuela", list(options.keys()))
autoescuela_id = options[selected_label]

with get_session() as session:
    autoescuela = get_autoescuela(session, autoescuela_id)

    col_info, col_score = st.columns(2)

    with col_info:
        st.subheader(autoescuela.name)
        st.write(f"**Estado:** `{autoescuela.status}`")
        st.write(f"**Ciudad:** {autoescuela.city or '—'}")
        st.write(f"**Email:** {autoescuela.email}")
        st.write(f"**Teléfono:** {autoescuela.phone or '—'}")
        if autoescuela.website:
            st.write(f"**Web:** {autoescuela.website}")
        if autoescuela.notes:
            st.write(f"**Notas:** {autoescuela.notes}")
        st.caption(
            f"Emails enviados: {autoescuela.emails_sent_count} | Respuestas: {autoescuela.replies_count} | "
            f"Primer contacto: {autoescuela.first_contact_date or '—'} | "
            f"Última respuesta: {autoescuela.last_reply_date or '—'}"
        )

    evaluation = get_latest_evaluation(session, autoescuela_id)
    with col_score:
        if evaluation is None:
            st.info("Todavía no evaluada. Usa 'Recalcular ranking' en el Dashboard.")
        else:
            st.metric("Puntuación", f"{evaluation.score}/100", evaluation.verdict)
            for comp in evaluation.breakdown.values():
                ratio = comp["points"] / comp["max"] if comp["max"] else 0
                label = f"{comp['label']}: {comp['points']}/{comp['max']}"
                if comp["status"] != "known":
                    label += f" ({comp['status']})"
                st.progress(min(1.0, max(0.0, ratio)), text=label)
            if evaluation.reasoning:
                st.write("**Razonamiento:**", evaluation.reasoning)
            if evaluation.pros:
                st.write("**Pros:** " + "; ".join(evaluation.pros))
            if evaluation.cons:
                st.write("**Contras:** " + "; ".join(evaluation.cons))
            if evaluation.risks:
                st.write("**Riesgos:** " + "; ".join(evaluation.risks))

    st.divider()
    st.subheader("Datos extraídos")
    st.caption(
        "🤖 = extraído por IA · ✋ = corregido a mano. Corrige cualquier dato que la IA "
        "haya interpretado mal — se guardará como manual y ya no se sobrescribirá."
    )

    field_values = list_field_values(session, autoescuela_id)
    if not field_values:
        st.caption("Todavía no hay datos extraídos para esta autoescuela.")
    else:
        for fv in field_values:
            c_name, c_value, c_source, c_save = st.columns([2, 3, 1, 1])
            c_name.markdown(f"**{fv.field_name}**")
            current_str = fv.value if isinstance(fv.value, str) else json.dumps(fv.value, ensure_ascii=False)
            new_value_str = c_value.text_input(
                "valor", value=current_str, key=f"field_{fv.id}", label_visibility="collapsed"
            )
            c_source.markdown("✋ manual" if fv.source == "manual" else "🤖 ia")
            if c_save.button("Guardar", key=f"save_{fv.id}"):
                parsed = parse_flexible_value(new_value_str)
                set_field_value_manual(session, autoescuela_id, fv.field_name, parsed, updated_by="streamlit")
                session.commit()
                st.success(f"Campo '{fv.field_name}' actualizado.")
                st.rerun()

    st.divider()
    st.subheader("Historial de comunicaciones")
    st.caption("Compara siempre el texto original con lo que entendió la IA arriba.")

    emails = list_emails_for_autoescuela(session, autoescuela_id)
    if not emails:
        st.caption("Sin comunicaciones registradas todavía.")
    for m in emails:
        direction_label = "📤 Enviado" if m.direction == "outbound" else "📥 Recibido"
        kind_label = f" ({m.kind})" if m.kind else ""
        with st.expander(f"{direction_label}{kind_label} — {m.timestamp or m.created_at} — {m.subject or '(sin asunto)'}"):
            st.write(f"De: {m.sender or '—'} | Para: {m.recipient or '—'}")
            st.text(m.body_text or "(sin texto)")
