"""Página: Visão Produto | Cliente — preço médio, SKUs vendidos, ranking mensal.

Inspirada na página "Visão Produto | Cliente" do Painel Vendas (Power BI). Reusa
scripts/query_faturamento_comercial.py — mesma fonte/caveat de
pages/12_Painel_Vendas.py, ver docs/CONTEXTO_VENDAS_SAP.md §10.
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
    faturamento_por_dimensao,
    faturamento_serie,
    skus_ativos_periodo,
)
from scripts.ui_filtros_comercial import render_filtros_comercial  # noqa: E402
from scripts.ui_theme import card, render_filtro_tipo_cliente  # noqa: E402

st.set_page_config(page_title="Produto | Cliente — Vendas Comercial", page_icon="🧪", layout="wide")
st.title(":material/category: Visão Produto | Cliente")
st.caption(
    "Fonte: `GOLD.vendas_sap.fct_faturamento_itens_sap` — mesma fonte/caveat de "
    "**Painel Vendas**, ver `docs/CONTEXTO_VENDAS_SAP.md` §10. **Família** vem de "
    "`vendas.dim_produto` (mapeamento SharePoint, ~316 materiais, ainda pequeno e "
    "instável — produto sem match cai em 'NAO INFORMADO')."
)

meses = st.slider("Janela (meses)", min_value=3, max_value=24, value=12, step=1)
hoje = datetime.date.today()
data_inicio = (hoje.replace(day=1) - pd.DateOffset(months=meses - 1)).date()

render_filtro_tipo_cliente()
tipo_cliente_opcao = st.session_state.get("flt_tipo_cliente", "Todos")
tipo_cliente = None if tipo_cliente_opcao == "Todos" else tipo_cliente_opcao

filtros = render_filtros_comercial("p15", list(DIMENSOES_FATURAMENTO))
if filtros:
    st.caption("Filtro ativo: " + ", ".join(f"**{k}** = {v}" for k, v in filtros.items()))


@st.cache_data(ttl=900, show_spinner="Consultando faturamento mensal...")
def _serie_mes_cached(
    data_inicio: datetime.date, data_fim: datetime.date, filtros: dict[str, str]
) -> pd.DataFrame:
    return faturamento_serie(data_inicio, data_fim, granularidade="mes", filtros=filtros)


@st.cache_data(ttl=900, show_spinner="Consultando SKUs/clientes ativos por mês...")
def _skus_cached(
    data_inicio: datetime.date, data_fim: datetime.date, filtros: dict[str, str]
) -> pd.DataFrame:
    return skus_ativos_periodo(data_inicio, data_fim, filtros=filtros)


@st.cache_data(ttl=900, show_spinner="Consultando faturamento por dimensão (mês)...")
def _dimensao_mes_cached(
    dimensao: str,
    data_inicio: datetime.date,
    data_fim: datetime.date,
    tipo_cliente,
    filtros: dict[str, str],
) -> pd.DataFrame:
    return faturamento_por_dimensao(
        data_inicio,
        data_fim,
        dimensao,
        granularidade="mes",
        tipo_cliente=tipo_cliente,
        filtros=filtros,
    )


df_mes = _serie_mes_cached(data_inicio, hoje, filtros)
df_skus = _skus_cached(data_inicio, hoje, filtros)

media_mensal = df_mes["Valor_Faturado"].mean() if not df_mes.empty else 0.0
media_skus = df_skus["Qtd_SKUs_Vendidos"].mean() if not df_skus.empty else 0.0
media_clientes = df_skus["Qtd_Clientes_Atendidos"].mean() if not df_skus.empty else 0.0

c1, c2, c3 = st.columns(3)
c1.metric("Média Vendas/mês", f"R$ {media_mensal:,.0f}")
c2.metric("Média SKUs vendidos/mês", f"{media_skus:,.0f}")
c3.metric("Média clientes atendidos/mês", f"{media_clientes:,.0f}")

st.divider()

with card("produto-cliente-evolucao"):
    col_a, col_b = st.columns([2, 1])
    with col_a:
        st.subheader("Faturamento Bruto e Preço Médio por mês")
        if df_mes.empty:
            st.info("Sem dado no período.")
        else:
            df_preco = df_mes.assign(
                Preco_Medio=lambda d: d["Valor_Faturado"] / d["Qtd_Faturada"].replace(0, pd.NA)
            )
            st.bar_chart(df_preco.set_index("Mes")["Valor_Faturado"])
            st.line_chart(df_preco.set_index("Mes")["Preco_Medio"])
    with col_b:
        st.subheader("SKUs vendidos por mês")
        if df_skus.empty:
            st.info("Sem dado no período.")
        else:
            st.bar_chart(df_skus.set_index("Mes")["Qtd_SKUs_Vendidos"])

st.divider()

st.subheader("Ranking mensal")
dimensao_opcao = st.radio("Quebrar por", options=["Cliente", "Família", "Produto"], horizontal=True)
df_dim_mes = _dimensao_mes_cached(dimensao_opcao, data_inicio, hoje, tipo_cliente, filtros)

if df_dim_mes.empty:
    st.info("Nada encontrado para essa combinação de filtro.")
else:
    total_por_dimensao = (
        df_dim_mes.groupby("Dimensao")["Valor_Faturado"].sum().sort_values(ascending=False)
    )
    top_n = st.slider(
        f"Top N {dimensao_opcao.lower()}s (por faturamento total no período)", 5, 50, 15, 5
    )
    top_dimensoes = total_por_dimensao.head(top_n).index

    st.markdown(f"**Faturamento mensal — Top {top_n} {dimensao_opcao.lower()}s**")
    pivot = df_dim_mes[df_dim_mes["Dimensao"].isin(top_dimensoes)].pivot_table(
        index="Dimensao", columns="Mes", values="Valor_Faturado", aggfunc="sum", fill_value=0
    )
    pivot = pivot.reindex(top_dimensoes)
    with card("produto-cliente-ranking-mensal"):
        st.dataframe(pivot.style.format("R$ {:,.0f}"), width="stretch")

    st.markdown("**Média dos últimos 6 meses (dentro da janela selecionada)**")
    ultimos_6_meses = sorted(df_dim_mes["Mes"].unique())[-6:]
    media_6m = (
        df_dim_mes[df_dim_mes["Mes"].isin(ultimos_6_meses)]
        .groupby("Dimensao")
        .agg(
            Qtde_Meses=("Mes", "nunique"),
            Fatur_Bruto_Medio=("Valor_Faturado", lambda s: s.sum() / len(ultimos_6_meses)),
        )
        .sort_values("Fatur_Bruto_Medio", ascending=False)
        .head(top_n)
    )
    with card("produto-cliente-media-6m"):
        st.dataframe(
            media_6m.style.format({"Fatur_Bruto_Medio": "R$ {:,.2f}"}),
            width="stretch",
        )
