"""Home — visão executiva (porte de `pages/0_Home.py`).

Duas seções que não se misturam: **Backlog e Operação** (`vendas_sap`, total bruto) e
**Faturamento Comercial** (`vendas_sap` via crosswalk cliente→setor, ~52% de cobertura — ver
`docs/CONTEXTO_VENDAS_SAP.md` §10). Mesma tabela fonte, consulta diferente: os totais não
somam entre as duas.

Diferença do Streamlit: cada seção é um bloco `lazy()` com requisição própria — a página abre
na hora e as seções chegam em paralelo (antes rodavam em sequência numa execução só). As
consultas são pré-aquecidas (`warm=True`), então normalmente nem esperam o DW.
"""

from __future__ import annotations

import datetime

import pandas as pd
from markupsafe import Markup, escape

from scripts.query_faturamento_comercial import (
    faturamento_por_dimensao,
    faturamento_serie,
    meta_vs_realizado_por_dimensao,
)
from scripts.query_vendas_sap import (
    aging_pendencias,
    credito_disponivel_clientes,
    devolucoes_credito_motivo,
    estoque_totais,
    estoque_validade_resumo,
    faturamento_mensal,
    pendencia_status_estoque,
)
from web import fmt
from web.cache import cached, em_paralelo
from web.ui import Ctx, Node, Pagina, icone
from web.views import SECOES, menu
from web.views._comum import config, para_brl


@cached(ttl=300, warm=True, nome="home: backlog e operação")
def _dados_executivos(excluir_internacional: bool, excluir_intercompany: bool, hoje: datetime.date) -> dict[str, pd.DataFrame]:
    return em_paralelo(
        aging=lambda: aging_pendencias(excluir_intercompany=excluir_intercompany),
        estoque_status=lambda: pendencia_status_estoque(excluir_intercompany=excluir_intercompany),
        estoque_totais=lambda: estoque_totais(excluir_paises_internacionais=excluir_internacional),
        estoque_validade=lambda: estoque_validade_resumo(excluir_paises_internacionais=excluir_internacional),
        credito_bloqueado=lambda: credito_disponivel_clientes(apenas_bloqueados=True, excluir_intercompany=excluir_intercompany),
        devolucoes=lambda: devolucoes_credito_motivo(
            data_inicio=hoje - datetime.timedelta(days=30), data_fim=hoje, limit=5000,
            excluir_intercompany=excluir_intercompany,
        ),
        faturamento_mensal=lambda: faturamento_mensal(meses=12, excluir_intercompany=excluir_intercompany),
    )


@cached(ttl=300, warm=True, nome="home: faturamento comercial")
def _dados_comerciais(hoje: datetime.date) -> dict[str, pd.DataFrame]:
    inicio_ano = hoje.replace(month=1, day=1)
    return em_paralelo(
        serie_mes=lambda: faturamento_serie(inicio_ano, hoje, granularidade="mes"),
        meta_canal=lambda: meta_vs_realizado_por_dimensao(inicio_ano, hoje, "Canal"),
        canal_ytd=lambda: faturamento_por_dimensao(inicio_ano, hoje, "Canal"),
    )


def _executivos() -> dict[str, pd.DataFrame]:
    return _dados_executivos(config("excluir_estoque_internacional"), config("excluir_intercompany"), datetime.date.today())


def _indicadores_operacao() -> dict[str, float]:
    """Números derivados de Backlog e Operação (usados nos KPIs e nos pontos de atenção)."""
    d = _executivos()
    aging, status, totais = d["aging"], d["estoque_status"], d["estoque_totais"]
    validade, credito, devol, fat = d["estoque_validade"], d["credito_bloqueado"], d["devolucoes"], d["faturamento_mensal"]
    r: dict[str, float] = {}
    r["pendente_total"] = aging["Valor_Pendente_Total"].sum() if not aging.empty else 0.0
    r["pendente_60mais"] = aging.loc[aging["Faixa_Aging"] == "60+ dias", "Valor_Pendente_Total"].sum() if not aging.empty else 0.0
    r["pct_60mais"] = r["pendente_60mais"] / r["pendente_total"] if r["pendente_total"] else 0.0
    r["sem_estoque"] = (
        status.loc[status["Status_Pendencia_Estoque"] == "Pendente sem Estoque", "Valor_Pendente_Total"].sum()
        if not status.empty else 0.0
    )
    r["pct_sem_estoque"] = r["sem_estoque"] / r["pendente_total"] if r["pendente_total"] else 0.0
    mes = fat["Mes"].max() if not fat.empty else None
    fat_mes = fat[fat["Mes"] == mes] if mes is not None else fat
    r["faturado_mes"] = para_brl(fat_mes, "Valor_Faturado", "Mes").sum() if not fat_mes.empty else 0.0
    primeira = totais.iloc[0] if not totais.empty else None
    r["estoque_valor"] = primeira["Valor_Financeiro_Estoque"] if primeira is not None else 0.0
    r["qtd_restrito"] = primeira["Qtd_Restrito"] if primeira is not None else 0.0
    r["qtd_fisico"] = primeira["Qtd_Fisico_Total"] if primeira is not None else 0.0
    r["pct_restrito"] = r["qtd_restrito"] / r["qtd_fisico"] if r["qtd_fisico"] else 0.0
    r["clientes_bloqueados"] = float(len(credito))
    r["exposicao_bloqueada"] = credito["Valor_Exposicao_Total_SAP"].sum() if not credito.empty else 0.0
    r["devolucoes_30d"] = devol["Montante"].sum() if not devol.empty else 0.0
    r["vencido"] = (
        validade.loc[validade["Faixa_Validade"] == "Vencido", "Valor_Financeiro_Estoque"].sum() if not validade.empty else 0.0
    )
    r["pct_vencido"] = r["vencido"] / r["estoque_valor"] if r["estoque_valor"] else 0.0
    return r


def _indicadores_comerciais() -> tuple[dict[str, float | None], pd.DataFrame]:
    hoje = datetime.date.today()
    inicio_ano = hoje.replace(month=1, day=1)
    d = _dados_comerciais(hoje)
    serie, meta, canal = d["serie_mes"], d["meta_canal"], d["canal_ytd"]
    # Achado 2026-09-04: 1 linha por Mes/Dimensao+Moeda — converter pra BRL antes de somar,
    # senão um Canal com faturamento em 2 moedas aparece repetido.
    if not serie.empty:
        serie = serie.assign(Valor_BRL=para_brl(serie, "Valor_Faturado", "Mes"))
    if not canal.empty:
        canal = canal.assign(_Data_Ref=inicio_ano + (hoje - inicio_ano) / 2)
        canal = canal.assign(Valor_BRL=para_brl(canal, "Valor_Faturado", "_Data_Ref")).groupby("Dimensao", as_index=False)["Valor_BRL"].sum()
    mes_atual = hoje.strftime("%Y-%m")
    r: dict[str, float | None] = {}
    r["mtd"] = serie.loc[serie["Mes"] == mes_atual, "Valor_BRL"].sum() if not serie.empty else 0.0
    r["ytd"] = serie["Valor_BRL"].sum() if not serie.empty else 0.0
    r["meta_mtd"] = meta.loc[meta["Mes"] == mes_atual, "Meta_Valor"].sum() if not meta.empty else 0.0
    r["meta_ytd"] = meta["Meta_Valor"].sum() if not meta.empty else 0.0
    r["cob_mtd"] = r["mtd"] / r["meta_mtd"] if r["meta_mtd"] else None
    r["cob_ytd"] = r["ytd"] / r["meta_ytd"] if r["meta_ytd"] else None
    return r, canal


# ── blocos ───────────────────────────────────────────────────────────────────


def _bloco_operacao(p: Node, ctx: Ctx) -> None:
    r = _indicadores_operacao()
    card = p.card()
    card.metrics(
        [
            ("Valor Pendente (Backlog)", fmt.brl(r["pendente_total"])),
            ("Faturado (mês corrente)", fmt.brl(r["faturado_mes"])),
            ("Valor em Estoque", fmt.brl(r["estoque_valor"])),
            ("Backlog com 60+ dias", fmt.pct(r["pct_60mais"], 0), None, "Fatia do valor pendente total que está aberta há mais de 60 dias."),
        ]
    )
    fat = _executivos()["faturamento_mensal"]
    mes = fat["Mes"].max() if not fat.empty else None
    fat_mes = fat[fat["Mes"] == mes] if mes is not None else fat
    if not fat_mes.empty and (fat_mes["Moeda"] != "BRL").any():
        card.expander("Faturado do mês corrente, por moeda (sem conversão)").valor_por_moeda(fat_mes, "Valor_Faturado")


def _bloco_evolucao(p: Node, ctx: Ctx) -> None:
    fat = _executivos()["faturamento_mensal"]
    if fat.empty:
        p.info("Sem dado de faturamento no período.")
        return
    card = p.card()
    serie = fat.assign(Valor_BRL=para_brl(fat, "Valor_Faturado", "Mes")).groupby("Mes")["Valor_BRL"].sum()
    card.bar_chart(serie.rename("Faturado (R$)"), fmt_valor="brl0")
    if (fat["Moeda"] != "BRL").any():
        pivot = fat.pivot_table(index="Mes", columns="Moeda", values="Valor_Faturado", aggfunc="sum", fill_value=0)
        card.expander("Ver por moeda, sem conversão").bar_chart(pivot)


def _bloco_comercial(p: Node, ctx: Ctx) -> None:
    r, canal = _indicadores_comerciais()
    ajuda_cob = (
        "Pode passar de 100% com folga: o numerador é o faturamento total (todas Org Vendas, "
        "incl. filial estrangeira/intercompany), a meta só cobre a fatia com crosswalk "
        "cliente→setor mapeado (~52%) — não é atingimento real de meta comercial."
    )
    p.card().metrics(
        [
            ("Faturado MTD (comercial)", fmt.brl(r["mtd"])),
            ("Faturado YTD (comercial)", fmt.brl(r["ytd"])),
            ("Cob. Meta MTD", fmt.pct(r["cob_mtd"], 1) if r["cob_mtd"] is not None else "—", None, ajuda_cob),
            ("Cob. Meta YTD", fmt.pct(r["cob_ytd"], 1) if r["cob_ytd"] is not None else "—", None,
             "Mesma ressalva do Cob. Meta MTD. Canal 'MS' também não tem meta própria — ver **Painel Vendas**."),
        ]
    )
    if not canal.empty:
        a, b = p.card().columns([1, 2])
        a.caption("Faturado YTD por Canal (convertido p/ BRL)")
        a.table(canal.rename(columns={"Dimensao": "Canal", "Valor_BRL": "Faturado YTD"}), {"Faturado YTD": "brl0"})
        b.bar_chart(canal.set_index("Dimensao")["Valor_BRL"].rename("Faturado YTD"), fmt_valor="brl0")


def _bloco_atencao(p: Node, ctx: Ctx) -> None:
    r = _indicadores_operacao()
    if r["pendente_total"] > 0:
        if r["pct_60mais"] >= 0.5:
            p.warning(
                f"**{fmt.pct(r['pct_60mais'], 0)} do valor pendente ({fmt.brl(r['pendente_60mais'])} de "
                f"{fmt.brl(r['pendente_total'])}) está em backlog há mais de 60 dias.** Aging concentrado "
                "assim geralmente indica pedidos travados, não giro normal — ver **Pedidos** e "
                "**Pendência x Estoque** para detalhar."
            )
        else:
            p.info(f"{fmt.pct(r['pct_60mais'], 0)} do valor pendente está em backlog há mais de 60 dias.")
        if r["pct_sem_estoque"] >= 0.3:
            p.warning(
                f"**{fmt.pct(r['pct_sem_estoque'], 0)} do valor pendente ({fmt.brl(r['sem_estoque'])}) está sem "
                "cobertura de estoque** — não é gargalo logístico/faturamento, é falta de produto pra "
                "atender. Ver **Estoque** para saber quais materiais faltam."
            )
        else:
            p.info(f"{fmt.pct(r['pct_sem_estoque'], 0)} do valor pendente está sem cobertura de estoque.")
    if r["clientes_bloqueados"] > 0:
        p.info(
            f"**{fmt.num(r['clientes_bloqueados'])} clientes bloqueados por crédito**, "
            f"{fmt.brl(r['exposicao_bloqueada'])} de exposição total — ver **Crédito e Devoluções** para "
            "saber quanto disso está represando pedidos no backlog."
        )
    if r["devolucoes_30d"] > 0:
        p.info(
            f"**{fmt.brl(r['devolucoes_30d'])} em devoluções/abatimentos de negócio** nos últimos 30 dias "
            "(excluindo transferência de faturamento de rotina) — ver **Crédito e Devoluções** para os motivos."
        )
    if r["qtd_fisico"] > 0:
        p.info(
            f'**{fmt.pct(r["pct_restrito"], 0)}** da quantidade física em estoque está "restrita" — soma de '
            "**em qualidade** (lote aguardando laudo/liberação, etapa normal) + **bloqueado** (travado "
            'manualmente, ex.: suspeita de desvio, quarentena). Ver aba "Restrito x Disponível" em **Estoque**.'
        )
    if r["vencido"] > 0:
        if r["pct_vencido"] >= 0.1:
            p.warning(
                f"**{fmt.pct(r['pct_vencido'], 0)} do valor em estoque ({fmt.brl(r['vencido'])}) já está vencido** "
                "— ver aba Validade dos lotes em **Estoque** para saber quais materiais/lotes."
            )
        else:
            p.info(f"{fmt.brl(r['vencido'])} em estoque vencido ({fmt.pct(r['pct_vencido'], 0)} do valor total).")
    com, _ = _indicadores_comerciais()
    if com["cob_ytd"] is not None:
        if com["cob_ytd"] < 0.8:
            p.warning(
                f"**Cob. Meta YTD comercial em {fmt.pct(com['cob_ytd'], 0)}** ({fmt.brl(com['ytd'])} de "
                f"{fmt.brl(com['meta_ytd'])}) — ver **Painel Vendas** pra detalhar por Divisional/Regional/"
                "Distrital/Setor onde está o maior desvio."
            )
        else:
            p.info(
                f"Cob. Meta YTD comercial em {fmt.pct(com['cob_ytd'], 0)} — normal passar de 100% com folga aqui "
                "(o numerador não tem o mesmo recorte da meta)."
            )


BLOCOS = {
    "operacao": _bloco_operacao,
    "evolucao": _bloco_evolucao,
    "comercial": _bloco_comercial,
    "atencao": _bloco_atencao,
}


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Visão Executiva", "dashboard")
    p.caption(
        "Resumo geral pra decisão rápida — números ao vivo de `GOLD.vendas_sap` nas 2 seções abaixo, "
        "com escopo diferente: **Backlog e Operação** soma tudo sem recorte comercial; **Faturamento "
        "Comercial** passa pelo crosswalk cliente→setor (~52% de cobertura) pra quebrar por "
        "Divisional/Regional/Distrital/Setor/Canal — ver `docs/CONTEXTO_VENDAS_SAP.md` §10. **As duas "
        "seções não são comparáveis 1:1.**"
    )
    nota = (
        '"Valor em Estoque" já filtra só centro em R$ (Brasil) — Uruguai/Colômbia usam moeda local '
        "e ficam de fora dessa soma."
    )
    if config("excluir_estoque_internacional"):
        nota += " Config do Admin também tira as **quantidades** de Uruguai/Colômbia dos totais."
    if config("excluir_intercompany"):
        nota += ' Cliente intercompany (filial "BLAU*") excluído por config do Admin nas duas seções.'
    p.caption(nota)

    p.subheader("Backlog e Operação (`vendas_sap`)")
    p.caption("Situação do backlog aberto hoje.")
    p.lazy("operacao", altura=110)
    p.divider()
    p.subheader("Evolução do faturamento (últimos 12 meses)")
    p.caption(
        "`vendas_sap.fct_faturamento_itens_sap`, total bruto (todas Org Vendas, incl. filial "
        "estrangeira/intercompany), convertido pra R$ (câmbio `TCURR`)."
    )
    p.lazy("evolucao", altura=320, gatilho="revealed")
    p.divider()
    p.subheader("Faturamento Comercial (quebra por Canal/Divisional/Regional/Setor)")
    p.caption(
        "Mesmo total de `vendas_sap.fct_faturamento_itens_sap` acima, quebrado por dimensão comercial "
        "via o crosswalk cliente→setor (`vendas.dim_cliente_setor` → `vendas.dim_estrutura`, ~52% de cobertura)."
    )
    p.lazy("comercial", altura=110)
    p.divider()
    p.subheader("Pontos de atenção")
    p.caption("Calculados ao vivo a partir do backlog, estoque, crédito, devoluções e meta de hoje.")
    p.lazy("atencao", altura=200, gatilho="revealed")
    p.divider()
    _mapa_paginas(p, ctx)


def _mapa_paginas(p: Pagina, ctx: Ctx) -> None:
    """Atalhos para as páginas (gerado do registro — nunca fica desatualizado)."""
    p.subheader("Páginas")
    secoes = [(s, pags) for s, pags in menu(ctx.usuario["role"]).items() if s and s != "Administração"]
    for coluna, (secao, paginas) in zip(p.columns(len(secoes)), secoes):
        itens = Markup("").join(
            Markup(f'<li><a href="{escape(pag.url)}">{icone(pag.icone)} {escape(pag.titulo)}</a></li>') for pag in paginas
        )
        coluna.card(SECOES[secao][1]).html(Markup(f'<ul class="lista-links">{itens}</ul>'))


AQUECER = [_executivos, lambda: _dados_comerciais(datetime.date.today())]
