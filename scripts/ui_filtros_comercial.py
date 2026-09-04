"""Painel de filtros de recorte compartilhado pelas páginas de Faturamento (Painel Vendas)
— `pages/12_Painel_Vendas.py` a `pages/15_Produto_Cliente.py`.

**Exceção deliberada** à convenção do resto de `scripts/` (só consulta, sem `streamlit`):
as 4 páginas acima precisavam do mesmo painel de ~10 `st.selectbox` lado a lado; duplicar
esse bloco 4x criava risco real de as páginas divergirem silenciosamente (uma esquecer de
filtrar, outra usar uma chave de `session_state` colidindo com outra). Um helper único aqui,
importado por elas, evita isso — mas continua sendo código de UI, não de consulta.
"""

from __future__ import annotations

import streamlit as st

from scripts.query_faturamento_comercial import DIMENSOES_FATURAMENTO, valores_dimensao


@st.cache_data(ttl=1800, show_spinner=False)
def _valores_cached(dimensao: str) -> list[str]:
    return valores_dimensao(dimensao)


def render_filtros_comercial(key_prefix: str, dimensoes: list[str]) -> dict[str, str]:
    """Renderiza 1 `st.selectbox` por dimensão em `dimensoes` (chaves de
    `DIMENSOES_FATURAMENTO`) e devolve só o que foi de fato escolhido (`{dimensao: valor}`,
    sem as que ficaram em "Todos") — pronto pra passar como `filtros=` pras funções de
    `scripts/query_faturamento_comercial.py`.

    Args:
        key_prefix: prefixo único por página pra `st.session_state` (evita colisão de `key`
            entre páginas — cada página deve passar um prefixo diferente, ex. nome do arquivo).
        dimensoes: quais dimensões oferecer como filtro nesta página — nem toda página deve
            oferecer todas (ex.: a página de Meta x Realizado só deve oferecer as dimensões
            que a Meta suporta, ver `DIMENSOES_META` em `query_faturamento_comercial.py`).
    """
    for dimensao in dimensoes:
        if dimensao not in DIMENSOES_FATURAMENTO:
            raise ValueError(f"dimensao de filtro inválida: {dimensao!r}")

    filtros: dict[str, str] = {}
    st.caption(
        "Restringe todos os números da página a um valor específico — não muda a "
        "dimensão do gráfico/tabela abaixo, só filtra o que entra na conta."
    )
    # Busca tudo antes de desenhar qualquer selectbox — 1 spinner só pra todas as
    # dimensões, em vez de widgets aparecendo um a um sem nenhum indicador (o que parecia
    # página travada na primeira carga, com cache frio).
    with st.spinner("Carregando opções de filtro..."):
        valores_por_dimensao = {d: _valores_cached(d) for d in dimensoes}
    cols = st.columns(3)
    for i, dimensao in enumerate(dimensoes):
        with cols[i % 3]:
            opcoes = ["Todos"] + valores_por_dimensao[dimensao]
            valor = st.selectbox(dimensao, opcoes, key=f"{key_prefix}_filtro_{dimensao}")
            if valor != "Todos":
                filtros[dimensao] = valor
    return filtros
