"""Página: Material — ficha de cadastro do material (`dim_material_sap`).

Reduzida em 2026-09-04: as abas de Pedido+Estoque+Fatura e Simulação FIFO por material
saíram daqui por ficarem redundantes com a página **Pendência x Estoque** (ranking de
materiais sem cobertura + drill-down por pedido, com classificação melhor). Esta página
virou pura consulta de cadastro — descrição, tipo, status, unidade de medida, peso — sem
duplicar quantidade/estoque (isso mora nas páginas Estoque/Pendência x Estoque).

Mostra por padrão o catálogo de produto acabado (`materiais_catalogo`, sem precisar digitar
nada) — clique numa linha pra abrir a ficha completa (`ficha_material`, `SELECT *`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import ficha_material, materiais_catalogo  # noqa: E402
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(page_title="Material — Vendas SAP", page_icon="🧪", layout="wide")
st.title(":material/inventory: Material: ficha de cadastro")
st.caption(
    "Dado cadastral do material (`dim_material_sap`) — descrição, tipo, status, unidade "
    "de medida, peso. Pra pedido/estoque/fatura ou fila FIFO por material, veja a página "
    "**Pendência x Estoque** (filtre por Material e clique num pedido pro detalhe completo)."
)

_catalogo_cached = st.cache_data(ttl=600)(materiais_catalogo)
_ficha_cached = st.cache_data(ttl=600)(ficha_material)

col1, col2 = st.columns([1, 2])
with col1:
    codigo_produto = st.text_input("Código do material (opcional)", placeholder="ex.: PA5522", key="material_codigo").strip().upper() or None
with col2:
    filtro_nome = st.text_input("Ou filtre o catálogo pelo nome (parcial)", placeholder="ex.: HEPAMAX", key="material_filtro_nome").strip() or None

if codigo_produto:
    df_ficha = _ficha_cached(codigo_produto)
    if df_ficha.empty:
        st.info("Nenhum material encontrado com esse código.")
    else:
        if len(df_ficha) > 1:
            st.caption(f"{len(df_ficha)} linhas encontradas pra esse código (mais de 1 Mandante) — mostrando todas.")
        for _, linha in df_ficha.iterrows():
            with card(f"material-ficha-{linha.get('Mandante', '')}"):
                st.dataframe(linha.to_frame(name="Valor"), width="stretch")
else:
    st.caption(
        "Catálogo de produto acabado (~1.812 materiais) — clique numa linha pra abrir a "
        "ficha completa, ou digite um código/nome acima pra filtrar direto."
    )
    df_catalogo = _catalogo_cached(somente_acabado=True, filtro_nome=filtro_nome)

    if df_catalogo.empty:
        st.info("Nenhum material encontrado com esse filtro.")
    else:
        st.caption(f"{len(df_catalogo):,} material(is)")

        def _ao_selecionar_catalogo() -> None:
            # Callback (roda ANTES do corpo do script na próxima execução) — mesmo padrão
            # de pages/27_Pendencia_x_Estoque.py::_ao_selecionar_ranking, necessário pra
            # escrever em st.session_state["material_codigo"] (widget já instanciado
            # mais acima no mesmo script).
            selecao = st.session_state["material_catalogo_tabela"]["selection"]["rows"]
            if selecao:
                st.session_state["material_codigo"] = df_catalogo.iloc[selecao[0]]["Codigo_Produto"]

        with card("material-catalogo"):
            st.dataframe(
                df_catalogo,
                width="stretch",
                hide_index=True,
                on_select=_ao_selecionar_catalogo,
                selection_mode="single-row",
                key="material_catalogo_tabela",
            )
