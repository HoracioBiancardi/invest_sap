"""Filtros e blocos compartilhados entre páginas — porte de `scripts/ui_theme.py`
(`render_filtro_periodo_tipo_cliente`, `render_valor_convertido_brl`),
`scripts/ui_filtros_comercial.py` e `scripts/ui_filtros_executivo.py`.

Período e Tipo de cliente continuam compartilhados entre as páginas que os usam (mesmo
comportamento das chaves `flt_*` do `st.session_state`): o valor escolhido numa página vale
nas outras da mesma sessão (`compartilhado=True` → `ctx.estado`).
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

import pandas as pd

from scripts import app_db
from scripts.query_faturamento_comercial import DIMENSOES_FATURAMENTO, valores_dimensao
from scripts.query_vendas_sap import (
    MOEDAS_CAMBIO_DISPONIVEL,
    converter_para_brl,
    listar_vendedores,
    taxas_cambio_brl,
)
from web import fmt
from web.cache import cached
from web.ui import Node

TIPOS_CLIENTE = ["Todos", "Governo", "Privado"]

# Valores possíveis de Status_Pendencia em fct_pendencia_sap (CASE fixo no model dbt
# gold/vendas_sap/fct_pendencia_sap.sql — manter manualmente se o model mudar).
STATUS_PENDENCIA_OPCOES = [
    "Concluido",
    "Pendente Logistico e Fiscal",
    "Pendente Logistico (Remessa)",
    "Pendente Fiscal (Faturamento)",
]


def config(chave: str) -> bool:
    """Flag booleana das Configurações do Admin."""
    return bool(app_db.get_setting(chave))


# ── consultas compartilhadas (cache) ─────────────────────────────────────────


@cached(ttl=3600, warm=True, nome="câmbio TCURR (HANA)")
def taxas() -> pd.DataFrame:
    return taxas_cambio_brl()


def para_brl(df: pd.DataFrame, valor_col: str, data_col: str, moeda_col: str = "Moeda") -> pd.Series:
    """`converter_para_brl` com a tabela de câmbio do cache (evita reconsultar o HANA)."""
    return converter_para_brl(df, valor_col, moeda_col=moeda_col, data_col=data_col, taxas=taxas())


@cached(ttl=1800, warm=True, nome="valores de dimensão comercial")
def valores_dimensao_cached(dimensao: str) -> list[str]:
    return valores_dimensao(dimensao)


@cached(ttl=1800, warm=True, nome="lista de vendedores")
def vendedores_cached() -> list[tuple[str, str]]:
    df = listar_vendedores()
    return list(zip(df["Nome_Vendedor"], df["Codigo_Vendedor"]))


# ── filtros ──────────────────────────────────────────────────────────────────


def tipo_cliente_param(opcao: str) -> Optional[str]:
    return None if opcao == "Todos" else opcao


def filtro_periodo_tipo_cliente(p: Node) -> tuple[_dt.date, _dt.date, str]:
    """Período (De/Até) + Tipo de cliente, compartilhados entre páginas.

    Returns:
        (data_inicio, data_fim, opção de tipo de cliente — "Todos"/"Governo"/"Privado").
    """
    hoje = _dt.date.today()
    c1, c2, c3 = p.columns([1, 1, 1])
    inicio = c1.date_input("Período — de", "flt_de", hoje - _dt.timedelta(days=30), maximo=hoje, compartilhado=True)
    fim = c2.date_input("até", "flt_ate", hoje, maximo=hoje, compartilhado=True)
    tipo = c3.selectbox("Tipo de cliente", TIPOS_CLIENTE, "flt_tipo_cliente", compartilhado=True)
    if inicio > fim:
        p.warning("A data inicial é depois da final — invertidas para consultar.")
        inicio, fim = fim, inicio
    return inicio, fim, tipo


def filtro_tipo_cliente(p: Node) -> str:
    """Só o Tipo de cliente (mesma chave compartilhada de `filtro_periodo_tipo_cliente`)."""
    c1, _ = p.columns([1, 2])
    return c1.selectbox("Tipo de cliente", TIPOS_CLIENTE, "flt_tipo_cliente", compartilhado=True)


def filtros_comercial(p: Node, prefixo: str, dimensoes: list[str]) -> dict[str, str]:
    """1 select por dimensão comercial; devolve só as escolhidas (`{dimensao: valor}`).

    Listas grandes (Cliente, Produto...) chegam ao navegador pelo IndexedDB (`dim=`), não no
    HTML de cada recarga.
    """
    for dimensao in dimensoes:
        if dimensao not in DIMENSOES_FATURAMENTO:
            raise ValueError(f"dimensao de filtro inválida: {dimensao!r}")
    corpo = p.expander("Filtros de recorte comercial", aberto=any(p.ctx.get(f"{prefixo}_f_{d}", "Todos") != "Todos" for d in dimensoes))
    corpo.caption(
        "Restringe todos os números da página a um valor específico — não muda a dimensão do "
        "gráfico/tabela, só filtra o que entra na conta."
    )
    filtros: dict[str, str] = {}
    cols = corpo.columns(3)
    for i, dimensao in enumerate(dimensoes):
        opcoes = ["Todos"] + valores_dimensao_cached(dimensao)
        valor = cols[i % 3].selectbox(
            dimensao, opcoes, f"{prefixo}_f_{dimensao}", dim=f"com:{dimensao}" if len(opcoes) > 60 else None
        )
        if valor and valor != "Todos":
            filtros[dimensao] = valor
    if filtros:
        p.caption("Filtro ativo: " + ", ".join(f"**{k}** = {v}" for k, v in filtros.items()))
    return filtros


def filtros_executivo(
    p: Node,
    prefixo: str,
    mostrar_pedido: bool = False,
    mostrar_material: bool = False,
    mostrar_status_pendencia: bool = False,
    mostrar_vendedor: bool = False,
) -> dict[str, object]:
    """Porte de `render_filtros_executivo` — ver docstring lá para cada chave devolvida."""
    ligados = [mostrar_pedido, mostrar_material, mostrar_status_pendencia, mostrar_vendedor]
    filtros: dict[str, object] = {}
    if not any(ligados):
        return filtros
    cols = iter(p.columns(sum(ligados)))
    if mostrar_pedido:
        valor = next(cols).text_input("Número do pedido", f"{prefixo}_pedido", placeholder="ex.: 138524")
        if valor.strip():
            filtros["numero_pedido"] = valor.strip()
    if mostrar_material:
        valor = next(cols).text_input("Código do material", f"{prefixo}_material", placeholder="ex.: PA5522")
        if valor.strip():
            filtros["codigo_produto"] = valor.strip().upper()
    if mostrar_status_pendencia:
        valor = next(cols).multiselect("Status da pendência", STATUS_PENDENCIA_OPCOES, f"{prefixo}_status_pendencia")
        if valor:
            filtros["status_pendencia"] = valor
    if mostrar_vendedor:
        opcoes = dict(vendedores_cached())
        nome = next(cols).selectbox("Vendedor", ["Todos"] + list(opcoes), f"{prefixo}_vendedor", dim="vendedores")
        if nome and nome != "Todos" and nome in opcoes:
            filtros["codigo_vendedor"] = opcoes[nome]
    return filtros


def nota_config(*, intercompany: bool = False, org_internacional: bool = False, estoque_internacional: bool = False) -> str:
    """Trecho de caption avisando quais exclusões do Admin estão ativas."""
    partes = []
    if intercompany and config("excluir_intercompany"):
        partes.append(' Cliente intercompany (filial "BLAU*") excluído por config do Admin.')
    if org_internacional and config("excluir_org_vendas_internacional"):
        partes.append(" Organização de Vendas Colômbia/Uruguai excluída por config do Admin.")
    if estoque_internacional and config("excluir_estoque_internacional"):
        partes.append(" Estoque de Uruguai/Colômbia excluído das quantidades por config do Admin.")
    return "".join(partes)


# ── blocos ───────────────────────────────────────────────────────────────────


def valor_convertido_brl(p: Node, df: pd.DataFrame, valor_col: str, data_col: str, rotulo: str, moeda_col: str = "Moeda") -> None:
    """Total convertido pra BRL (TCURR) + quebra por moeda num expander (porte de
    `ui_theme.render_valor_convertido_brl`)."""
    if df.empty:
        p.metric(f"{rotulo} (BRL)", "R$ 0")
        return
    valores = para_brl(df, valor_col, data_col, moeda_col)
    p.metric(f"{rotulo} (convertido p/ BRL)", fmt.brl(valores.sum()))
    sem_taxa = valores.isna() & (df[moeda_col] != "BRL")
    if sem_taxa.any():
        moedas = sorted(df.loc[sem_taxa, moeda_col].unique())
        p.caption(
            f":material/info: {fmt.num(int(sem_taxa.sum()))} linha(s) em moeda sem taxa de câmbio "
            f"disponível ({', '.join(moedas)}) não entraram no total acima (só "
            f"{', '.join(MOEDAS_CAMBIO_DISPONIVEL)} têm taxa real nesta base)."
        )
    if (df[moeda_col] != "BRL").any():
        p.expander("Ver por moeda, sem conversão (valor original de cada uma)").valor_por_moeda(df, valor_col, moeda_col)
