"""Crédito e Devoluções — limite/exposição por cliente e devoluções/abatimentos com motivo
(porte de `pages/7_Credito_Devolucoes.py`). Só a aba aberta consulta o DW."""

from __future__ import annotations

import datetime
from typing import Optional

import pandas as pd

from scripts.query_vendas_sap import credito_disponivel_clientes, devolucoes_credito_motivo
from web import fmt
from web.cache import cached
from web.ui import Ctx, Node, Pagina
from web.views._comum import config, filtro_periodo_tipo_cliente, nota_config, tipo_cliente_param


@cached(ttl=300, nome="crédito: limite por cliente")
def _credito(apenas_bloqueados: bool, tipo_cliente: Optional[str], limite: int, excl_interco: bool) -> pd.DataFrame:
    return credito_disponivel_clientes(apenas_bloqueados, tipo_cliente=tipo_cliente, limit=limite, excluir_intercompany=excl_interco)


@cached(ttl=300, nome="crédito: devoluções/abatimentos")
def _devolucoes(inicio: datetime.date, fim: datetime.date, excluir_rv: bool, cliente: Optional[str], tipo_cliente: Optional[str], excl_interco: bool) -> pd.DataFrame:
    return devolucoes_credito_motivo(
        data_inicio=inicio, data_fim=fim, excluir_faturamento_rotina=excluir_rv, nome_cliente=cliente,
        tipo_cliente=tipo_cliente, limit=5000, excluir_intercompany=excl_interco,
    )


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Crédito e Devoluções", "credit_card")
    inicio, fim, tipo_opcao = filtro_periodo_tipo_cliente(p)
    p.caption(f"Filtro: tipo de cliente **{tipo_opcao}**." + nota_config(intercompany=True))
    abas = p.tabs(["Limite de crédito", "Devoluções / abatimentos (com motivo)"], "aba")
    if abas.ativa == 0:
        _aba_limite(abas.corpo, tipo_cliente_param(tipo_opcao))
    else:
        _aba_devolucoes(abas.corpo, inicio, fim, tipo_cliente_param(tipo_opcao))


def _aba_limite(p: Node, tipo_cliente: Optional[str]) -> None:
    p.caption("Fonte: `GOLD.vendas_sap.fct_limite_credito_sap` — limite/exposição por cliente.")
    a, b = p.columns([1, 2])
    apenas_bloqueados = a.checkbox("Só clientes bloqueados", "cred_bloqueados")
    limite = b.slider("Máximo de linhas", "cred_limite", 100, 20000, 5000, 100)
    df = _credito(apenas_bloqueados, tipo_cliente, limite, config("excluir_intercompany"))
    if df.empty:
        p.info("Nada encontrado.")
        return
    if len(df) == limite:
        p.warning(
            f"Resultado truncado em {fmt.num(limite)} linhas — pode haver mais clientes além desse teto. "
            "Ajuste o filtro ou aumente o 'Máximo de linhas' acima."
        )
    p.metrics(
        [
            ("Clientes", fmt.num(len(df))),
            ("Limite Concedido Total", fmt.brl(df["Valor_Limite_Credito_Concedido"].sum(), 2)),
            ("Saldo Vencido Total", fmt.brl(df["Valor_Saldo_Vencido"].sum(), 2)),
        ]
    )
    colunas = [
        "Codigo_Cliente", "Nome_Cliente", "Classe_Risco_Cliente", "Flag_Cliente_Bloqueado",
        "Valor_Limite_Credito_Concedido", "Valor_Exposicao_Total_SAP", "Valor_Saldo_A_Vencer",
        "Valor_Saldo_Vencido", "Valor_Credito_Disponivel",
    ]
    p.card().table(df[colunas], {c: "brl2" for c in colunas if c.startswith("Valor_")}, nome_arquivo="limite_credito")


def _aba_devolucoes(p: Node, inicio: datetime.date, fim: datetime.date, tipo_cliente: Optional[str]) -> None:
    p.caption(
        "Fonte: `GOLD.vendas_sap.fct_credito_devolucoes_sap` — lançamentos de crédito/devolução/abatimento "
        "com o texto de motivo (livre) do financeiro. Por padrão exclui `Tp_doc = 'RV'` (transferência de "
        "faturamento de rotina, ~92% das linhas). **Montante vem com o sinal contábil real do SAP** "
        "(negativo = crédito/`H`, positivo = débito/`S`) — os totais são posição líquida, não soma bruta."
    )
    a, b = p.columns(2)
    cliente = a.text_input("Cliente contém (opcional)", "dev_cliente").strip() or None
    incluir_rv = b.checkbox("Incluir faturamento de rotina (RV)", "dev_rv")
    p.caption(f"Período: **{fmt.data(inicio)}** a **{fmt.data(fim)}** (filtro de período no topo).")
    df = _devolucoes(inicio, fim, not incluir_rv, cliente, tipo_cliente, config("excluir_intercompany"))
    if df.empty:
        p.info("Nada encontrado para esse filtro.")
        return
    p.metrics(
        [
            ("Qtd Lançamentos", fmt.num(len(df))),
            ("Valor Líquido (Débito − Crédito)", fmt.brl(df["Montante"].sum(), 2), None,
             "Soma de Montante já com sinal contábil — não é o total bruto de transações."),
        ]
    )
    p.subheader("Por tipo de documento (código SAP)")
    p.caption(
        "Sem tradução oficial pra esses códigos ainda (T003T implementada no data-platform, sem dado real "
        "até a extração Bronze→Silver rodar) — use o texto de motivo abaixo, bem mais informativo."
    )
    p.card().bar_chart(df.groupby("Tp_doc")["Montante"].sum().rename("Montante (R$)"), fmt_valor="brl0")
    p.subheader("Motivos mais frequentes")
    top = df.groupby("Texto")["Montante"].agg(Valor_Total="sum", Qtd="count").sort_values("Valor_Total", ascending=False).head(30)
    p.card().table(top, {"Valor_Total": "brl2", "Qtd": "num0"}, indice=True, nome_arquivo="motivos_devolucao")
    p.divider()
    p.subheader(f"Detalhe ({fmt.num(len(df))} linhas)")
    colunas = ["N_documento", "Codigo_Cliente", "Nome_Cliente", "Data_documento", "Tp_doc", "Indicador_Debito_Credito", "Montante", "Texto"]
    p.card().table(df[colunas], {"Montante": "num2", "Data_documento": "date"}, nome_arquivo="devolucoes_detalhe")
