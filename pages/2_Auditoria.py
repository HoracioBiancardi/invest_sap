"""Página: auditoria do fluxo — reusa scripts/audit_pendencia_flow.py + 1 checagem comercial.

Ver docs/INVESTIGACAO_PENDENCIA_SAP.md §7 para o histórico da primeira rodada de
audit_pendencia_flow e o que os resultados significaram.

A checagem `linha_negocio_rh_vs_estrutura` (2026-09-05) é diferente das outras 4 — não vem de
`audit_pendencia_flow.py` (que é só sobre o fluxo de pendência), reusa
`scripts/query_vendas_sap.py::auditoria_linha_negocio_rh_vs_estrutura` — ver docstring da
função e `docs/REGRAS_E_MELHORIAS_DW.md` §4.13 pro contexto completo (achado ao investigar
como reduzir a dependência de `vendas.dim_estrutura`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.audit_pendencia_flow import CHECKS as CHECKS_PENDENCIA  # noqa: E402
from scripts.query_vendas_sap import auditoria_linha_negocio_rh_vs_estrutura  # noqa: E402
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(page_title="Auditoria do Fluxo — Vendas SAP", page_icon="🩺", layout="wide")

from scripts.auth import require_login  # noqa: E402

require_login(show_logout=False)  # defesa em profundidade: página aberta direto por URL
st.title(":material/fact_check: Auditoria do Fluxo")
st.caption(
    "Varre o fluxo inteiro (não um pedido específico) procurando padrões de anomalia — "
    "ver `docs/COMO_RODAR.md` §8 para o que cada checagem faz."
)

CHECKS = {**CHECKS_PENDENCIA, "linha_negocio_rh_vs_estrutura": auditoria_linha_negocio_rh_vs_estrutura}

DESCRICOES = {
    "valor_sem_quantidade": "Linha com valor > 0 e quantidade = 0 (o padrão do bug KWMENG/ZMENG).",
    "pendencia_escondida": "'Concluido' com valor > 0 mas zero remessa e zero fatura — sintoma genérico.",
    "reconciliacao_contagem": "Contagem SAP cru (HANA) vs Gold, por tipo de pedido — detecta join quebrado.",
    "integridade_dimensoes": "% de linhas com join de dimensão falho (cliente/centro/produto sem nome).",
    "linha_negocio_rh_vs_estrutura": (
        "Cliente onde a Unidade de Negócio do vendedor (RH) discorda da Linha de Negócio "
        "manual (dim_estrutura) — candidato a rótulo desatualizado, não certeza de erro."
    ),
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
