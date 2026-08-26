"""Página: Relatório Analítico — detalhe linha a linha, com seletor de colunas.

Réplica funcional da página "Relatório Analítico" do Painel Vendas (Power BI, com o "Filtro
Colunas Relatório"). Reusa scripts/query_faturamento_comercial.py — mesma fonte/caveat de
pages/12_Faturamento_vs_Meta.py, ver docs/CONTEXTO_VENDAS_SAP.md §10.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_faturamento_comercial import (  # noqa: E402
    COLUNAS_RELATORIO_ANALITICO,
    COLUNAS_RELATORIO_ANALITICO_PADRAO,
    DIMENSOES_FATURAMENTO,
    relatorio_analitico,
)
from scripts.ui_filtros_comercial import render_filtros_comercial  # noqa: E402
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(
    page_title="Relatório Analítico — Vendas Comercial", page_icon="🔬", layout="wide"
)
st.title(":material/query_stats: Relatório Analítico")
st.caption(
    "Fonte: `GOLD.vendas_sap.fct_faturamento_itens_sap` — mesma fonte/caveat de "
    "**Faturamento vs Meta**, ver `docs/CONTEXTO_VENDAS_SAP.md` §10. Detalhe linha a linha "
    "(1 linha = 1 item de fatura) — as outras páginas de Faturamento sempre agregam, esta não."
)
st.caption(
    "Consulta mais pesada que as outras páginas (JOINs linha a linha, não agregado) — a "
    "primeira carga de um período/filtro novo pode levar até ~40s. Ver "
    "`docs/CONTEXTO_VENDAS_SAP.md` §6.10 se um número parecer implausível."
)

tipo_cliente_opcao = st.session_state.get("flt_tipo_cliente", "Todos")
tipo_cliente = None if tipo_cliente_opcao == "Todos" else tipo_cliente_opcao

col_periodo1, col_periodo2 = st.columns(2)
hoje = datetime.date.today()
with col_periodo1:
    data_inicio = st.date_input("De", value=hoje.replace(day=1), max_value=hoje)
with col_periodo2:
    data_fim = st.date_input("Até", value=hoje, max_value=hoje)

filtros = render_filtros_comercial("p17", list(DIMENSOES_FATURAMENTO))
if filtros:
    st.caption("Filtro ativo: " + ", ".join(f"**{k}** = {v}" for k, v in filtros.items()))

colunas = st.multiselect(
    "Colunas do relatório",
    options=list(COLUNAS_RELATORIO_ANALITICO),
    default=COLUNAS_RELATORIO_ANALITICO_PADRAO,
)
limite = st.slider("Máximo de linhas", min_value=100, max_value=10000, value=500, step=100)


@st.cache_data(ttl=900, show_spinner="Consultando relatório analítico...")
def _relatorio_cached(
    data_inicio: datetime.date,
    data_fim: datetime.date,
    colunas: list[str],
    tipo_cliente: Optional[str],
    filtros: dict[str, str],
    limite: int,
) -> pd.DataFrame:
    return relatorio_analitico(
        data_inicio,
        data_fim,
        colunas=colunas or None,
        tipo_cliente=tipo_cliente,
        filtros=filtros,
        limit=limite,
    )


if not colunas:
    st.info("Selecione ao menos 1 coluna.")
elif data_inicio > data_fim:
    st.warning("'De' não pode ser depois de 'Até'.")
else:
    df = _relatorio_cached(data_inicio, data_fim, colunas, tipo_cliente, filtros, limite)

    if df.empty:
        st.info("Nada encontrado para esse período/filtro.")
    else:
        if len(df) == limite:
            st.warning(
                f"Resultado truncado em {limite:,} linhas — pode haver mais no período/filtro "
                "selecionado. Estreite o período, adicione um filtro de recorte ou aumente o "
                "'Máximo de linhas' acima."
            )
        st.caption(f"{len(df):,} linhas")

        formato = {}
        if "Valor Faturado" in df.columns:
            formato["Valor Faturado"] = "R$ {:,.2f}"
        if "Preço Unitário" in df.columns:
            formato["Preço Unitário"] = "R$ {:,.2f}"
        if "Qtd Faturada" in df.columns:
            formato["Qtd Faturada"] = "{:,.0f}"

        with card("relatorio-analitico-detalhe"):
            st.dataframe(df.style.format(formato), width="stretch", hide_index=True)
