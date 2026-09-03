"""Painel de filtros compartilhado pelas páginas da seção Executivo (Oportunidade, Pedidos,
Remessas, Faturamento, Faturamento x Meta, Estoque, Material, Crédito e Devoluções, Vendedor,
Vendedor x Meta x Faturamento).

Mesma exceção deliberada de `scripts/ui_filtros_comercial.py`: código de UI, não de consulta,
justificado por evitar duplicar o mesmo bloco de widgets em várias páginas.

Diferente do painel comercial (que tem um registro genérico de dimensão em
`query_faturamento_comercial.DIMENSOES_FATURAMENTO`), o lado `query_vendas_sap.py` não tem
esse padrão — cada função de consulta aceita seus próprios parâmetros soltos (pedido, material,
centro, etc.), sem um dicionário de dimensões unificado. Por isso este painel é mais simples e
explícito: cada página liga só os `mostrar_*` que sua(s) função(ões) de consulta de fato aceitam
como parâmetro — não force um filtro numa página cuja consulta não tem esse argumento.
"""

from __future__ import annotations

import streamlit as st

from scripts.query_vendas_sap import listar_vendedores

# Valores possíveis de Status_Pendencia em fct_pendencia_sap (ver dbt model
# gold/vendas_sap/fct_pendencia_sap.sql) — não vem de tabela de domínio, é um CASE fixo no
# model, então a lista abaixo precisa ser mantida manualmente se o model mudar.
STATUS_PENDENCIA_OPCOES = [
    "Concluido",
    "Pendente Logistico e Fiscal",
    "Pendente Logistico (Remessa)",
    "Pendente Fiscal (Faturamento)",
]


@st.cache_data(ttl=1800, show_spinner=False)
def _vendedores_cached() -> list[tuple[str, str]]:
    df = listar_vendedores()
    return list(zip(df["Nome_Vendedor"], df["Codigo_Vendedor"]))


def render_filtros_executivo(
    key_prefix: str,
    mostrar_pedido: bool = False,
    mostrar_material: bool = False,
    mostrar_status_pendencia: bool = False,
    mostrar_vendedor: bool = False,
) -> dict[str, object]:
    """Renderiza os widgets ativados e devolve só o que foi de fato preenchido.

    Chaves possíveis no retorno: `numero_pedido` (str), `codigo_produto` (str, já em
    upper()), `status_pendencia` (list[str], vazio se nada selecionado — omitido do dict
    nesse caso) e `codigo_vendedor` (str, código Salesforce — None se "Todos").

    Args:
        key_prefix: prefixo único por página pra `st.session_state` (evita colisão de
            `key` entre páginas — cada página deve passar um prefixo diferente).
        mostrar_pedido, mostrar_material, mostrar_status_pendencia, mostrar_vendedor:
            liga cada widget individualmente — só ative o que a(s) função(ões) de consulta
            daquela página de fato aceitam como parâmetro.
    """
    filtros: dict[str, object] = {}
    if not any([mostrar_pedido, mostrar_material, mostrar_status_pendencia, mostrar_vendedor]):
        return filtros

    cols = st.columns(sum([mostrar_pedido, mostrar_material, mostrar_status_pendencia, mostrar_vendedor]))
    i = 0
    if mostrar_pedido:
        with cols[i]:
            valor = st.text_input("Número do pedido", placeholder="ex.: 138524", key=f"{key_prefix}_pedido")
        if valor:
            filtros["numero_pedido"] = valor.strip()
        i += 1
    if mostrar_material:
        with cols[i]:
            valor = st.text_input("Código do material", placeholder="ex.: PA5522", key=f"{key_prefix}_material")
        if valor:
            filtros["codigo_produto"] = valor.strip().upper()
        i += 1
    if mostrar_status_pendencia:
        with cols[i]:
            valor = st.multiselect(
                "Status da pendência", STATUS_PENDENCIA_OPCOES, key=f"{key_prefix}_status_pendencia"
            )
        if valor:
            filtros["status_pendencia"] = valor
        i += 1
    if mostrar_vendedor:
        with cols[i]:
            with st.spinner("Carregando vendedores..."):
                opcoes_vendedor = dict(_vendedores_cached())
            nome = st.selectbox(
                "Vendedor", ["Todos"] + list(opcoes_vendedor), key=f"{key_prefix}_vendedor"
            )
        if nome != "Todos":
            filtros["codigo_vendedor"] = opcoes_vendedor[nome]
    return filtros
