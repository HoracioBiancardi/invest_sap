"""Página: Faturamento Diário — mês atual, com quebra por dimensão comercial.

Inspirada na página "Faturamento Diário - Mês Atual" do Painel Vendas (Power BI). Reusa
scripts/query_faturamento_comercial.py — mesma fonte/caveat de
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
    DIMENSOES_FATURAMENTO,
    faturamento_por_dimensao,
    faturamento_serie,
)
from scripts.ui_filtros_comercial import render_filtros_comercial  # noqa: E402

st.set_page_config(
    page_title="Faturamento Diário — Vendas Comercial", page_icon="📅", layout="wide"
)
st.title("📅 Faturamento Diário — Mês Atual")
st.caption(
    "Fonte: `GOLD.vendas_sap.fct_faturamento_itens_sap` — mesma fonte/caveat de "
    "**Faturamento vs Meta**, ver `docs/CONTEXTO_VENDAS_SAP.md` §10. Sempre olha o mês "
    "corrente (não usa o período do filtro global do sidebar, que costuma ser mais amplo)."
)

tipo_cliente_opcao = st.session_state.get("flt_tipo_cliente", "Todos")
tipo_cliente = None if tipo_cliente_opcao == "Todos" else tipo_cliente_opcao

hoje = datetime.date.today()
ontem = hoje - datetime.timedelta(days=1)
inicio_mes = hoje.replace(day=1)

filtros = render_filtros_comercial("p13", list(DIMENSOES_FATURAMENTO))
if filtros:
    st.caption("Filtro ativo: " + ", ".join(f"**{k}** = {v}" for k, v in filtros.items()))


@st.cache_data(ttl=180, show_spinner="Consultando faturamento diário...")
def _dia_cached(
    data_inicio: datetime.date,
    data_fim: datetime.date,
    tipo_cliente: Optional[str],
    filtros: dict[str, str],
) -> pd.DataFrame:
    return faturamento_serie(
        data_inicio, data_fim, granularidade="dia", tipo_cliente=tipo_cliente, filtros=filtros
    )


@st.cache_data(ttl=180, show_spinner="Consultando quebra por dimensão...")
def _dimensao_dia_cached(
    dimensao: str, data_ref: datetime.date, tipo_cliente: Optional[str], filtros: dict[str, str]
) -> pd.DataFrame:
    return faturamento_por_dimensao(
        data_ref,
        data_ref,
        dimensao,
        granularidade="total",
        tipo_cliente=tipo_cliente,
        filtros=filtros,
    )


@st.cache_data(ttl=180, show_spinner="Consultando quebra por dimensão (mês)...")
def _dimensao_mes_cached(
    dimensao: str,
    data_inicio: datetime.date,
    data_fim: datetime.date,
    tipo_cliente: Optional[str],
    filtros: dict[str, str],
) -> pd.DataFrame:
    return faturamento_por_dimensao(
        data_inicio,
        data_fim,
        dimensao,
        granularidade="total",
        tipo_cliente=tipo_cliente,
        filtros=filtros,
    )


df_dia = _dia_cached(inicio_mes, hoje, tipo_cliente, filtros)
faturado_hoje = (
    df_dia.loc[df_dia["Dia"] == hoje, "Valor_Faturado"].sum() if not df_dia.empty else 0.0
)
faturado_mes = df_dia["Valor_Faturado"].sum() if not df_dia.empty else 0.0

c1, c2 = st.columns(2)
c1.metric("Faturamento do Dia", f"R$ {faturado_hoje:,.0f}")
c2.metric("Faturamento do Mês (MTD)", f"R$ {faturado_mes:,.0f}")

st.divider()

st.subheader("Evolução diária")
if df_dia.empty:
    st.info("Sem faturamento no mês corrente ainda.")
else:
    st.bar_chart(df_dia.set_index("Dia")["Valor_Faturado"])

st.divider()

st.subheader("Quebra por dimensão comercial")
col_dim, col_janela = st.columns([1, 1])
with col_dim:
    dimensao = st.selectbox(
        "Quebrar por", options=list(DIMENSOES_FATURAMENTO), index=0, key="dim_diario"
    )
with col_janela:
    janela_opcao = st.radio("Janela", options=["Hoje", "Mês (MTD)"], horizontal=True)

if janela_opcao == "Hoje":
    df_quebra = _dimensao_dia_cached(dimensao, hoje, tipo_cliente, filtros)
    if df_quebra.empty:
        st.info(
            "Sem faturamento hoje ainda para esse recorte — comum se a consulta for feita "
            "de manhã, antes do primeiro lote de faturas do dia ser processado."
        )
else:
    df_quebra = _dimensao_mes_cached(dimensao, inicio_mes, hoje, tipo_cliente, filtros)
    if df_quebra.empty:
        st.info("Nada encontrado para esse recorte no mês.")

if not df_quebra.empty:
    col_g, col_h = st.columns([2, 1])
    with col_g:
        st.bar_chart(df_quebra.set_index("Dimensao")["Valor_Faturado"].head(20))
    with col_h:
        st.dataframe(
            df_quebra[["Dimensao", "Valor_Faturado", "Qtd_Faturada"]].style.format(
                {"Valor_Faturado": "R$ {:,.2f}", "Qtd_Faturada": "{:,.0f}"}
            ),
            width="stretch",
            hide_index=True,
        )

st.divider()

st.subheader("Faturamento por Estado (UF) — mês corrente")
df_uf = _dimensao_mes_cached("Estado (UF)", inicio_mes, hoje, tipo_cliente, filtros)
if df_uf.empty:
    st.info("Sem faturamento no mês corrente ainda.")
else:
    col_i, col_j = st.columns([1, 1])
    with col_i:
        st.bar_chart(df_uf.set_index("Dimensao")["Valor_Faturado"])
    with col_j:
        st.dataframe(
            df_uf[["Dimensao", "Valor_Faturado", "Qtd_Faturada"]]
            .rename(columns={"Dimensao": "Estado"})
            .style.format({"Valor_Faturado": "R$ {:,.2f}", "Qtd_Faturada": "{:,.0f}"}),
            width="stretch",
            hide_index=True,
        )
