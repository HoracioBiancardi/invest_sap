"""Página: rastrear um pedido pelas 3 camadas — reusa scripts/trace_pedido.py.

Ver docs/CONTEXTO_VENDAS_SAP.md §8 para o funcionamento do elo Opportunity → Pedido, e
docs/INVESTIGACAO_PENDENCIA_SAP.md §5 para um caso real de uso (pedido 137490).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.trace_pedido import trace_pedido  # noqa: E402

st.set_page_config(page_title="Rastrear Pedido — Vendas SAP", page_icon="🧭", layout="wide")
st.title("🧭 Rastrear Pedido")
st.caption("SAP cru (HANA) → Gold `vendas_sap` → Salesforce (Opportunity/OpportunityLineItem), lado a lado.")

col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    numero_pedido = st.text_input("Número do pedido", placeholder="ex.: 137490")
with col2:
    item = st.text_input("Item (opcional)", placeholder="ex.: 10")
with col3:
    st.write("")
    st.write("")
    buscar = st.button("Rastrear", type="primary", disabled=not numero_pedido)

if buscar and numero_pedido:
    with st.spinner(f"Rastreando pedido {numero_pedido}..."):
        resultado = trace_pedido(numero_pedido, item or None)

    for titulo, df in resultado.items():
        with st.expander(f"{titulo} ({len(df)} linha{'s' if len(df) != 1 else ''})", expanded=not df.empty):
            if isinstance(df, pd.DataFrame) and not df.empty:
                st.dataframe(df, width="stretch", hide_index=True)
            else:
                st.info("Nada encontrado.")
elif not numero_pedido:
    st.caption("Digite um número de pedido (com ou sem zeros à esquerda) e clique em \"Rastrear\".")
