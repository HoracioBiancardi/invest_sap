"""Remessas — volume por tipo x centro e detalhe item a item (porte de `pages/21_Remessas.py`).
Fonte `GOLD.vendas_sap.fct_remessa_itens_sap` (LIKP/LIPS). Os status SAP (Wbsta/Lfgsa/Lvsta/
Fksta) estão 100% NULL nesta base e não viram filtro; sem valor financeiro nem prazo/atraso."""

from __future__ import annotations

import datetime
from typing import Optional

import pandas as pd

from scripts.query_vendas_sap import remessas, remessas_resumo
from web import fmt
from web.cache import cached
from web.ui import Ctx, Pagina
from web.views._comum import config, filtros_executivo, nota_config


@cached(ttl=300, nome="remessas: resumo")
def _resumo(inicio: datetime.date, fim: datetime.date, excl: bool) -> pd.DataFrame:
    return remessas_resumo(data_inicio=inicio, data_fim=fim, excluir_intercompany=excl)


@cached(ttl=300, nome="remessas: detalhe")
def _detalhe(pedido: Optional[str], produto: Optional[str], inicio: datetime.date, fim: datetime.date, excl: bool) -> pd.DataFrame:
    return remessas(numero_pedido=pedido, codigo_produto=produto, data_inicio=inicio, data_fim=fim, limit=2000, excluir_intercompany=excl)


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Remessas", "local_shipping")
    p.caption(
        "Fonte: `GOLD.vendas_sap.fct_remessa_itens_sap` (LIKP/LIPS — grão Entrega+Item). Os status SAP "
        "(Wbsta/Lfgsa/Lvsta/Fksta) estão **100% NULL** nesta base — não viram filtro. Sem valor financeiro nem "
        "prazo/atraso no modelo; pra status de pendência confiável, veja **Pedidos**/**Pendência x Estoque**."
    )
    hoje = datetime.date.today()
    a, b, _ = p.columns(3)
    inicio = a.date_input("Período — de", "remessas_de", hoje - datetime.timedelta(days=30), maximo=hoje)
    fim = b.date_input("até", "remessas_ate", hoje, maximo=hoje)
    if inicio > fim:
        inicio, fim = fim, inicio
    p.caption("Período por `Data_Remessa` (data real de saída).")
    filtros = filtros_executivo(p, "remessas", mostrar_pedido=True, mostrar_material=True)
    excl = config("excluir_intercompany")
    nota = nota_config(intercompany=True)
    if nota:
        p.caption(nota.strip())

    p.subheader("Volume por Tipo de Remessa x Centro")
    resumo = _resumo(inicio, fim, excl)
    card = p.card()
    if resumo.empty:
        card.info("Nada encontrado para esse período.")
    else:
        card.metrics(
            [
                ("Remessas (documentos distintos)", fmt.num(resumo["Qtd_Remessas"].sum())),
                ("Qtd remetida total", fmt.num(resumo["Qtd_Remetida_Total"].sum())),
                ("Peso líquido total (kg)", fmt.num(resumo["Peso_Liquido_Total"].sum())),
            ]
        )
        x, y = card.columns(2)
        x.bar_chart(resumo.groupby("Codigo_Centro")["Qtd_Remetida_Total"].sum().rename("Qtd remetida"))
        y.bar_chart(resumo.groupby("Tipo_Remessa")["Qtd_Remetida_Total"].sum().rename("Qtd remetida"))
        card.table(resumo, nome_arquivo="remessas_resumo")
    p.divider()
    p.subheader("Detalhe (item a item)")
    detalhe = _detalhe(filtros.get("numero_pedido"), filtros.get("codigo_produto"), inicio, fim, excl)
    if detalhe.empty:
        p.info("Nada encontrado para esse filtro.")
        return
    colunas = [
        "Numero_Entrega", "Item_Entrega", "Numero_Pedido_Origem", "Item_Pedido_Origem", "Data_Remessa", "Tipo_Remessa",
        "Codigo_Produto", "Codigo_Centro", "Codigo_Cliente", "Nome_Cliente", "Charg_Numero_Do_Lote", "Qtd_Remetida",
        "Peso_Liquido", "Rota",
    ]
    p.caption(f"{fmt.num(len(detalhe))} linha(s) (teto de 2.000).")
    p.card().table(detalhe[colunas], nome_arquivo="remessas_detalhe")
