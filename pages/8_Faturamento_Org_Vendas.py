"""Página: faturamento por Organização de Vendas (SAP) x Linha de Negócio (comercial).

Reusa scripts/query_vendas_sap.py::faturamento_por_org_vendas_linha_negocio. Ver docstring
da função pra entender por que são 2 dimensões independentes, não uma hierarquia 1:1.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import faturamento_por_org_vendas_linha_negocio  # noqa: E402
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(page_title="Faturamento por Org Vendas — Vendas SAP", page_icon="🏢", layout="wide")
st.title(":material/corporate_fare: Faturamento: Organização de Vendas x Linha de Negócio")
st.caption(
    "Organização de Vendas é o campo SAP (VKORG) — quem faturou. Linha de Negócio "
    "(AESTHETICS / AESTHETICS BLAU-BRG / FARMA / ONCO-HEMATO / NÃO ALOCADO) vem em 2 "
    "camadas: cruzamento manual Cliente → `vendas.dim_cliente_setor` + "
    "`vendas.dim_estrutura` (~52% dos clientes), com fallback por heurística de produto "
    "dominante (`vendas.dim_produto.unidade_de_negocio`) pra quem não tem match manual "
    "(eleva a cobertura pra ~87%). Coluna **Origem_Linha_Negocio** no detalhe indica qual "
    "camada resolveu cada linha — a heurística tem precisão menor pra ONCO/HEMATO (~64%, "
    "confunde com FARMA), ver `docs/CONTEXTO_VENDAS_SAP.md` §8.2. São 2 dimensões "
    "**independentes**: a mesma Organização de Vendas fatura pra várias linhas de negócio "
    "ao mesmo tempo, não é uma hierarquia 1 pra 1."
)

tipo_cliente_opcao = st.session_state.get("flt_tipo_cliente", "Todos")
tipo_cliente = None if tipo_cliente_opcao == "Todos" else tipo_cliente_opcao
data_inicio = st.session_state.get("flt_data_inicio", datetime.date.today() - datetime.timedelta(days=30))
data_fim = st.session_state.get("flt_data_fim", datetime.date.today())
st.caption(
    f"Usando filtro global: período de **{data_inicio:%d/%m/%Y}** a **{data_fim:%d/%m/%Y}**, "
    f"tipo de cliente **{tipo_cliente_opcao}** — ajuste no sidebar."
)


@st.cache_data(ttl=300, show_spinner="Consultando faturamento por Org Vendas x Linha de Negócio...")
def _faturamento_cached(data_inicio: datetime.date, data_fim: datetime.date, tipo_cliente: Optional[str]) -> pd.DataFrame:
    return faturamento_por_org_vendas_linha_negocio(data_inicio=data_inicio, data_fim=data_fim, tipo_cliente=tipo_cliente)


df = _faturamento_cached(data_inicio, data_fim, tipo_cliente)

if df.empty:
    st.info("Nada encontrado para esse período.")
else:
    c1, c2 = st.columns(2)
    c1.metric("Valor Faturado Total", f"R$ {df['Valor_Faturado'].sum():,.2f}")
    c2.metric("Qtd Faturada Total", f"{df['Qtd_Faturada'].sum():,.0f}")

    st.divider()

    with card("fatorg-por-dimensao"):
        col_a, col_b = st.columns([1, 1])
        with col_a:
            st.subheader("Por Organização de Vendas")
            st.bar_chart(df.groupby("Descricao_Org_Vendas")["Valor_Faturado"].sum())
        with col_b:
            st.subheader("Por Linha de Negócio")
            st.bar_chart(df.groupby("Linha_Negocio")["Valor_Faturado"].sum())

    st.divider()

    st.subheader("Matriz Org Vendas x Linha de Negócio (R$)")
    matriz = df.pivot_table(
        index="Descricao_Org_Vendas", columns="Linha_Negocio", values="Valor_Faturado", aggfunc="sum", fill_value=0
    )
    with card("fatorg-matriz"):
        st.dataframe(matriz.style.format("R$ {:,.2f}"), width="stretch")

    st.divider()

    st.subheader("Detalhe")
    with card("fatorg-detalhe"):
        st.dataframe(
            df[
                [
                    "Codigo_Org_Vendas",
                    "Descricao_Org_Vendas",
                    "Linha_Negocio",
                    "Origem_Linha_Negocio",
                    "Valor_Faturado",
                    "Qtd_Faturada",
                    "Qtd_Itens",
                ]
            ],
            width="stretch",
            hide_index=True,
        )
