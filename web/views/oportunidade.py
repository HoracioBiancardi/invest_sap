"""Oportunidade — funil Salesforce: estágio, ganha x não ganha, aging de oportunidade aberta e
divergência de valor vs. pedido SAP (porte de `pages/19_Oportunidade.py`). Cobertura de
Oportunidade ~73% (2026-08-13): pedido sem match não entra no funil, mas conta na conversão."""

from __future__ import annotations

import datetime
from typing import Optional

import pandas as pd

from scripts.query_vendas_sap import correlacao_oportunidade_pedido_pendencia_fatura
from web import fmt
from web.cache import cached
from web.ui import Ctx, Node, Pagina
from web.views._comum import config, filtro_periodo_tipo_cliente, filtros_executivo, nota_config, tipo_cliente_param

LIMITE = 20000


@cached(ttl=300, nome="oportunidade: correlação opp→pedido→fatura")
def _dados(inicio: datetime.date, fim: datetime.date, pedido: Optional[str], tipo_cliente: Optional[str], excl_interco: bool) -> pd.DataFrame:
    return correlacao_oportunidade_pedido_pendencia_fatura(
        data_inicio=inicio, data_fim=fim, apenas_pendentes=False, numero_pedido=pedido,
        tipo_cliente=tipo_cliente, limit=LIMITE, excluir_intercompany=excl_interco,
    )


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Oportunidade", "target")
    p.caption(
        "Funil da Oportunidade (Salesforce): estágio, ganha x não ganha, aging de oportunidade aberta e "
        "divergência de valor vs. o pedido SAP. Cobertura não é 100% (~73%, medido em 2026-08-13) — pedido sem "
        "match não entra nas métricas de funil (só linhas COM Oportunidade), mas conta no denominador de 'conversão'."
    )
    inicio, fim, tipo_opcao = filtro_periodo_tipo_cliente(p)
    p.caption(f"Filtro: período de **{fmt.data(inicio)}** a **{fmt.data(fim)}**, tipo de cliente **{tipo_opcao}**." + nota_config(intercompany=True))
    filtros = filtros_executivo(p, "oportunidade", mostrar_pedido=True)
    df = _dados(inicio, fim, filtros.get("numero_pedido"), tipo_cliente_param(tipo_opcao), config("excluir_intercompany"))
    if df.empty:
        p.info("Nada encontrado para esse filtro.")
        return
    if len(df) >= LIMITE:
        p.warning(f"Atingiu o teto de segurança de {fmt.num(LIMITE)} linhas — reduza o período pra garantir totais exatos.")
    opp = df[df["Nome_Oportunidade"].notna()].copy()
    dedup = opp.drop_duplicates(subset=["Nome_Oportunidade", "Data_Criacao_Oportunidade"])
    # Salesforce também é multi-moeda — funil/ganha/aging restritos a BRL.
    if not dedup.empty and (dedup["Moeda_Oportunidade"] != "BRL").any():
        p.caption("Valor de Oportunidade (deduplicado), por moeda:")
        p.valor_por_moeda(dedup, "Valor_Oportunidade", moeda_col="Moeda_Oportunidade")
    dedup_brl = dedup[dedup["Moeda_Oportunidade"] == "BRL"]
    p.metrics(
        [
            ("Pedidos+Item no período", fmt.num(len(df))),
            ("% com Oportunidade vinculada", fmt.pct(len(opp) / len(df), 0)),
            ("Oportunidades distintas (aprox.)", fmt.num(len(dedup))),
            ("Oportunidades ganhas", fmt.pct(dedup["Oportunidade_Ganha"].mean(), 0) if not dedup.empty else "—"),
        ]
    )
    p.divider()
    if opp.empty:
        p.info("Nenhuma linha com Oportunidade vinculada nesse filtro — sem funil pra mostrar.")
        return
    abas = p.tabs(["Funil por Estágio", "Ganha x Não Ganha", "Aging de Oportunidade Aberta", "Divergência Oportunidade x Pedido", "Detalhe"], "aba")
    [_funil, _ganha, _aging, _divergencia, _detalhe][abas.ativa](abas.corpo, opp, dedup_brl)


def _funil(p: Node, opp: pd.DataFrame, dedup_brl: pd.DataFrame) -> None:
    p.caption(
        "`Estagio_Oportunidade` é o `stage_name` cru do Salesforce — mistura estágios de aprovação de "
        "licitação/governo (Em Aprovação, Aprovado, Reprovado, Recusado 1º/2º nível...) com o funil de venda "
        "privada (Ganha/Perdida): são 2 fluxos no mesmo campo. `% Em Aberto` = fração do estágio ainda sem "
        "`Data_Fechamento_Oportunidade` — é o jeito de achar aprovação parada/represada."
    )
    funil = (
        dedup_brl.assign(Em_Aberto=dedup_brl["Data_Fechamento_Oportunidade"].isna())
        .groupby("Estagio_Oportunidade", dropna=False)
        .agg(Qtd_Oportunidades=("Nome_Oportunidade", "count"), Valor_Oportunidade=("Valor_Oportunidade", "sum"), Pct_Em_Aberto=("Em_Aberto", "mean"))
        .sort_values("Qtd_Oportunidades", ascending=False)
    )
    card = p.card()
    card.bar_chart(funil["Qtd_Oportunidades"].rename("Oportunidades"), horizontal=True, altura=max(240, 26 * len(funil) + 40))
    card.table(funil, {"Valor_Oportunidade": "brl0", "Pct_Em_Aberto": "pct0", "Qtd_Oportunidades": "num0"}, indice=True, nome_arquivo="funil_estagio")
    criacao = pd.to_datetime(opp["Data_Criacao_Oportunidade"], utc=True, errors="coerce").dt.tz_localize(None)
    pedido = pd.to_datetime(opp["Data_Inclusao_Pedido"], errors="coerce")
    fatura = pd.to_datetime(opp["Primeira_Data_Faturamento"], errors="coerce")
    opp_pedido = (pedido - criacao).dt.days
    opp_pedido = opp_pedido[opp_pedido >= 0]
    pedido_fatura = (fatura - pedido).dt.days
    pedido_fatura = pedido_fatura[pedido_fatura >= 0]
    p.metrics(
        [
            ("Mediana dias: Oportunidade → Pedido", fmt.num(opp_pedido.median()) if not opp_pedido.empty else "sem dado"),
            ("Mediana dias: Pedido → 1ª Fatura", fmt.num(pedido_fatura.median()) if not pedido_fatura.empty else "sem dado"),
        ]
    )


def _ganha(p: Node, opp: pd.DataFrame, dedup_brl: pd.DataFrame) -> None:
    resumo = dedup_brl.groupby("Oportunidade_Ganha", dropna=False).agg(
        Qtd_Oportunidades=("Nome_Oportunidade", "count"), Valor_Oportunidade=("Valor_Oportunidade", "sum")
    )
    resumo.index = resumo.index.map({True: "Ganha", False: "Não ganha / em aberto"})
    card = p.card()
    a, b = card.columns(2)
    a.bar_chart(resumo["Valor_Oportunidade"].rename("Valor (R$)"), fmt_valor="brl0")
    b.table(resumo, {"Valor_Oportunidade": "brl2", "Qtd_Oportunidades": "num0"}, indice=True)


def _aging(p: Node, opp: pd.DataFrame, dedup_brl: pd.DataFrame) -> None:
    p.caption("Oportunidade sem `Data_Fechamento_Oportunidade` — ainda em aberto. Dias contados de `Data_Criacao_Oportunidade`.")
    abertas = dedup_brl[dedup_brl["Data_Fechamento_Oportunidade"].isna()].copy()
    if abertas.empty:
        p.info("Nenhuma Oportunidade em aberto nesse filtro.")
        return
    abertas["Dias_Aberta"] = (
        pd.Timestamp.today().normalize()
        - pd.to_datetime(abertas["Data_Criacao_Oportunidade"], utc=True, errors="coerce").dt.tz_localize(None)
    ).dt.days
    faixas = ["0-15 dias", "16-30 dias", "31-60 dias", "61-90 dias", "90+ dias"]
    abertas["Faixa_Aging"] = pd.cut(abertas["Dias_Aberta"], bins=[-1, 15, 30, 60, 90, 10**6], labels=faixas)
    pivot = abertas.groupby("Faixa_Aging", observed=True)["Valor_Oportunidade"].sum().reindex(faixas)
    card = p.card()
    a, b = card.columns(2)
    a.bar_chart(pivot.rename("Valor (R$)"), fmt_valor="brl0")
    b.table(pivot.to_frame(), {"Valor_Oportunidade": "brl2"}, indice=True)


def _divergencia(p: Node, opp: pd.DataFrame, dedup_brl: pd.DataFrame) -> None:
    p.caption(
        "Compara `Valor_Item_Oportunidade` (Salesforce `OpportunityLineItem.TotalPrice`, valor negociado do item) "
        "com `Valor_Liquido_Pedido` (SAP, o que virou pedido) pro mesmo Pedido+Item — não confundir com "
        "`Valor_Oportunidade`, total do negócio inteiro."
    )
    div = opp.loc[opp["Valor_Item_Oportunidade"].notna() & (opp["Valor_Liquido_Pedido"] != 0)].copy()
    if div.empty:
        p.info("Nenhuma linha com Oportunidade e Pedido pra comparar nesse filtro.")
        return
    div["Diferenca"] = div["Valor_Item_Oportunidade"] - div["Valor_Liquido_Pedido"]
    div["Diferenca_Pct"] = div["Diferenca"] / div["Valor_Liquido_Pedido"] * 100
    a, _ = p.columns([1, 2])
    limite = a.slider("Mostrar divergências acima de (%)", "opp_div_pct", 0, 100, 5, 5)
    filtrado = div[div["Diferenca_Pct"].abs() >= limite].sort_values("Diferenca_Pct", key=lambda s: s.abs(), ascending=False)
    p.metric(f"Linhas com divergência ≥ {limite}%", f"{fmt.num(len(filtrado))} de {fmt.num(len(div))}")
    colunas = ["Numero_Pedido", "Item_Pedido", "Nome_Cliente", "Nome_Oportunidade", "Moeda", "Valor_Item_Oportunidade",
               "Valor_Liquido_Pedido", "Diferenca", "Diferenca_Pct"]
    card = p.card()
    card.caption("`Moeda` do pedido SAP ao lado — o % vale linha a linha mesmo fora de BRL, mas os valores absolutos não levam \"R$\" fixo.")
    card.table(
        filtrado[colunas].head(500),
        {"Valor_Item_Oportunidade": "num2", "Valor_Liquido_Pedido": "num2", "Diferenca": "num2", "Diferenca_Pct": "pctv1"},
        nome_arquivo="divergencia_opp_pedido",
    )


def _detalhe(p: Node, opp: pd.DataFrame, dedup_brl: pd.DataFrame) -> None:
    colunas = [
        "Numero_Pedido", "Item_Pedido", "Nome_Cliente", "Nome_Oportunidade", "Estagio_Oportunidade", "Oportunidade_Ganha",
        "Moeda_Oportunidade", "Valor_Oportunidade", "Valor_Item_Oportunidade", "Moeda", "Valor_Liquido_Pedido",
        "Status_Pendencia", "Status_Faturamento", "Data_Criacao_Oportunidade", "Data_Fechamento_Oportunidade",
    ]
    p.card().table(opp[colunas], {"Valor_Oportunidade": "num2", "Valor_Item_Oportunidade": "num2", "Valor_Liquido_Pedido": "num2"},
                   nome_arquivo="oportunidades_detalhe")
