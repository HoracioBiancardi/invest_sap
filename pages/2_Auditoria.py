"""Página: auditoria do fluxo — reusa scripts/audit_pendencia_flow.py.

Ver docs/INVESTIGACAO_PENDENCIA_SAP.md §7 para o histórico da primeira rodada e o que os
resultados significaram.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.audit_pendencia_flow import CHECKS  # noqa: E402
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(page_title="Auditoria do Fluxo — Vendas SAP", page_icon="🩺", layout="wide")
st.title(":material/fact_check: Auditoria do Fluxo")
st.caption(
    "Varre o fluxo inteiro (não um pedido específico) procurando padrões de anomalia — "
    "ver `docs/COMO_RODAR.md` §8 para o que cada checagem faz."
)

DESCRICOES = {
    "valor_sem_quantidade": "Linha com valor > 0 e quantidade = 0 (o padrão do bug KWMENG/ZMENG).",
    "pendencia_escondida": "'Concluido' com valor > 0 mas zero remessa e zero fatura — sintoma genérico.",
    "reconciliacao_contagem": "Contagem SAP cru (HANA) vs Gold, por tipo de pedido — detecta join quebrado.",
    "integridade_dimensoes": "% de linhas com join de dimensão falho (cliente/centro/produto sem nome).",
}

selecionadas = st.multiselect(
    "Checagens a rodar",
    options=list(CHECKS),
    default=list(CHECKS),
    format_func=lambda nome: f"{nome} — {DESCRICOES.get(nome, '')}",
)

if st.button("Rodar auditoria", type="primary", disabled=not selecionadas):
    for nome in selecionadas:
        st.subheader(nome)
        st.caption(DESCRICOES.get(nome, ""))
        with st.spinner(f"Rodando {nome}..."):
            resultado = CHECKS[nome]()

        secoes = resultado.items() if isinstance(resultado, dict) else [(nome, resultado)]
        for titulo, df in secoes:
            if isinstance(resultado, dict):
                st.markdown(f"**{titulo}**")
            if not isinstance(df, pd.DataFrame) or df.empty:
                st.info("Nenhuma anomalia encontrada.")
            else:
                with card(f"auditoria-{nome}-{titulo}"):
                    st.dataframe(df, width="stretch", hide_index=True)
        st.divider()
else:
    st.caption("Selecione as checagens e clique em \"Rodar auditoria\".")
