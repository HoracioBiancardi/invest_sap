"""Página: Relatório de Pedidos — quantidade, valor médio e ranking por cliente.

Inspirada na página "Relatório de Pedidos" do Painel Vendas (Power BI). Complementa (não
duplica) pages/10_Analise_Historica.py: aquela mostra a tendência Pedido x Faturado; esta
foca em quantidade/valor médio de pedido e no ranking por cliente. Reusa
scripts/query_vendas_sap.py (`fct_vendas_itens_sap` — pedido/ordem de venda, conceito
diferente de faturamento comercial das páginas 12-15/17, mesmo hoje todas lendo de
`vendas_sap`; ver docs/CONTEXTO_VENDAS_SAP.md §10).
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import pedidos_mensal, pedidos_por_cliente  # noqa: E402
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(page_title="Relatório de Pedidos — Vendas SAP", page_icon="🧾", layout="wide")
st.title(":material/receipt_long: Relatório de Pedidos")
st.caption(
    "Fonte: `GOLD.vendas_sap.fct_vendas_itens_sap` (Data_Inclusao_Pedido — pedido entrando "
    "no funil, não é o mesmo que faturamento). Ver **Análise Histórica** para a tendência "
    "Pedido x Faturado lado a lado."
)

tipo_cliente_opcao = st.session_state.get("flt_tipo_cliente", "Todos")
tipo_cliente = None if tipo_cliente_opcao == "Todos" else tipo_cliente_opcao

meses = st.slider("Janela (meses)", min_value=6, max_value=36, value=12, step=1)
hoje = datetime.date.today()
data_inicio = (hoje.replace(day=1) - pd.DateOffset(months=meses - 1)).date()


@st.cache_data(ttl=900, show_spinner="Consultando pedidos mensais...")
def _pedidos_mensal_cached(meses: int) -> pd.DataFrame:
    return pedidos_mensal(meses=meses)


@st.cache_data(ttl=900, show_spinner="Consultando ranking de pedidos por cliente...")
def _pedidos_cliente_cached(
    data_inicio: datetime.date, data_fim: datetime.date, n: int, tipo_cliente: Optional[str]
) -> pd.DataFrame:
    return pedidos_por_cliente(data_inicio, data_fim, n=n, tipo_cliente=tipo_cliente)


df_mensal = _pedidos_mensal_cached(int(meses))

if df_mensal.empty:
    st.info("Sem pedidos no período.")
else:
    df_mensal = df_mensal.assign(
        Valor_Medio_Pedido=lambda d: d["Valor_Pedido"] / d["Qtd_Pedidos"].replace(0, pd.NA)
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Pedidos no período", f"{df_mensal['Qtd_Pedidos'].sum():,.0f}")
    c2.metric("Valor total pedido", f"R$ {df_mensal['Valor_Pedido'].sum():,.0f}")
    c3.metric(
        "Valor médio de pedido (média mensal)", f"R$ {df_mensal['Valor_Medio_Pedido'].mean():,.0f}"
    )

    st.divider()

    with card("pedidos-mensal"):
        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("Quantidade mensal de pedidos")
            st.bar_chart(df_mensal.set_index("Mes")["Qtd_Pedidos"])
        with col_b:
            st.subheader("Valor médio de pedido — mensal")
            st.bar_chart(df_mensal.set_index("Mes")["Valor_Medio_Pedido"])

st.divider()

st.subheader("Ranking de pedidos por cliente")
n_clientes = st.slider("Quantos clientes mostrar", min_value=5, max_value=50, value=20, step=5)
df_clientes = _pedidos_cliente_cached(data_inicio, hoje, n_clientes, tipo_cliente)

if df_clientes.empty:
    st.info("Nada encontrado para esse período/filtro.")
else:
    with card("pedidos-ranking-clientes"):
        st.dataframe(
            df_clientes.style.format(
                {
                    "Qtd_Pedidos": "{:,.0f}",
                    "Qtd_Itens_Total": "{:,.0f}",
                    "Media_Itens_Pedido": "{:,.2f}",
                    "Valor_Medio_Pedido": "R$ {:,.2f}",
                }
            ),
            width="stretch",
            hide_index=True,
        )
