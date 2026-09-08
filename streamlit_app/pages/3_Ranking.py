import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # permite `from app import ...`

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from app.db import get_session
from app.formatting import format_duration, format_price, format_range
from app.ranking_service import SORT_KEYS, build_ranking_rows, sort_ranking_rows

st.set_page_config(page_title="Ranking", page_icon="🏆", layout="wide")
st_autorefresh(interval=60_000, key="ranking_autorefresh")
st.title("🏆 Ranking de autoescuelas")
st.caption("Ordenado según tus prioridades: inicio de prácticas, frecuencia, tiempo hasta examen y precio. Esta página se actualiza sola cada minuto.")

VERDICT_EMOJI = {
    "highly_recommended": "🟢",
    "recommended": "🟡",
    "possible": "🟠",
    "not_recommended": "🔴",
}
VERDICT_LABELS = {
    "highly_recommended": "Muy recomendada",
    "recommended": "Recomendada",
    "possible": "Posible",
    "not_recommended": "No recomendada",
}

sort_by = st.selectbox("Ordenar por", SORT_KEYS, index=0)

with get_session() as session:
    rows = build_ranking_rows(session)
rows = sort_ranking_rows(rows, sort_by)

scored = [r for r in rows if r["score"] is not None]
unscored = [r for r in rows if r["score"] is None]

if not scored:
    st.info("Todavía no hay autoescuelas evaluadas. Ve al Dashboard y pulsa 'Recalcular ranking'.")
else:
    for r in scored:
        if r["status"] in ("rejected", "bounced"):
            emoji = "❌"
            verdict_label = "Rebotó (no entregado)" if r["status"] == "bounced" else "Descartada"
        else:
            emoji = VERDICT_EMOJI.get(r["verdict"], "")
            verdict_label = VERDICT_LABELS.get(r["verdict"], r["verdict"])
        st.markdown(f"### {emoji} {r['name']} — {r['score']}/100 ({verdict_label})")
        cols = st.columns(5)
        cols[0].metric("Inicio", format_duration(r["waiting_time_to_start"]))
        cols[1].metric("Prácticas/sem", format_range(r["practices_per_week_min"], r["practices_per_week_max"]))
        cols[2].metric("Examen", format_duration(r["estimated_time_to_exam"]))
        cols[3].metric("Precio", format_price(r["practice_price"]))
        cols[4].metric("Localidad", r["city"] or "—")
        st.divider()

if unscored:
    st.subheader(f"Sin evaluar todavía ({len(unscored)})")
    df = pd.DataFrame([{"ID": r["id"], "Nombre": r["name"], "Estado": r["status"]} for r in unscored])
    st.dataframe(df, width="stretch", hide_index=True)
