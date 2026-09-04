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

from scripts.query_vendas_sap import buscar_cliente_por_nome, cliente_360, top_clientes_pendentes  # noqa: E402
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(page_title="Cliente 360 — Vendas SAP", page_icon="🏥", layout="wide")
st.title(":material/account_circle: Cliente 360")
st.caption(
    "Busque pelo nome (parcial) pra achar o código, ou informe o `Codigo_Cliente` "
    "direto se já souber — ver pedido/pendência/fatura, crédito e devoluções juntos, "
    "sem cruzar página na mão."
)


@st.cache_data(ttl=600, show_spinner="Buscando cliente pelo nome...")
def _busca_nome_cached(nome_fragmento: str) -> pd.DataFrame:
    return buscar_cliente_por_nome(nome_fragmento)


@st.cache_data(ttl=600, show_spinner="Consultando top clientes por pendência...")
def _top_clientes_cached(n: int) -> pd.DataFrame:
    return top_clientes_pendentes(n=n)


def _selecionar_cliente_da_busca() -> None:
    # Callback (roda ANTES do corpo do script na próxima execução) — só assim dá pra
    # escrever em st.session_state["cliente360_codigo"], que é lido pelo text_input
    # declarado mais abaixo no mesmo script (mesmo padrão de
    # pages/27_Pendencia_x_Estoque.py::_ao_selecionar_ranking).
    escolha = st.session_state.get("cliente360_nome_selecao")
    if escolha:
        st.session_state["cliente360_codigo"] = escolha.rsplit(" — ", 1)[-1].strip()


col_nome, col_codigo, col_check = st.columns([1.3, 1, 1])
with col_nome:
    nome_busca = st.text_input("Buscar pelo nome (parcial, opcional)", placeholder="ex.: BRAZMIX", key="cliente360_nome_busca").strip()
    if nome_busca:
        df_matches = _busca_nome_cached(nome_busca)
        if df_matches.empty:
            st.caption("Nenhum cliente encontrado com esse nome.")
        else:
            opcoes = [f"{row.Nome_Cliente} — {row.Codigo_Cliente}" for row in df_matches.itertuples()]
            st.selectbox(
                f"{len(opcoes)} resultado(s) — escolha um",
                opcoes,
                index=None,
                placeholder="Escolha um cliente...",
                key="cliente360_nome_selecao",
                on_change=_selecionar_cliente_da_busca,
            )
with col_codigo:
    codigo_cliente = st.text_input("Código do cliente", placeholder="ex.: 0001004873", key="cliente360_codigo").strip()
with col_check:
    somente_pendente = st.checkbox("Só backlog aberto (aba Pedido)", value=False, key="cliente360_somente_pendente")

if not codigo_cliente:
    st.caption("Ou escolha direto um dos clientes com mais pendência (clique numa linha):")
    df_top_clientes = _top_clientes_cached(10)
    if not df_top_clientes.empty:

        def _ao_selecionar_top_cliente() -> None:
            # Callback (roda ANTES do corpo do script na próxima execução) — mesmo
            # padrão de _selecionar_cliente_da_busca acima e de
            # pages/27_Pendencia_x_Estoque.py::_ao_selecionar_ranking.
            selecao = st.session_state["cliente360_top_tabela"]["selection"]["rows"]
            if selecao:
                st.session_state["cliente360_codigo"] = df_top_clientes.iloc[selecao[0]]["Codigo_Cliente"]

        with card("cliente360-top"):
            st.dataframe(
                df_top_clientes.style.format(
                    {"Qtd_Pendente_Total": "{:,.0f}", "Valor_Pendente_Total": "R$ {:,.2f}"}
                ),
                width="stretch",
                hide_index=True,
                on_select=_ao_selecionar_top_cliente,
                selection_mode="single-row",
                key="cliente360_top_tabela",
            )
else:

    @st.cache_data(ttl=300, show_spinner="Consultando cliente...")
    def _cliente_cached(codigo_cliente: str, somente_pendente: bool) -> dict[str, pd.DataFrame]:
        return cliente_360(codigo_cliente, somente_pendente=somente_pendente)

    resultado = _cliente_cached(codigo_cliente, somente_pendente)

    df_pedidos = resultado["Pedido / Pendência / Fatura"]
    if all(resultado[chave].empty for chave in resultado):
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

        tab_pedido, tab_credito, tab_devolucao, tab_oportunidade, tab_remessa = st.tabs(
            ["Pedido / Pendência / Fatura", "Crédito", "Devoluções (12 meses)", "Oportunidade", "Remessas"]
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

        with tab_oportunidade:
            df_opp = resultado["Oportunidade"]
            df_opp_match = df_opp[df_opp["Nome_Oportunidade"].notna()] if not df_opp.empty else df_opp
            with card("cliente360-oportunidade"):
                if df_opp_match.empty:
                    st.info("Nenhuma Oportunidade vinculada nos últimos 24 meses (cobertura de ~73% medida — ver docs).")
                else:
                    valor_oportunidade = df_opp_match.drop_duplicates(subset=["Nome_Oportunidade", "Data_Criacao_Oportunidade"])[
                        "Valor_Oportunidade"
                    ].sum()
                    st.metric("Valor de Oportunidade (deduplicado)", f"R$ {valor_oportunidade:,.2f}")
                    colunas_opp = [
                        "Numero_Pedido", "Item_Pedido", "Codigo_Produto", "Descricao_Produto",
                        "Nome_Oportunidade", "Estagio_Oportunidade", "Oportunidade_Ganha",
                        "Valor_Oportunidade", "Valor_Item_Oportunidade", "Status_Pendencia",
                    ]
                    st.dataframe(df_opp_match[colunas_opp], width="stretch", hide_index=True)

        with tab_remessa:
            df_rem = resultado["Remessas"]
            with card("cliente360-remessa"):
                if df_rem.empty:
                    st.info("Nenhuma remessa nos últimos 24 meses.")
                else:
                    colunas_remessa = [
                        "Numero_Entrega", "Item_Entrega", "Numero_Pedido_Origem", "Data_Remessa",
                        "Tipo_Remessa", "Codigo_Produto", "Codigo_Centro", "Charg_Numero_Do_Lote", "Qtd_Remetida",
                    ]
                    st.dataframe(df_rem[colunas_remessa], width="stretch", hide_index=True)
