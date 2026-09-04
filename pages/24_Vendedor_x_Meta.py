"""Página: Vendedor x Meta x Faturamento.

Reusa scripts/query_vendas_sap.py::faturamento_vendedor_com_meta_bu. Página nova — não
existe meta oficial por vendedor na base (ver docstring da função e
docs/CONTEXTO_VENDAS_SAP.md §8.2/§8.3): meta é decisão de planejamento por Setor/BU, sem
coluna de vendedor. Esta página mostra faturamento real por vendedor **ao lado** do
atingimento de meta da BU dele — não inventa uma meta individual.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import faturamento_vendedor_com_meta_bu  # noqa: E402
from scripts.ui_theme import card, render_filtro_periodo_tipo_cliente  # noqa: E402

st.set_page_config(page_title="Vendedor x Meta — Vendas SAP", page_icon="🎯", layout="wide")
st.title(":material/track_changes: Vendedor x Meta x Faturamento")
st.warning(
    "**Não existe meta oficial por vendedor** nesta base — meta é decisão de planejamento "
    "por Setor/BU (`vendas.fat_meta_equipe`, planilha SharePoint), sem coluna de vendedor. "
    "As colunas `Meta_Valor_BU`/`Valor_Realizado_BU`/`Atingimento_BU` abaixo são do **grupo "
    "(BU) inteiro** — repetidas em toda linha de vendedor daquela BU, não uma meta calculada "
    "pra pessoa. `BU` do vendedor vem de `dim_vendedor_sf.Unidade_Negocio`, o metadado mais "
    "raso da dimensão (só ~25% dos vendedores preenchido) — a maioria cai em **'NAO "
    "ALOCADO'**, sem meta pra comparar. Não use esta página pra decisão de comissão "
    "individual."
)

render_filtro_periodo_tipo_cliente()
tipo_cliente_opcao = st.session_state.get("flt_tipo_cliente", "Todos")
tipo_cliente = None if tipo_cliente_opcao == "Todos" else tipo_cliente_opcao
data_inicio = st.session_state.get("flt_data_inicio", datetime.date.today() - datetime.timedelta(days=30))
data_fim = st.session_state.get("flt_data_fim", datetime.date.today())
st.caption(
    f"Filtro: período de **{data_inicio:%d/%m/%Y}** a **{data_fim:%d/%m/%Y}**, "
    f"tipo de cliente **{tipo_cliente_opcao}**."
)


@st.cache_data(ttl=300, show_spinner="Consultando faturamento por vendedor x meta da BU...")
def _dados_cached(data_inicio: datetime.date, data_fim: datetime.date, tipo_cliente: Optional[str]) -> pd.DataFrame:
    return faturamento_vendedor_com_meta_bu(data_inicio=data_inicio, data_fim=data_fim, tipo_cliente=tipo_cliente)


df = _dados_cached(data_inicio, data_fim, tipo_cliente)

if df.empty:
    st.info("Nada encontrado para esse período/filtro.")
else:
    df_identificados = df[df["Codigo_Vendedor"] != "SEM_VENDEDOR"].sort_values("Valor_Faturado", ascending=False)
    df_com_bu = df_identificados[df_identificados["BU"] != "NAO ALOCADO"]
    pct_com_bu = (len(df_com_bu) / len(df_identificados)) if len(df_identificados) else 0.0

    c1, c2, c3 = st.columns(3)
    c1.metric("Vendedores identificados", f"{len(df_identificados):,}")
    c2.metric("Faturado no período", f"R$ {df_identificados['Valor_Faturado'].sum():,.0f}")
    c3.metric("% de vendedores com BU cadastrada", f"{pct_com_bu:.0%}")

    st.divider()

    st.subheader("Por BU: faturamento de vendedor x meta do grupo")
    if df_com_bu.empty:
        st.info("Nenhum vendedor com BU cadastrada nesse filtro — sem comparação possível.")
    else:
        resumo_bu = df_com_bu.groupby("BU").agg(
            Qtd_Vendedores=("Codigo_Vendedor", "nunique"),
            Valor_Faturado_Vendedores=("Valor_Faturado", "sum"),
            Meta_Valor_BU=("Meta_Valor_BU", "first"),
            Valor_Realizado_BU=("Valor_Realizado_BU", "first"),
            Atingimento_BU=("Atingimento_BU", "first"),
        ).sort_values("Valor_Faturado_Vendedores", ascending=False)
        with card("vendedor-meta-bu"):
            col_a, col_b = st.columns([2, 1])
            with col_a:
                st.bar_chart(resumo_bu[["Valor_Faturado_Vendedores", "Meta_Valor_BU"]])
            with col_b:
                st.dataframe(
                    resumo_bu.style.format(
                        {
                            "Valor_Faturado_Vendedores": "R$ {:,.0f}",
                            "Meta_Valor_BU": "R$ {:,.0f}",
                            "Valor_Realizado_BU": "R$ {:,.0f}",
                            "Atingimento_BU": "{:.1%}",
                        }
                    ),
                    width="stretch",
                )

    st.divider()

    st.subheader("Detalhe por vendedor")
    top_n = st.slider("Quantos vendedores mostrar", min_value=5, max_value=50, value=20, step=5)
    with card("vendedor-meta-detalhe"):
        st.dataframe(
            df_identificados.head(top_n)[
                [
                    "Nome_Vendedor", "BU", "Valor_Faturado", "Qtd_Clientes",
                    "Meta_Valor_BU", "Valor_Realizado_BU", "Atingimento_BU",
                ]
            ].style.format(
                {
                    "Valor_Faturado": "R$ {:,.0f}",
                    "Meta_Valor_BU": "R$ {:,.0f}",
                    "Valor_Realizado_BU": "R$ {:,.0f}",
                    "Atingimento_BU": "{:.1%}",
                }
            ),
            width="stretch",
            hide_index=True,
        )
