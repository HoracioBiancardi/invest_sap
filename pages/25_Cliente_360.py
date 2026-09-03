"""Página: Cliente 360 — pedido/pendência/fatura + crédito + devoluções, tudo por cliente.

Reusa scripts/query_vendas_sap.py::cliente_360 — mesmo espírito das páginas Material
(busca por material) e Pedidos > Buscar pedido (busca por pedido), aplicado a Codigo_Cliente.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import cliente_360  # noqa: E402
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(page_title="Cliente 360 — Vendas SAP", page_icon="🏥", layout="wide")
st.title(":material/account_circle: Cliente 360")
st.caption(
    "Informe o código do cliente (`Codigo_Cliente`, ex.: 0001004873 — não o nome) pra ver "
    "pedido/pendência/fatura, crédito e devoluções juntos, sem cruzar página na mão."
)

col1, col2 = st.columns([2, 1])
with col1:
    codigo_cliente = st.text_input("Código do cliente", placeholder="ex.: 0001004873", key="cliente360_codigo").strip()
with col2:
    somente_pendente = st.checkbox("Só backlog aberto (aba Pedido)", value=False, key="cliente360_somente_pendente")

if not codigo_cliente:
    st.caption(
        "Não sabe o código? Busque o nome nas páginas **Pedidos** (ranking de clientes) ou "
        "**Crédito e Devoluções** (filtro por trecho do nome) primeiro."
    )
else:

    @st.cache_data(ttl=300, show_spinner="Consultando cliente...")
    def _cliente_cached(codigo_cliente: str, somente_pendente: bool) -> dict[str, pd.DataFrame]:
        return cliente_360(codigo_cliente, somente_pendente=somente_pendente)

    resultado = _cliente_cached(codigo_cliente, somente_pendente)

    df_pedidos = resultado["Pedido / Pendência / Fatura"]
    if df_pedidos.empty and resultado["Crédito"].empty and resultado["Devoluções / abatimentos (últimos 12 meses)"].empty:
        st.info("Nada encontrado para esse código de cliente.")
    else:
        nome_cliente = df_pedidos["Nome_Cliente"].iloc[0] if not df_pedidos.empty else codigo_cliente
        st.subheader(nome_cliente)

        if not df_pedidos.empty:
            c1, c2, c3 = st.columns(3)
            c1.metric("Itens de pedido", f"{len(df_pedidos):,}")
            c2.metric("Valor pendente de faturamento", f"R$ {df_pedidos['Valor_Pendente_Faturamento'].sum():,.2f}")
            c3.metric("Valor faturado (histórico da consulta)", f"R$ {df_pedidos['Valor_Liquido_Faturado'].sum():,.2f}")

        df_credito = resultado["Crédito"]
        if not df_credito.empty:
            bloqueado = (df_credito["Flag_Cliente_Bloqueado"] == "X").any()
            credito_disponivel = df_credito["Valor_Credito_Disponivel"].min()
            if bloqueado or credito_disponivel < 0:
                st.warning(
                    f"Cliente **{'bloqueado por crédito' if bloqueado else 'sem limite de crédito disponível'}** "
                    f"— pior caso entre áreas de crédito: R$ {credito_disponivel:,.2f} disponível."
                )

        st.divider()

        tab_pedido, tab_credito, tab_devolucao = st.tabs(
            ["Pedido / Pendência / Fatura", "Crédito", "Devoluções (12 meses)"]
        )

        with tab_pedido:
            with card("cliente360-pedido"):
                if df_pedidos.empty:
                    st.info("Nenhum pedido encontrado para esse filtro.")
                else:
                    colunas = [
                        "Numero_Pedido", "Item_Pedido", "Data_Inclusao_Pedido", "Codigo_Produto",
                        "Descricao_Produto", "Nome_Centro", "Qtd_Pedida", "Qtd_Faturada",
                        "Qtd_Pendente_Operacional", "Valor_Liquido_Pedido", "Valor_Liquido_Faturado",
                        "Valor_Pendente_Faturamento", "Status_Pendencia", "Status_Faturamento",
                    ]
                    st.dataframe(df_pedidos[colunas], width="stretch", hide_index=True)

        with tab_credito:
            with card("cliente360-credito"):
                if df_credito.empty:
                    st.info("Nenhum registro de crédito encontrado para esse cliente.")
                else:
                    st.dataframe(df_credito, width="stretch", hide_index=True)

        with tab_devolucao:
            df_dev = resultado["Devoluções / abatimentos (últimos 12 meses)"]
            with card("cliente360-devolucao"):
                if df_dev.empty:
                    st.info("Nenhuma devolução/abatimento nos últimos 12 meses.")
                else:
                    st.dataframe(df_dev, width="stretch", hide_index=True)
