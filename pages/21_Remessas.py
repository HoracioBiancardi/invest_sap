"""Página: Remessas — reusa scripts/query_vendas_sap.py::remessas/remessas_resumo.

Página nova (não existia consulta nenhuma sobre `fct_remessa_itens_sap` antes desta
entrega). Ver docstring de `remessas()` pro caveat importante: os 4 campos de status SAP
(`Wbsta`/`Lfgsa`/`Lvsta`/`Fksta`) estão **100% NULL** nesta base — não dá pra filtrar ou
segmentar remessa por eles hoje. Também não há valor financeiro nem data de entrega
prevista/atraso no modelo atual — só volume (quantidade/peso) e a data real de saída.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import remessas, remessas_resumo  # noqa: E402
from scripts.ui_filtros_executivo import render_filtros_executivo  # noqa: E402
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(page_title="Remessas — Vendas SAP", page_icon="🚚", layout="wide")
st.title(":material/local_shipping: Remessas")
st.caption(
    "Fonte: `GOLD.vendas_sap.fct_remessa_itens_sap` (LIKP/LIPS — grão Entrega+Item). Os "
    "campos de status SAP (Wbsta/Lfgsa/Lvsta/Fksta) estão **100% NULL** nesta base — não "
    "aparecem aqui como filtro. Sem valor financeiro nem prazo/atraso disponível no modelo "
    "atual; pra status de pendência confiável, veja `Status_Pendencia` nas páginas "
    "**Pedidos**/**Material**."
)

hoje = datetime.date.today()
data_inicio_padrao = hoje - datetime.timedelta(days=30)
col_p1, col_p2 = st.columns(2)
with col_p1:
    data_inicio = st.date_input("De", value=data_inicio_padrao, max_value=hoje, key="remessas_data_inicio")
with col_p2:
    data_fim = st.date_input("Até", value=hoje, max_value=hoje, key="remessas_data_fim")
st.caption(
    "Período por `Data_Remessa` (data real de saída) — sem período, a consulta varre o "
    "histórico inteiro (lento e pouco útil pra uma lista de item a item)."
)

filtros = render_filtros_executivo("remessas", mostrar_pedido=True, mostrar_material=True)
numero_pedido = filtros.get("numero_pedido")
codigo_produto = filtros.get("codigo_produto")


@st.cache_data(ttl=300, show_spinner="Consultando resumo de remessas...")
def _resumo_cached(data_inicio: datetime.date, data_fim: datetime.date) -> pd.DataFrame:
    return remessas_resumo(data_inicio=data_inicio, data_fim=data_fim)


@st.cache_data(ttl=300, show_spinner="Consultando remessas...")
def _remessas_cached(
    numero_pedido: Optional[str], codigo_produto: Optional[str], data_inicio: datetime.date, data_fim: datetime.date
) -> pd.DataFrame:
    return remessas(
        numero_pedido=numero_pedido,
        codigo_produto=codigo_produto,
        data_inicio=data_inicio,
        data_fim=data_fim,
        limit=2000,
    )


df_resumo = _resumo_cached(data_inicio, data_fim)

st.subheader("Volume por Tipo de Remessa x Centro")
with card("remessas-resumo"):
    if df_resumo.empty:
        st.info("Nada encontrado para esse período.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Remessas (documentos distintos)", f"{df_resumo['Qtd_Remessas'].sum():,.0f}")
        c2.metric("Qtd remetida total", f"{df_resumo['Qtd_Remetida_Total'].sum():,.0f}")
        c3.metric("Peso líquido total (kg)", f"{df_resumo['Peso_Liquido_Total'].sum():,.0f}")
        col_a, col_b = st.columns([1, 1])
        with col_a:
            st.bar_chart(df_resumo.groupby("Codigo_Centro")["Qtd_Remetida_Total"].sum())
        with col_b:
            st.bar_chart(df_resumo.groupby("Tipo_Remessa")["Qtd_Remetida_Total"].sum())
        st.dataframe(df_resumo, width="stretch", hide_index=True)

st.divider()

st.subheader("Detalhe (item a item)")
df_detalhe = _remessas_cached(numero_pedido, codigo_produto, data_inicio, data_fim)
with card("remessas-detalhe"):
    if df_detalhe.empty:
        st.info("Nada encontrado para esse filtro.")
    else:
        colunas_exibir = [
            "Numero_Entrega", "Item_Entrega", "Numero_Pedido_Origem", "Item_Pedido_Origem",
            "Data_Remessa", "Tipo_Remessa", "Codigo_Produto", "Codigo_Centro", "Codigo_Cliente", "Nome_Cliente",
            "Charg_Numero_Do_Lote", "Qtd_Remetida", "Peso_Liquido", "Rota",
        ]
        st.caption(f"{len(df_detalhe):,} linha(s) (teto de 2.000).")
        st.dataframe(df_detalhe[colunas_exibir], width="stretch", hide_index=True)
