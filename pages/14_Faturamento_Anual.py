"""Página: Faturamento Anual — comparativo YoY (ano corrente x ano anterior), por dimensão.

Inspirada na página "Faturamento Anual" do Painel Vendas (Power BI) — tabelas "Comparativo
Ano atual vs anterior". Reusa scripts/query_faturamento_comercial.py — mesma fonte/caveat de
pages/12_Faturamento_vs_Meta.py, ver docs/CONTEXTO_VENDAS_SAP.md §10.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_faturamento_comercial import (  # noqa: E402
    DIMENSOES_FATURAMENTO,
    faturamento_anual_comparativo,
    top_clientes_periodo,
)
from scripts.ui_filtros_comercial import render_filtros_comercial  # noqa: E402

st.set_page_config(page_title="Faturamento Anual — Vendas Comercial", page_icon="📆", layout="wide")
st.title("📆 Faturamento Anual — Comparativo YoY")
st.caption(
    "Fonte: `GOLD.vendas_sap.fct_faturamento_itens_sap` — mesma fonte/caveat de "
    "**Faturamento vs Meta**, ver `docs/CONTEXTO_VENDAS_SAP.md` §10. Compara o ano corrente "
    "até hoje (YTD) contra o mesmo período do ano anterior (YTD) e o ano anterior inteiro."
)

filtros = render_filtros_comercial("p14", list(DIMENSOES_FATURAMENTO))
if filtros:
    st.caption("Filtro ativo: " + ", ".join(f"**{k}** = {v}" for k, v in filtros.items()))

dimensao = st.selectbox("Quebrar por", options=list(DIMENSOES_FATURAMENTO), index=0)


@st.cache_data(ttl=1800, show_spinner="Consultando comparativo anual...")
def _comparativo_cached(dimensao: str, filtros: dict[str, str]) -> pd.DataFrame:
    return faturamento_anual_comparativo(dimensao, filtros=filtros)


df = _comparativo_cached(dimensao, filtros)

if df.empty:
    st.info("Nada encontrado para essa dimensão.")
else:
    col_ano_anterior, col_ytd_anterior, col_ytd_atual = df.columns[1], df.columns[2], df.columns[3]

    total_ytd_anterior = df[col_ytd_anterior].sum()
    total_ytd_atual = df[col_ytd_atual].sum()
    variacao_total = (
        ((total_ytd_atual - total_ytd_anterior) / total_ytd_anterior) if total_ytd_anterior else 0.0
    )

    c1, c2, c3 = st.columns(3)
    c1.metric(col_ytd_anterior, f"R$ {total_ytd_anterior:,.0f}")
    c2.metric(col_ytd_atual, f"R$ {total_ytd_atual:,.0f}")
    c3.metric("Evolução YTD", f"{variacao_total:+.1%}")

    st.divider()

    col_a, col_b = st.columns([2, 1])
    with col_a:
        st.subheader(f"{dimensao}: YTD ano anterior x YTD ano corrente")
        st.bar_chart(df.set_index("Dimensao")[[col_ytd_anterior, col_ytd_atual]].head(20))
    with col_b:
        st.subheader("Maiores altas/quedas (YTD)")
        top_evolucao = df.dropna(subset=["Evolucao_YTD_Pct"]).sort_values(
            "Evolucao_YTD_Pct", ascending=False
        )
        st.dataframe(
            top_evolucao[["Dimensao", "Evolucao_YTD_Pct"]].style.format(
                {"Evolucao_YTD_Pct": "{:+.1%}"}
            ),
            width="stretch",
            hide_index=True,
        )

    st.divider()

    st.subheader("Detalhe")
    st.dataframe(
        df.style.format(
            {col: "R$ {:,.2f}" for col in [df.columns[1], col_ytd_anterior, col_ytd_atual]}
            | {"Evolucao_YTD_Pct": "{:+.1%}"}
        ),
        width="stretch",
        hide_index=True,
    )

st.divider()

st.subheader("Top clientes — ano corrente (YTD)")

hoje = datetime.date.today()


@st.cache_data(ttl=1800, show_spinner="Consultando top clientes...")
def _top_clientes_cached(n: int, filtros: dict[str, str]) -> pd.DataFrame:
    return top_clientes_periodo(hoje.replace(month=1, day=1), hoje, n=n, filtros=filtros)


n_clientes = st.slider("Quantos clientes mostrar", min_value=5, max_value=50, value=15, step=5)
df_clientes = _top_clientes_cached(n_clientes, filtros)
if df_clientes.empty:
    st.info("Nada encontrado.")
else:
    st.dataframe(
        df_clientes.style.format(
            {"Valor_Faturado": "R$ {:,.2f}", "Qtd_Faturada": "{:,.0f}", "Preco_Medio": "R$ {:,.2f}"}
        ),
        width="stretch",
        hide_index=True,
    )
