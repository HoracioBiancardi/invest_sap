"""Página: visão de pendências (backlog) — reusa scripts/query_vendas_sap.py."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Garante que a raiz do projeto está no sys.path independente de como o arquivo foi
# invocado (streamlit run, execução direta, etc.) — necessário para o pacote `scripts`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import (  # noqa: E402
    aging_pendencias,
    pendencia_por_tipo_ordem_venda,
    pendencia_status_estoque,
    top_clientes_pendentes,
)
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(page_title="Pendências — Vendas SAP", page_icon="📦", layout="wide")
st.title(":material/pending_actions: Pendências (backlog)")
st.caption("Fonte: `GOLD.vendas_sap.fct_pendencia_sap` / `fct_limite_credito_sap`.")

tipo_cliente_opcao = st.session_state.get("flt_tipo_cliente", "Todos")
tipo_cliente = None if tipo_cliente_opcao == "Todos" else tipo_cliente_opcao
st.caption(
    f"Usando filtro global de tipo de cliente: **{tipo_cliente_opcao}** (ajuste no sidebar). "
    "O período (dias) do filtro global **não** se aplica aqui de propósito — um backlog "
    "existe pra mostrar tudo que está em aberto, inclusive o que é antigo; limitar por data "
    "esconderia justamente os itens mais críticos de aging."
)

_cache = st.cache_data(ttl=300)
_aging_cached = _cache(aging_pendencias)
_estoque_cached = _cache(pendencia_status_estoque)
_top_clientes_cached = _cache(top_clientes_pendentes)
_tipo_ordem_cached = _cache(pendencia_por_tipo_ordem_venda)

df_aging = _aging_cached(tipo_cliente=tipo_cliente)

st.subheader("Aging do backlog aberto")
with card("pendencias-aging"):
    col1, col2 = st.columns([1, 1])
    with col1:
        st.dataframe(df_aging, width="stretch", hide_index=True)
    with col2:
        if not df_aging.empty:
            st.bar_chart(df_aging.set_index("Faixa_Aging")["Valor_Pendente_Total"])

st.divider()

df_estoque = _estoque_cached(tipo_cliente=tipo_cliente)

st.subheader("Backlog por cobertura de estoque")
with card("pendencias-estoque"):
    col1, col2 = st.columns([1, 1])
    with col1:
        st.dataframe(df_estoque, width="stretch", hide_index=True)
    with col2:
        if not df_estoque.empty:
            st.bar_chart(df_estoque.set_index("Status_Pendencia_Estoque")["Valor_Pendente_Total"])

st.divider()

st.subheader("Top clientes por valor pendente")
n = st.slider("Quantos clientes mostrar", min_value=5, max_value=50, value=20, step=5)
df_clientes = _top_clientes_cached(n, tipo_cliente=tipo_cliente)
with card("pendencias-top-clientes"):
    st.dataframe(df_clientes, width="stretch", hide_index=True)

st.divider()

st.subheader("Backlog por Tipo de Ordem de Venda")
st.caption(
    "Tipo_Ordem_Venda é o código SAP (AUART) do pedido — sem tradução pra texto disponível "
    "nesta base, mas útil pra ver se o backlog está concentrado num tipo específico de ordem."
)
df_tipo_ordem = _tipo_ordem_cached(tipo_cliente=tipo_cliente)
with card("pendencias-tipo-ordem"):
    col3, col4 = st.columns([1, 1])
    with col3:
        st.dataframe(df_tipo_ordem, width="stretch", hide_index=True)
    with col4:
        if not df_tipo_ordem.empty:
            st.bar_chart(
                df_tipo_ordem.set_index("Tipo_Ordem_Venda")["Valor_Pendente_Total"].head(15)
            )

st.caption(
    "Crédito (limite/exposição) e devoluções/abatimentos com motivo agora têm página própria "
    "— veja **Crédito e Devoluções** no menu à esquerda."
)
