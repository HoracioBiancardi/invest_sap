"""Página: Visão do Vendedor — ranking e drill-down individual de faturamento por vendedor.

Reusa scripts/query_vendas_sap.py::faturamento_por_vendedor/faturamento_vendedor_mensal/
top_clientes_por_vendedor. `Codigo_Vendedor` só é confiável quando `Origem_Vendedor =
'SALESFORCE'` — a origem SAP (VBPA parvw='VE') está sempre vazia em produção, confirmado
ao vivo 2026-08-26 (ver `_vendedor_join_sql` em query_vendas_sap.py). Medido ao vivo na
mesma data: ~82% dos itens de fct_faturamento_itens_sap têm vendedor identificado via
Salesforce (o resto cai em "Sem Vendedor Identificado", uma fatia grande e real, não um
bug), e desses 100% batem com `dim_vendedor_sf` (327 vendedores, nome sempre preenchido).
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import (  # noqa: E402
    faturamento_por_vendedor,
    faturamento_vendedor_mensal,
    top_clientes_por_vendedor,
)
from scripts.ui_theme import card, render_filtro_periodo_tipo_cliente  # noqa: E402

st.set_page_config(page_title="Visão do Vendedor — Vendas SAP", page_icon="🧑‍💼", layout="wide")
st.title(":material/badge: Visão do Vendedor")
st.caption(
    "Fonte: `GOLD.vendas_sap.fct_faturamento_itens_sap.Codigo_Vendedor`, nome via "
    "`dim_vendedor_sf`. **Só existe vendedor identificado quando a origem é Salesforce** — "
    "a origem SAP está sempre vazia em produção (join VBPA quebrado). Item sem vendedor "
    "identificado cai em **'Sem Vendedor Identificado'**, do mesmo jeito que outras "
    "páginas usam 'NAO ALOCADO' — não é descartado, é uma fatia real e esperada do "
    "faturamento, então **não confie em rankings/comparações aqui pra decisão de "
    "performance/comissão individual sem checar essa fatia primeiro**."
)

render_filtro_periodo_tipo_cliente()
tipo_cliente_opcao = st.session_state.get("flt_tipo_cliente", "Todos")
tipo_cliente = None if tipo_cliente_opcao == "Todos" else tipo_cliente_opcao
data_inicio = st.session_state.get(
    "flt_data_inicio", datetime.date.today() - datetime.timedelta(days=30)
)
data_fim = st.session_state.get("flt_data_fim", datetime.date.today())
st.caption(
    f"Filtro: período de **{data_inicio:%d/%m/%Y}** a **{data_fim:%d/%m/%Y}**, "
    f"tipo de cliente **{tipo_cliente_opcao}**."
)


@st.cache_data(ttl=300, show_spinner="Consultando faturamento por vendedor...")
def _por_vendedor_cached(
    data_inicio: datetime.date, data_fim: datetime.date, tipo_cliente: str | None
) -> pd.DataFrame:
    return faturamento_por_vendedor(data_inicio, data_fim, tipo_cliente=tipo_cliente)


df = _por_vendedor_cached(data_inicio, data_fim, tipo_cliente)

if df.empty:
    st.info("Nada encontrado para esse período/filtro.")
else:
    valor_total = df["Valor_Faturado"].sum()
    df_sem_vendedor = df[df["Codigo_Vendedor"] == "SEM_VENDEDOR"]
    valor_sem_vendedor = df_sem_vendedor["Valor_Faturado"].sum() if not df_sem_vendedor.empty else 0.0
    pct_sem_vendedor = (valor_sem_vendedor / valor_total) if valor_total else 0.0
    df_identificados = df[df["Codigo_Vendedor"] != "SEM_VENDEDOR"].sort_values(
        "Valor_Faturado", ascending=False
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Faturado no período", f"R$ {valor_total:,.0f}")
    c2.metric("Vendedores identificados", f"{len(df_identificados):,}")
    c3.metric(
        "Top vendedor",
        df_identificados.iloc[0]["Nome_Vendedor"] if not df_identificados.empty else "—",
        help=(
            f"R$ {df_identificados.iloc[0]['Valor_Faturado']:,.0f}"
            if not df_identificados.empty
            else None
        ),
    )
    c4.metric(
        "Sem vendedor identificado",
        f"{pct_sem_vendedor:.0%}",
        help=(
            f"R$ {valor_sem_vendedor:,.0f} de R$ {valor_total:,.0f} sem Codigo_Vendedor "
            "resolvido nesse período — ver caveat no topo da página."
        ),
    )

    if pct_sem_vendedor >= 0.3:
        st.warning(
            f"**{pct_sem_vendedor:.0%} do faturamento do período (R$ {valor_sem_vendedor:,.0f}) "
            "está sem vendedor identificado** — fatia grande demais pra ignorar ao ler os "
            "rankings abaixo, principalmente se a intenção for comparar desempenho entre "
            "pessoas."
        )

    st.divider()

    st.subheader("Ranking de vendedores")
    top_n = st.slider("Quantos vendedores mostrar", min_value=5, max_value=50, value=20, step=5)
    top_vendedores = df_identificados.head(top_n)
    with card("vendedor-ranking"):
        col_a, col_b = st.columns([2, 1])
        with col_a:
            st.bar_chart(top_vendedores.set_index("Nome_Vendedor")["Valor_Faturado"])
        with col_b:
            st.dataframe(
                top_vendedores[["Nome_Vendedor", "Valor_Faturado", "Qtd_Clientes"]].style.format(
                    {"Valor_Faturado": "R$ {:,.0f}", "Qtd_Clientes": "{:,.0f}"}
                ),
                width="stretch",
                hide_index=True,
            )

    st.divider()

    st.subheader("Detalhe de 1 vendedor")
    if df_identificados.empty:
        st.info("Nenhum vendedor identificado nesse período/filtro.")
    else:
        opcoes_vendedor = dict(
            zip(df_identificados["Nome_Vendedor"], df_identificados["Codigo_Vendedor"])
        )
        nome_selecionado = st.selectbox("Vendedor", options=list(opcoes_vendedor))
        codigo_selecionado = opcoes_vendedor[nome_selecionado]

        meses_tendencia = st.slider(
            "Janela da tendência mensal (meses)", min_value=3, max_value=24, value=12, step=1
        )

        @st.cache_data(ttl=300, show_spinner="Consultando tendência mensal...")
        def _tendencia_cached(codigo_vendedor: str, meses: int) -> pd.DataFrame:
            return faturamento_vendedor_mensal(codigo_vendedor, meses=meses)

        @st.cache_data(ttl=300, show_spinner="Consultando top clientes do vendedor...")
        def _top_clientes_vendedor_cached(
            codigo_vendedor: str, data_inicio: datetime.date, data_fim: datetime.date
        ) -> pd.DataFrame:
            return top_clientes_por_vendedor(codigo_vendedor, data_inicio, data_fim, n=15)

        df_tendencia = _tendencia_cached(codigo_selecionado, meses_tendencia)
        df_top_clientes = _top_clientes_vendedor_cached(codigo_selecionado, data_inicio, data_fim)

        if df_tendencia.empty:
            st.info("Sem faturamento nesse vendedor na janela selecionada.")
        else:
            with card("vendedor-tendencia"):
                st.bar_chart(df_tendencia.set_index("Mes")["Valor_Faturado"])

        st.caption(f"Top clientes de **{nome_selecionado}** no período do filtro global.")
        if df_top_clientes.empty:
            st.info("Sem faturamento desse vendedor no período do filtro global.")
        else:
            with card("vendedor-top-clientes"):
                st.dataframe(
                    df_top_clientes.style.format(
                        {"Valor_Faturado": "R$ {:,.2f}", "Qtd_Faturada": "{:,.0f}"}
                    ),
                    width="stretch",
                    hide_index=True,
                )
