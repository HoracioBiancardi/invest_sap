"""Página: consulta ao dicionário de dados SAP (DDIC) — reusa scripts/ddic_lookup.py."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.ddic_lookup import campos_tabela, descricao_tabela  # noqa: E402

st.set_page_config(page_title="DDIC Lookup — Vendas SAP", page_icon="📖", layout="wide")
st.title("📖 DDIC Lookup")
st.caption("O que é uma tabela/campo SAP, direto do dicionário de dados (`DD02T`/`DD03L`/`DD04T` via HANA).")

col1, col2 = st.columns([2, 2])
with col1:
    tabela = st.text_input("Tabela SAP", placeholder="ex.: VBAK, VBAP, VBRK").strip().upper()
with col2:
    campo = st.text_input("Campo (opcional, filtra)", placeholder="ex.: AUART").strip().upper()

if tabela:
    with st.spinner(f"Consultando {tabela}..."):
        desc = descricao_tabela(tabela)
        campos = campos_tabela(tabela)

    if desc.empty:
        st.warning(f"Tabela {tabela!r} não encontrada em DD02T.")
    else:
        st.subheader(f"{tabela}: {desc.iloc[0]['DDTEXT']}")

    if campo:
        campos = campos[campos["FIELDNAME"].str.upper() == campo]

    if campos.empty:
        st.info("Nenhum campo encontrado.")
    else:
        st.dataframe(campos, width="stretch", hide_index=True)
else:
    st.caption("Digite o nome de uma tabela SAP (ex.: VBAK) para começar.")
