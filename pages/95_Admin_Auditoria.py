"""Administração → Auditoria: últimos eventos do log (logins, senhas, cofre, configurações).

Só perfil `admin`. Lê a tabela `audit_log` do SQLite local (`scripts/app_db.py`).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Auditoria — Invest SAP", page_icon="🛠️", layout="wide")

from scripts import app_db  # noqa: E402
from scripts.auth import require_admin  # noqa: E402

admin = require_admin()

st.title(":material/history: Audit Log & Histórico")
st.caption("Últimos 200 eventos: logins, trocas de senha, cofre e alterações de configuração.")

st.dataframe(pd.DataFrame(app_db.listar_audit()), use_container_width=True, hide_index=True)
