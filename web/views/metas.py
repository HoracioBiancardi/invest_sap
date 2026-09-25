"""Faturamento x Meta (SAP) — meta de `vendas.fat_meta_equipe` x realizado atribuído ao mesmo
`cod_setor` via `vendas.dim_cliente_setor` (porte de `pages/11_Metas.py`). Herda a cobertura
~52% do crosswalk: faturamento sem setor cai em BU 'NAO ALOCADO' (não some)."""

from __future__ import annotations

import datetime
from typing import Optional

import pandas as pd

from scripts.query_vendas_sap import meta_vs_realizado_mensal
from web import charts, fmt
from web.cache import cached
from web.ui import Ctx, Pagina

BUS = ["ONCO-HEMATO", "FARMA", "BLAU AESTHETICS", "MS", "Botulift"]


@cached(ttl=900, nome="metas: meta x realizado mensal")
def _meta(meses: int, bu: Optional[tuple[str, ...]]) -> pd.DataFrame:
    fim = datetime.date.today()
    inicio = (fim.replace(day=1) - pd.DateOffset(months=meses - 1)).date()
    return meta_vs_realizado_mensal(data_inicio=inicio, data_fim=fim, bu=bu)


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Meta x Realizado", "track_changes")
    p.caption(
        "Meta vem de `vendas.fat_meta_equipe` — planejamento comercial (SharePoint, mês x setor x "
        "material); não existe fonte SAP/Salesforce equivalente. Realizado é atribuído ao mesmo `cod_setor` "
        "via `vendas.dim_cliente_setor` (~52% de cobertura) — cliente sem setor cai em BU **'NAO ALOCADO'**. "
        "`BU` é o valor bruto de `fat_meta_equipe.bu`, não 1:1 com a Linha de Negócio da página Faturamento."
    )
    a, b = p.columns([1, 1])
    meses = a.slider("Período (meses)", "metas_meses", 3, 24, 8)
    bu_opcoes = b.multiselect("BU", BUS, "metas_bu", help="Vazio = todas.")
    df = _meta(meses, tuple(sorted(bu_opcoes)) or None)
    if df.empty:
        p.info("Nada encontrado para esse período/BU.")
        return
    df = df.assign(Atingimento=(df["Valor_Realizado"] / df["Meta_Valor"]).where(df["Meta_Valor"] > 0))
    total_meta, total_real = df["Meta_Valor"].sum(), df["Valor_Realizado"].sum()
    p.metrics(
        [
            ("Meta Total", fmt.brl(total_meta)),
            ("Realizado Total", fmt.brl(total_real)),
            ("Atingimento", fmt.pct(total_real / total_meta if total_meta else 0, 1)),
        ]
    )
    p.caption(
        "Atingimento acima **exclui** BU 'NAO ALOCADO' do numerador/denominador de meta (não tem meta) — "
        "o realizado 'NAO ALOCADO' continua à parte na tabela. Não é o faturamento total da empresa."
    )
    p.divider()
    p.subheader("Meta x Realizado por mês (BUs com meta)")
    p.caption("Rótulo sobre a barra de Venda = % de atingimento do mês.")
    com_meta = df[df["BU"] != "NAO ALOCADO"]
    if not com_meta.empty:
        comparacao = com_meta.groupby("Mes")[["Meta_Valor", "Valor_Realizado"]].sum().reset_index()
        p.card().chart(charts.meta_realizado(comparacao, "Mes"))
    p.divider()
    p.subheader("Atingimento por BU (R$, soma do período)")
    por_bu = com_meta.groupby("BU")[["Meta_Valor", "Valor_Realizado"]].sum().reset_index().sort_values("Valor_Realizado", ascending=False)
    card = p.card()
    x, y = card.columns([2, 1])
    x.chart(charts.meta_realizado(por_bu, "BU"))
    y.table(
        por_bu.assign(Atingimento=(por_bu["Valor_Realizado"] / por_bu["Meta_Valor"]).where(por_bu["Meta_Valor"] > 0)),
        {"Meta_Valor": "brl0", "Valor_Realizado": "brl0", "Atingimento": "pct1"},
    )
    p.divider()
    p.subheader("Detalhe mensal")
    p.card().table(
        df[["Mes", "BU", "Meta_Valor", "Valor_Realizado", "Atingimento", "Meta_Unidades", "Unidades_Realizado"]],
        {"Meta_Valor": "brl0", "Valor_Realizado": "brl0", "Atingimento": "pct1", "Meta_Unidades": "num0", "Unidades_Realizado": "num0"},
        nome_arquivo="meta_realizado_mensal",
    )
