import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # permite `from app import ...`

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from app.db import get_session
from app.formatting import format_duration, format_price, format_range
from app.models import AUTOESCUELA_STATUSES
from app.ranking_service import build_ranking_rows

st.set_page_config(page_title="Autoescuelas", page_icon="📋", layout="wide")
st_autorefresh(interval=60_000, key="autoescuelas_autorefresh")
st.title("📋 Autoescuelas")
st.caption("Esta página se actualiza sola cada minuto.")

with get_session() as session:
    rows = build_ranking_rows(session)

col1, col2 = st.columns([1, 2])
with col1:
    status_filter = st.multiselect("Estado", options=list(AUTOESCUELA_STATUSES))
with col2:
    city_filter = st.text_input("Localidad contiene...")

filtered = rows
if status_filter:
    filtered = [r for r in filtered if r["status"] in status_filter]
if city_filter:
    filtered = [r for r in filtered if city_filter.lower() in (r["city"] or "").lower()]

table_data = [
    {
        "ID": r["id"],
        "Autoescuela": r["name"],
        "Localidad": r["city"] or "—",
        "Estado": r["status"],
        "Inicio": format_duration(r["waiting_time_to_start"]),
        "Precio": format_price(r["practice_price"]),
        "Duración": f"{r['practice_duration_minutes']:g} min" if r["practice_duration_minutes"] is not None else "—",
        "Prácticas/sem": format_range(r["practices_per_week_min"], r["practices_per_week_max"]),
        "Examen": format_duration(r["estimated_time_to_exam"]),
        "Score": str(r["score"]) if r["score"] is not None else "—",
    }
    for r in filtered
]

df = pd.DataFrame(table_data)
st.dataframe(df, width="stretch", hide_index=True)
st.caption(f"{len(filtered)} autoescuela(s) de {len(rows)} en total. Haz clic en una cabecera de columna para ordenar.")
