"""Página: Faturamento — visão executiva resumida (total bruto de `vendas_sap`).

Funde 2 páginas antigas: Faturamento por Org Vendas x Linha de Negócio
(scripts/query_vendas_sap.py::faturamento_por_org_vendas_linha_negocio) e a parte de
faturamento/devoluções de Análise Histórica (faturamento_mensal/devolucoes_mensal) — a
parte de "pedidos entrando no funil" de Análise Histórica virou parte da página **Pedidos**.

Diferente de **Faturamento (Painel Vendas)** (scripts/query_faturamento_comercial.py, que
passa pelo crosswalk cliente→setor comercial, ~52% cobertura) — aqui é o total bruto de
`fct_faturamento_itens_sap`, sem esse crosswalk. Ver docs/CONTEXTO_VENDAS_SAP.md §10.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import (  # noqa: E402
    devolucoes_mensal,
    faturamento_mensal,
    faturamento_por_org_vendas_linha_negocio,
)
from scripts.ui_theme import card, render_filtro_periodo_tipo_cliente  # noqa: E402

st.set_page_config(page_title="Faturamento — Vendas SAP", page_icon="💰", layout="wide")
st.title(":material/payments: Faturamento")

render_filtro_periodo_tipo_cliente()
tipo_cliente_opcao = st.session_state.get("flt_tipo_cliente", "Todos")
tipo_cliente = None if tipo_cliente_opcao == "Todos" else tipo_cliente_opcao
data_inicio = st.session_state.get("flt_data_inicio", datetime.date.today() - datetime.timedelta(days=30))
data_fim = st.session_state.get("flt_data_fim", datetime.date.today())
st.caption(
    f"Filtro: período de **{data_inicio:%d/%m/%Y}** a **{data_fim:%d/%m/%Y}**, "
    f"tipo de cliente **{tipo_cliente_opcao}**. A série mensal abaixo ignora esse período "
    "de propósito (mostra tendência de mais longo prazo)."
)

tab_resumo, tab_tendencia = st.tabs(["Org Vendas x Linha de Negócio", "Tendência mensal"])


@st.cache_data(ttl=300, show_spinner="Consultando faturamento por Org Vendas x Linha de Negócio...")
def _faturamento_org_cached(data_inicio: datetime.date, data_fim: datetime.date, tipo_cliente: Optional[str]) -> pd.DataFrame:
    return faturamento_por_org_vendas_linha_negocio(data_inicio=data_inicio, data_fim=data_fim, tipo_cliente=tipo_cliente)


with tab_resumo:
    st.caption(
        "Organização de Vendas é o campo SAP (VKORG). Linha de Negócio vem do cruzamento "
        "Cliente → `dim_cliente_setor`/`dim_estrutura` (~52%) com fallback por heurística de "
        "produto dominante (eleva pra ~87%) — coluna `Origem_Linha_Negocio` no detalhe indica "
        "qual camada resolveu cada linha. São 2 dimensões independentes, não uma hierarquia."
    )
    df_org = _faturamento_org_cached(data_inicio, data_fim, tipo_cliente)
    if df_org.empty:
        st.info("Nada encontrado para esse período.")
    else:
        c1, c2 = st.columns(2)
        c1.metric("Valor Faturado Total", f"R$ {df_org['Valor_Faturado'].sum():,.2f}")
        c2.metric("Qtd Faturada Total", f"{df_org['Qtd_Faturada'].sum():,.0f}")

        st.divider()
        with card("faturamento-org-dimensao"):
            col_a, col_b = st.columns([1, 1])
            with col_a:
                st.subheader("Por Organização de Vendas")
                st.bar_chart(df_org.groupby("Descricao_Org_Vendas")["Valor_Faturado"].sum())
            with col_b:
                st.subheader("Por Linha de Negócio")
                st.bar_chart(df_org.groupby("Linha_Negocio")["Valor_Faturado"].sum())

        st.divider()
        st.subheader("Matriz Org Vendas x Linha de Negócio (R$)")
        matriz = df_org.pivot_table(
            index="Descricao_Org_Vendas", columns="Linha_Negocio", values="Valor_Faturado", aggfunc="sum", fill_value=0
        )
        with card("faturamento-org-matriz"):
            st.dataframe(matriz.style.format("R$ {:,.2f}"), width="stretch")

        st.divider()
        st.subheader("Detalhe")
        with card("faturamento-org-detalhe"):
            st.dataframe(
                df_org[
                    [
                        "Codigo_Org_Vendas", "Descricao_Org_Vendas", "Linha_Negocio",
                        "Origem_Linha_Negocio", "Valor_Faturado", "Qtd_Faturada", "Qtd_Itens",
                    ]
                ],
                width="stretch",
                hide_index=True,
            )

with tab_tendencia:
    st.caption(
        "Faturamento e devoluções/abatimentos mês a mês — as únicas 2 séries com data real "
        "de transação acumulada nesta base (backlog/estoque só guardam o estado de hoje)."
    )
    meses = st.slider("Período (meses)", min_value=6, max_value=60, value=24, step=6, key="faturamento_meses")

    @st.cache_data(ttl=1800, show_spinner="Consultando histórico...")
    def _historico_cached(meses: int) -> dict[str, pd.DataFrame]:
        return {"faturamento": faturamento_mensal(meses=meses), "devolucoes": devolucoes_mensal(meses=meses)}

    dados = _historico_cached(int(meses))
    df_fat = dados["faturamento"]
    df_dev = dados["devolucoes"]

    def _resumo_ano_atual_vs_anterior(df: pd.DataFrame, col_valor: str) -> tuple[float, float]:
        if df.empty or len(df) < 2:
            return 0.0, 0.0
        ultimos_12 = df.tail(12)[col_valor].sum()
        anteriores_12 = df.iloc[max(0, len(df) - 24) : max(0, len(df) - 12)][col_valor].sum()
        return ultimos_12, anteriores_12

    st.subheader("Faturamento")
    if df_fat.empty:
        st.info("Sem dado de faturamento no período.")
    else:
        atual, anterior = _resumo_ano_atual_vs_anterior(df_fat, "Valor_Faturado")
        variacao = ((atual - anterior) / anterior) if anterior else 0.0
        c1, c2, c3 = st.columns(3)
        c1.metric("Últimos 12 meses", f"R$ {atual:,.0f}")
        c2.metric("12 meses anteriores", f"R$ {anterior:,.0f}")
        c3.metric("Variação", f"{variacao:+.1%}")
        with card("faturamento-tendencia"):
            st.bar_chart(df_fat.set_index("Mes")["Valor_Faturado"])

    st.divider()

    st.subheader("Devoluções / abatimentos de negócio")
    st.caption("Exclui `Tipo_Documento_Contabil = 'RV'` (transferência de faturamento de rotina).")
    if df_dev.empty:
        st.info("Sem dado de devolução no período.")
    else:
        atual, anterior = _resumo_ano_atual_vs_anterior(df_dev, "Valor")
        variacao = ((atual - anterior) / anterior) if anterior else 0.0
        c1, c2, c3 = st.columns(3)
        c1.metric("Últimos 12 meses", f"R$ {atual:,.0f}")
        c2.metric("12 meses anteriores", f"R$ {anterior:,.0f}")
        c3.metric("Variação", f"{variacao:+.1%}")
        with card("faturamento-devolucoes"):
            st.bar_chart(df_dev.set_index("Mes")["Valor"])
