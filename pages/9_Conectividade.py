"""Página: checagem de conectividade — reusa scripts/db.py.

Movida da Home pra Técnico (2026-08-25): é uma ferramenta de diagnóstico pontual, não um
dashboard de negócio — não faz sentido junto dos KPIs executivos.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.db import get_hana_connection, get_sqlserver_engine  # noqa: E402

st.set_page_config(page_title="Conectividade — Vendas SAP", page_icon="🔌", layout="wide")
st.title(":material/cable: Conectividade")
st.caption("Testa a conexão com SQL Server (BRONZE/SILVER/GOLD) e SAP HANA/Datasphere usando as credenciais do `.env`.")

if st.button("Verificar conexões", type="primary"):
    cols = st.columns(4)
    checks = [
        ("SQL Server BRONZE", lambda: get_sqlserver_engine("BRONZE").connect().close()),
        ("SQL Server SILVER", lambda: get_sqlserver_engine("SILVER").connect().close()),
        ("SQL Server GOLD", lambda: get_sqlserver_engine("GOLD").connect().close()),
        ("SAP HANA/Datasphere", lambda: get_hana_connection().close()),
    ]
    for col, (nome, fn) in zip(cols, checks):
        with col:
            try:
                fn()
                st.success(nome)
            except Exception as exc:  # noqa: BLE001
                st.error(f"{nome}\n\n{exc}")
else:
    st.caption("Clique para testar SQL Server (BRONZE/SILVER/GOLD) e SAP HANA.")
