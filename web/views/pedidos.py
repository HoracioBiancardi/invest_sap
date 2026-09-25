"""Pedidos — backlog aberto (aging, radar de pedido zumbi, cobertura, top clientes, tipo de
ordem), volume de pedidos entrando e rastreio de 1 pedido (porte de `pages/20_Pedidos.py`).

Diferença do Streamlit: cada seção da Visão geral é um bloco carregado em paralelo (antes as 8
consultas rodavam em sequência). O período do filtro não se aplica ao backlog aberto, de
propósito — ele mostra tudo em aberto, inclusive o antigo.
"""

from __future__ import annotations

import datetime
from typing import Optional

import pandas as pd
from markupsafe import Markup

from scripts import app_db
from scripts.query_vendas_sap import (
    aging_pendencias,
    estoque_reservado_por_material_centro,
    pedidos_mensal,
    pedidos_por_cliente,
    pendencia_por_tipo_ordem_venda,
    pendencia_status_estoque,
    pendencias_abertas,
    top_clientes_pendentes,
)
from scripts.trace_pedido import trace_pedido
from web import fmt
from web.cache import cached
from web.ui import Ctx, Node, Pagina
from web.views._comum import config, filtro_tipo_cliente, nota_config, tipo_cliente_param


@cached(ttl=300, nome="pedidos: aging")
def _aging(tipo_cliente: Optional[str], excl: bool) -> pd.DataFrame:
    return aging_pendencias(tipo_cliente=tipo_cliente, excluir_intercompany=excl)


@cached(ttl=300, nome="pedidos: cobertura de estoque")
def _cobertura(tipo_cliente: Optional[str], excl: bool) -> pd.DataFrame:
    return pendencia_status_estoque(tipo_cliente=tipo_cliente, excluir_intercompany=excl)


@cached(ttl=300, warm=True, nome="pedidos: backlog aberto (radar zumbi)")
def _backlog(excl: bool) -> pd.DataFrame:
    return pendencias_abertas(excluir_intercompany=excl)


@cached(ttl=1800, warm=True, nome="pedidos: reserva VBBE")
def _reservado() -> pd.DataFrame:
    return estoque_reservado_por_material_centro()


@cached(ttl=300, nome="pedidos: top clientes pendentes")
def _top_clientes(n: int, tipo_cliente: Optional[str], excl: bool) -> pd.DataFrame:
    return top_clientes_pendentes(n, tipo_cliente=tipo_cliente, excluir_intercompany=excl)


@cached(ttl=300, nome="pedidos: tipo de ordem")
def _tipo_ordem(tipo_cliente: Optional[str], excl: bool) -> pd.DataFrame:
    return pendencia_por_tipo_ordem_venda(tipo_cliente=tipo_cliente, excluir_intercompany=excl)


@cached(ttl=300, nome="pedidos: volume mensal")
def _mensal(meses: int, excl: bool) -> pd.DataFrame:
    return pedidos_mensal(meses, excluir_intercompany=excl)


@cached(ttl=300, nome="pedidos: ranking por cliente")
def _ranking_clientes(inicio: datetime.date, fim: datetime.date, n: int, tipo_cliente: Optional[str], excl: bool) -> pd.DataFrame:
    return pedidos_por_cliente(inicio, fim, n, tipo_cliente, excl)


@cached(ttl=300, nome="pedidos: rastreio de 1 pedido")
def _trace(numero: str, item: Optional[str]) -> dict:
    return trace_pedido(numero, item)


def _tipo(ctx: Ctx) -> Optional[str]:
    return tipo_cliente_param(ctx.get("flt_tipo_cliente") or ctx.estado.get("flt_tipo_cliente", ["Todos"])[-1])


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Pedidos", "receipt_long")
    tipo_opcao = filtro_tipo_cliente(p)
    abas = p.tabs(["Visão geral", "Buscar pedido"], "aba")
    if abas.ativa == 1:
        _buscar(abas.corpo, ctx)
        return
    corpo = abas.corpo
    corpo.caption(
        f"Tipo de cliente: **{tipo_opcao}**. O período **não** se aplica ao backlog aberto de propósito — ele "
        "mostra tudo em aberto, inclusive o que é antigo." + nota_config(intercompany=True)
    )
    for nome, titulo, altura in (
        ("aging", "Aging do backlog aberto", 260),
        ("zumbi", "🧟 Radar de pedido zumbi (visão global, todos os materiais)", 420),
        ("cobertura", "Backlog por cobertura de estoque", 260),
        ("top_clientes", "Top clientes por valor pendente", 320),
        ("tipo_ordem", "Backlog por Tipo de Ordem de Venda", 300),
        ("mensal", "Volume de pedidos entrando no funil", 320),
        ("ranking_clientes", "Ranking de pedidos por cliente", 320),
    ):
        corpo.subheader(titulo)
        corpo.lazy(nome, altura=altura, gatilho="load" if nome in ("aging", "zumbi") else "revealed")
        corpo.divider()


def _tabela_e_barras(p: Node, df: pd.DataFrame, indice: str, valor: str, top: Optional[int] = None) -> None:
    card = p.card()
    a, b = card.columns(2)
    a.table(df, {valor: "brl0"})
    if not df.empty:
        serie = df.set_index(indice)[valor]
        serie = serie.head(top) if top else serie
        b.bar_chart(serie.rename("Valor pendente (R$)"), horizontal=True, fmt_valor="brl0", altura=max(220, 30 * len(serie) + 40))


def _bloco_aging(p: Node, ctx: Ctx) -> None:
    df = _aging(_tipo(ctx), config("excluir_intercompany"))
    _tabela_e_barras(p, df, "Faixa_Aging", "Valor_Pendente_Total")
    p.caption(
        'O balde "60+ dias" junta de 61 dias até pedido de mais de 1 década — ver "Radar de pedido zumbi" abaixo '
        "pra abrir esse balde. `Valor_Pendente_Total` soma só pedidos em BRL."
    )


def _bloco_zumbi(p: Node, ctx: Ctx) -> None:
    p.caption(
        "Cruza o backlog aberto (`fct_pendencia_sap`, `Qtd_Pendente_Remessa > 0`) com o estoque reservado no SAP "
        "(`Qtd_Estoque_Reservada`, VBBE) por Material+Centro, em toda a base — backlog muito maior que a reserva "
        "viva é sinal de pedido antigo nunca baixado/cancelado no SAP (mesmo padrão achado no PA5522). O tipo de "
        "cliente não se aplica aqui."
    )
    bruto = _backlog(config("excluir_intercompany"))
    if bruto.empty:
        p.info("Nenhum backlog aberto encontrado.")
        return
    a, _ = p.columns([1, 1])
    limiar = a.number_input(
        'Considerar backlog "recente" até quantos dias (o resto entra como possível pedido zumbi)',
        "pedidos_limiar_zumbi", 30, 3650, int(app_db.get_setting("limiar_zumbi_dias")), 30,
    )
    backlog = bruto[bruto["Qtd_Pendente_Remessa"] > 0].copy()
    backlog["Backlog_Antigo"] = backlog["Dias_Desde_Inclusao_Pedido"] > limiar
    # Valor_Pendente_Faturamento vem na moeda do pedido — só BRL entra nas somas em R$.
    brl = backlog[backlog["Moeda"] == "BRL"].copy()
    outras = backlog[backlog["Moeda"] != "BRL"].groupby("Moeda")["Valor_Pendente_Faturamento"].sum().sort_values(ascending=False)
    total = brl["Valor_Pendente_Faturamento"].sum()
    antigo = brl.loc[brl["Backlog_Antigo"], "Valor_Pendente_Faturamento"].sum()
    p.card().metrics(
        [
            ("Backlog aberto total (R$)", fmt.brl(total)),
            (f"Possível zumbi, > {fmt.num(limiar)} dias (R$)", fmt.brl(antigo), f"{fmt.pct(antigo / total if total else 0, 0)} do total"),
            ("Qtd possível zumbi (unid.)", fmt.num(backlog.loc[backlog["Backlog_Antigo"], "Qtd_Pendente_Remessa"].sum())),
            ("Materiais afetados", fmt.num(backlog.loc[backlog["Backlog_Antigo"], "Codigo_Produto"].nunique())),
        ]
    )
    p.caption(
        "Valores em R$ somam só pedidos `Moeda='BRL'` (backlog em outra moeda não entra, pra não misturar moeda). "
        "`Qtd`/`Materiais afetados` somam todas as moedas."
    )
    if not outras.empty:
        p.expander(f"Backlog aberto em outras moedas ({len(outras)}) — não somado acima").table(
            outras.rename("Valor_Pendente_Faturamento (moeda própria)").to_frame(), {"Valor_Pendente_Faturamento (moeda própria)": "num2"}, indice=True
        )
    faixas = ["0-90 dias", "91-365 dias", "1-3 anos", "3+ anos"]
    brl["Faixa_Idade"] = pd.cut(brl["Dias_Desde_Inclusao_Pedido"], bins=[-1, 90, 365, 1095, float("inf")], labels=faixas)
    p.card().bar_chart(
        brl.groupby("Faixa_Idade", observed=True)["Valor_Pendente_Faturamento"].sum().reindex(faixas).rename("Valor pendente (R$)"),
        fmt_valor="brl0", altura=260,
    )
    p.markdown("**Ranking de materiais por valor de backlog antigo (candidato a limpeza)**")
    antigo_brl = backlog["Backlog_Antigo"] & (backlog["Moeda"] == "BRL")
    ranking = (
        backlog.assign(
            _qtd_recente=backlog["Qtd_Pendente_Remessa"].where(~backlog["Backlog_Antigo"], 0),
            _qtd_antigo=backlog["Qtd_Pendente_Remessa"].where(backlog["Backlog_Antigo"], 0),
            _valor_antigo=backlog["Valor_Pendente_Faturamento"].where(antigo_brl, 0),
        )
        .groupby(["Codigo_Produto", "Descricao_Produto", "Codigo_Centro", "Nome_Centro"])
        .agg(
            Itens_Total=("Numero_Pedido", "size"),
            Qtd_Recente=("_qtd_recente", "sum"),
            Qtd_Antigo=("_qtd_antigo", "sum"),
            Valor_Antigo=("_valor_antigo", "sum"),
            Dias_Max=("Dias_Desde_Inclusao_Pedido", "max"),
        )
        .reset_index()
    )
    ranking = ranking[ranking["Valor_Antigo"] > 0].merge(_reservado(), on=["Codigo_Produto", "Codigo_Centro"], how="left")
    ranking["Qtd_Reservada"] = ranking["Qtd_Reservada"].fillna(0)
    ranking = ranking.sort_values("Valor_Antigo", ascending=False)
    b, _ = p.columns([1, 2])
    n = b.slider("Quantos materiais mostrar", "pedidos_zumbi_top_n", 5, 100, 20, 5)
    p.card().table(
        ranking.head(n),
        {"Qtd_Recente": "num0", "Qtd_Antigo": "num0", "Valor_Antigo": "brl2", "Qtd_Reservada": "num0", "Dias_Max": "num0", "Itens_Total": "num0"},
        nome_arquivo="ranking_backlog_antigo",
    )
    p.caption(
        "`Qtd_Reservada` bem menor que `Qtd_Antigo` reforça a suspeita de pedido zumbi. Não tratar como sinal de "
        "compra/produção sem confirmar com vendas/SAP — pra 1 material, ver **Pendência x Estoque** (filtre por "
        "Material e clique num pedido)."
    )


def _bloco_cobertura(p: Node, ctx: Ctx) -> None:
    p.caption(
        "`Status_Pendencia_Estoque` compara a quantidade pendente com `Qtd_Estoque_Disponivel_Venda` de "
        "`fct_pendencia_sap` — pode subestimar o estoque real de um material específico; pra decidir se dá pra "
        "faturar 1 material, use **Pendência x Estoque**. `Valor_Pendente_Total` soma só pedidos em BRL."
    )
    _tabela_e_barras(p, _cobertura(_tipo(ctx), config("excluir_intercompany")), "Status_Pendencia_Estoque", "Valor_Pendente_Total")


def _bloco_top_clientes(p: Node, ctx: Ctx) -> None:
    p.caption("`Valor_Pendente_Total` soma só pedidos em BRL, pra não misturar moeda.")
    a, _ = p.columns([1, 2])
    n = a.slider("Quantos clientes mostrar", "pedidos_top_n", 5, 50, 20, 5)
    p.card().table(_top_clientes(n, _tipo(ctx), config("excluir_intercompany")), {"Valor_Pendente_Total": "brl2"}, nome_arquivo="top_clientes_pendentes")


def _bloco_tipo_ordem(p: Node, ctx: Ctx) -> None:
    p.caption(
        "Tipo_Ordem_Venda é o código SAP (AUART) do pedido — sem tradução pra texto nesta base. "
        "`Valor_Pendente_Total` soma só pedidos em BRL."
    )
    _tabela_e_barras(p, _tipo_ordem(_tipo(ctx), config("excluir_intercompany")), "Tipo_Ordem_Venda", "Valor_Pendente_Total", top=15)


def _janela_meses(p: Node) -> int:
    a, _ = p.columns([1, 2])
    return a.slider("Janela (meses)", "pedidos_meses", 6, 36, 12)


def _bloco_mensal(p: Node, ctx: Ctx) -> None:
    p.caption(
        "Fonte: `fct_vendas_itens_sap.Data_Inclusao_Pedido` — pedido novo entrando (não é backlog). Valores somam "
        "só pedidos `Moeda='BRL'`; `Pedidos no período` conta todas as moedas."
    )
    meses = _janela_meses(p)
    df = _mensal(meses, config("excluir_intercompany"))
    if df.empty:
        p.info("Sem pedidos no período.")
        return
    df = df.assign(Valor_Medio_Pedido=df["Valor_Pedido"] / df["Qtd_Pedidos_BRL"].replace(0, pd.NA))
    p.metrics(
        [
            ("Pedidos no período", fmt.num(df["Qtd_Pedidos"].sum())),
            ("Valor total pedido", fmt.brl(df["Valor_Pedido"].sum())),
            ("Valor médio de pedido (média mensal)", fmt.brl(df["Valor_Medio_Pedido"].astype(float).mean())),
        ]
    )
    card = p.card()
    a, b = card.columns(2)
    a.subheader("Quantidade mensal de pedidos")
    a.bar_chart(df.set_index("Mes")["Qtd_Pedidos"].rename("Pedidos"))
    b.subheader("Valor médio de pedido — mensal")
    b.bar_chart(df.set_index("Mes")["Valor_Medio_Pedido"].astype(float).rename("Valor médio (R$)"), fmt_valor="brl0")


def _bloco_ranking_clientes(p: Node, ctx: Ctx) -> None:
    p.caption("Restrito a pedidos `Moeda='BRL'` — cliente com pedido só em moeda estrangeira no período não aparece aqui.")
    meses = int(ctx.get("pedidos_meses") or 12)
    meses = max(6, min(36, meses))
    hoje = datetime.date.today()
    inicio = (hoje.replace(day=1) - pd.DateOffset(months=meses - 1)).date()
    a, _ = p.columns([1, 2])
    n = a.slider("Quantos clientes mostrar", "pedidos_ranking_n", 5, 50, 20, 5)
    p.caption(f"Janela: últimos {meses} meses (o controle \"Janela (meses)\" da seção acima).")
    df = _ranking_clientes(inicio, hoje, n, _tipo(ctx), config("excluir_intercompany"))
    if df.empty:
        p.info("Nada encontrado para esse período/filtro.")
        return
    p.card().table(
        df, {"Qtd_Pedidos": "num0", "Qtd_Itens_Total": "num0", "Media_Itens_Pedido": "num2", "Valor_Medio_Pedido": "brl2"},
        nome_arquivo="ranking_pedidos_cliente",
    )


def _buscar(p: Node, ctx: Ctx) -> None:
    p.caption("SAP cru (HANA) → Gold `vendas_sap` → Salesforce (Opportunity/OpportunityLineItem), lado a lado.")
    a, b, c = p.columns([2, 1, 1])
    numero = a.text_input("Número do pedido", "pedidos_busca_numero", placeholder="ex.: 137490").strip()
    item = b.text_input("Item (opcional)", "pedidos_busca_item", placeholder="ex.: 10").strip()
    c.html(Markup('<div class="campo-rotulo">&nbsp;</div>'))
    buscar = c.button("Rastrear", "pedidos_rastrear", primario=True, desabilitado=not numero, icone_nome="search")
    if not (buscar and numero):
        p.caption('Digite um número de pedido (com ou sem zeros à esquerda) e clique em "Rastrear".')
        p.caption("Pra funil/conversão Oportunidade → Pedido em nível de portfólio, veja a página **Oportunidade**.")
        return
    for titulo, df in _trace(numero, item or None).items():
        n = len(df) if isinstance(df, pd.DataFrame) else 0
        corpo = p.expander(f"{titulo} ({n} linha{'s' if n != 1 else ''})", aberto=n > 0)
        if isinstance(df, pd.DataFrame) and not df.empty:
            corpo.table(df, nome_arquivo=f"pedido_{numero}")
        else:
            corpo.info("Nada encontrado.")


BLOCOS = {
    "aging": _bloco_aging,
    "zumbi": _bloco_zumbi,
    "cobertura": _bloco_cobertura,
    "top_clientes": _bloco_top_clientes,
    "tipo_ordem": _bloco_tipo_ordem,
    "mensal": _bloco_mensal,
    "ranking_clientes": _bloco_ranking_clientes,
}

AQUECER = [lambda: _backlog(config("excluir_intercompany")), _reservado]
