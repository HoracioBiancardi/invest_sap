"""Página: Pendência x Estoque — visão global, todos os materiais/pedidos de uma vez.

"Cópia" da ideia da Visão 360 (Pedido/Pendência cruzado com Estoque), só que sem exigir
filtrar por 1 material/cliente — pra ver o retrato completo da carteira: quanto do
backlog aberto tem estoque de verdade atrás (via `fct_pendencia_status_sap`, a mesma
simulação FIFO da aba "Simulação FIFO" da página Material) e quanto não tem.

Reusa scripts/query_vendas_sap.py::pendencia_x_estoque_global. Pra investigar 1 material
ou 1 cliente específico com todo o contexto (Oportunidade, Remessa, crédito), usar Visão
360/Material/Cliente 360 — esta página é só o panorama, não substitui o drill-down.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import pendencia_x_estoque_global  # noqa: E402
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(page_title="Pendência x Estoque — Vendas SAP", page_icon="🧩", layout="wide")
st.title(":material/fact_check: Pendência x Estoque: visão global")
st.caption(
    "Todo o backlog aberto (`Flag_Pendencia = 1`) cruzado com a simulação FIFO de "
    "estoque (`fct_pendencia_status_sap`) — sem precisar informar material ou cliente. "
    "`Status_Alocacao_Virtual` já vem calculado pelo SAP/dbt simulando fila de "
    "prioridade: 'EM REMESSA' tem estoque de verdade reservado pra essa posição da "
    "fila, 'SEM ESTOQUE' não tem, nem simulando o que ainda vai chegar. Pra abrir 1 "
    "material específico com todo o contexto (crédito, remessa, oportunidade), use "
    "**Visão 360**, **Material** ou **Cliente 360**."
)


@st.cache_data(ttl=300, show_spinner="Consultando pendência x estoque (todos os materiais)...")
def _dados_cached() -> pd.DataFrame:
    return pendencia_x_estoque_global()


df = _dados_cached()

if df.empty:
    st.info("Nenhum backlog aberto encontrado.")
else:
    f1, f2, f3 = st.columns(3)
    with f1:
        filtro_status = st.multiselect(
            "Status_Alocacao_Virtual",
            options=sorted(df["Status_Alocacao_Virtual"].dropna().unique()),
            default=[],
            key="pxe_status",
            help="Vazio = todos.",
        )
    with f2:
        filtro_centro = st.text_input("Código do centro (opcional)", key="pxe_centro").strip()
    with f3:
        filtro_material = st.text_input("Código do material (opcional)", key="pxe_material").strip().upper()

    df_filtrado = df
    if filtro_status:
        df_filtrado = df_filtrado[df_filtrado["Status_Alocacao_Virtual"].isin(filtro_status)]
    if filtro_centro:
        df_filtrado = df_filtrado[df_filtrado["Codigo_Centro"] == filtro_centro]
    if filtro_material:
        df_filtrado = df_filtrado[df_filtrado["Codigo_Produto"].str.upper() == filtro_material]

    df_sem_estoque = df_filtrado[df_filtrado["Status_Alocacao_Virtual"] == "SEM ESTOQUE"]
    valor_total = df_filtrado["Valor_Pendente_Faturamento"].sum()
    valor_sem_estoque = df_sem_estoque["Valor_Pendente_Faturamento"].sum()

    with card("pxe-kpi"):
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Itens de pedido (filtro atual)", f"{len(df_filtrado):,}")
        k2.metric("Qtd pendente total", f"{df_filtrado['Qtd_Pendente_Operacional'].sum():,.0f}")
        k3.metric("Valor pendente total", f"R$ {valor_total:,.0f}")
        k4.metric(
            "Sem cobertura de estoque (SEM ESTOQUE)",
            f"R$ {valor_sem_estoque:,.0f}",
            f"{(valor_sem_estoque / valor_total * 100 if valor_total else 0):.0f}% do valor filtrado",
        )

    st.markdown("**Valor pendente por Status_Alocacao_Virtual**")
    with card("pxe-status-chart"):
        col_a, col_b = st.columns([1, 1])
        df_status = df_filtrado.groupby("Status_Alocacao_Virtual")["Valor_Pendente_Faturamento"].sum().sort_values(ascending=False)
        with col_a:
            st.dataframe(df_status.reset_index(), width="stretch", hide_index=True)
        with col_b:
            st.bar_chart(df_status)

    st.divider()

    st.markdown("**Ranking de materiais sem cobertura de estoque (priorizar compra/produção)**")
    st.caption(
        "Ordenado por `Valor_Sem_Estoque` — maior valor pendente sem estoque na simulação "
        "FIFO primeiro. `Qtd_Em_Remessa` é o que já tem estoque reservado na fila."
    )
    df_ranking = (
        df_filtrado.groupby(["Codigo_Produto", "Descricao_Produto", "Codigo_Centro", "Nome_Centro"])
        .apply(
            lambda g: pd.Series(
                {
                    "Itens_Total": len(g),
                    "Qtd_Sem_Estoque": g.loc[g["Status_Alocacao_Virtual"] == "SEM ESTOQUE", "Qtd_Pendente_Operacional"].sum(),
                    "Valor_Sem_Estoque": g.loc[g["Status_Alocacao_Virtual"] == "SEM ESTOQUE", "Valor_Pendente_Faturamento"].sum(),
                    "Qtd_Em_Remessa": g.loc[g["Status_Alocacao_Virtual"] == "EM REMESSA", "Qtd_Pendente_Operacional"].sum(),
                }
            ),
            include_groups=False,
        )
        .reset_index()
    )
    df_ranking = df_ranking[df_ranking["Valor_Sem_Estoque"] > 0].sort_values("Valor_Sem_Estoque", ascending=False)

    n_ranking = st.slider("Quantos materiais mostrar", min_value=5, max_value=200, value=30, step=5, key="pxe_top_n")
    with card("pxe-ranking"):
        st.dataframe(
            df_ranking.head(n_ranking).style.format(
                {
                    "Qtd_Sem_Estoque": "{:,.0f}",
                    "Valor_Sem_Estoque": "R$ {:,.2f}",
                    "Qtd_Em_Remessa": "{:,.0f}",
                }
            ),
            width="stretch",
            hide_index=True,
        )

    st.divider()

    st.markdown("**Detalhe item a item**")
    n_detalhe = st.slider("Quantas linhas mostrar", min_value=50, max_value=5000, value=500, step=50, key="pxe_detalhe_n")
    colunas_detalhe = [
        "Numero_Pedido", "Item_Pedido", "Data_Inclusao_Pedido", "Dias_Desde_Inclusao_Pedido",
        "Codigo_Produto", "Descricao_Produto", "Codigo_Centro", "Nome_Centro",
        "Nome_Cliente", "Qtd_Pendente_Operacional", "Valor_Pendente_Faturamento",
        "Status_Pendencia", "Status_Alocacao_Virtual",
    ]
    with card("pxe-detalhe"):
        st.dataframe(
            df_filtrado.sort_values("Valor_Pendente_Faturamento", ascending=False)[colunas_detalhe].head(n_detalhe),
            width="stretch",
            hide_index=True,
        )
