"""Cliente 360 — pedido/pendência/fatura, crédito, devoluções, oportunidade e remessas de 1
cliente (porte de `pages/25_Cliente_360.py`). Busca por nome ou código; sem cliente escolhido,
mostra os que têm mais pendência (clique numa linha)."""

from __future__ import annotations

import pandas as pd

from scripts.query_vendas_sap import buscar_cliente_por_nome, cliente_360, top_clientes_pendentes
from web import fmt
from web.cache import cached
from web.ui import Ctx, Node, Pagina
from web.views._comum import config


@cached(ttl=600, nome="cliente 360: busca por nome")
def _busca(fragmento: str) -> pd.DataFrame:
    return buscar_cliente_por_nome(fragmento)


@cached(ttl=600, nome="cliente 360: top clientes por pendência")
def _top(n: int, excl: bool) -> pd.DataFrame:
    return top_clientes_pendentes(n=n, excluir_intercompany=excl)


@cached(ttl=300, nome="cliente 360: dados do cliente")
def _cliente(codigo: str, somente_pendente: bool) -> dict[str, pd.DataFrame]:
    return cliente_360(codigo, somente_pendente=somente_pendente)


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Cliente 360", "account_circle")
    p.caption(
        "Busque pelo nome (parcial) pra achar o código, ou informe o `Codigo_Cliente` direto — pedido/pendência/"
        "fatura, crédito e devoluções juntos, sem cruzar página na mão."
    )
    # Escolha na busca por nome ou clique no top clientes preenche o código.
    for origem in ("c360_escolha", "c360_top_sel"):
        valor = ctx.get(origem) or ""
        if valor:
            ctx.params["cliente360_codigo"] = [valor]
            ctx.params[origem] = [""]
    a, b, c = p.columns([1.3, 1, 1])
    nome = a.text_input("Buscar pelo nome (parcial, opcional)", "cliente360_nome_busca", placeholder="ex.: BRAZMIX").strip()
    if nome:
        achados = _busca(nome)
        if achados.empty:
            a.caption("Nenhum cliente encontrado com esse nome.")
        else:
            opcoes = [(row.Codigo_Cliente, f"{row.Nome_Cliente} — {row.Codigo_Cliente}") for row in achados.itertuples()]
            a.selectbox(
                f"{len(opcoes)} resultado(s) — escolha um", opcoes, "c360_escolha",
                formatar=lambda o: o[1], valor_de=lambda o: str(o[0]), placeholder="Escolha um cliente...",
            )
    codigo = b.text_input("Código do cliente", "cliente360_codigo", placeholder="ex.: 0001004873").strip()
    somente_pendente = c.checkbox("Só backlog aberto (aba Pedido)", "cliente360_somente_pendente")

    if not codigo:
        _escolha_top(p)
        return
    _painel_cliente(p, codigo, somente_pendente)


def _escolha_top(p: Pagina) -> None:
    excl = config("excluir_intercompany")
    p.caption(
        "Ou escolha direto um dos clientes com mais pendência (clique numa linha)"
        + (' — filiais intercompany ("BLAU*") excluídas por config do Admin; busque pelo nome se precisar de uma delas.' if excl else ".")
    )
    top = _top(10, excl)
    if not top.empty:
        p.card().table(
            top, {"Qtd_Pendente_Total": "num0", "Valor_Pendente_Total": "brl2"},
            selecao="c360_top_sel", coluna_selecao="Codigo_Cliente", nome_arquivo="top_clientes_pendencia",
        )


def _painel_cliente(p: Pagina, codigo: str, somente_pendente: bool) -> None:
    r = _cliente(codigo, somente_pendente)
    pedidos = r["Pedido / Pendência / Fatura"]
    if all(df.empty for df in r.values()):
        p.info("Nada encontrado para esse código de cliente.")
        return
    p.subheader(pedidos["Nome_Cliente"].iloc[0] if not pedidos.empty else codigo)
    if not pedidos.empty:
        # Valor_* vem na moeda do pedido — só BRL nos totais.
        brl = pedidos[pedidos["Moeda"] == "BRL"]
        nao_brl = int((pedidos["Moeda"] != "BRL").sum())
        p.metrics(
            [
                ("Itens de pedido", fmt.num(len(pedidos))),
                ("Valor pendente de faturamento (BRL)", fmt.brl(brl["Valor_Pendente_Faturamento"].sum(), 2)),
                ("Valor faturado (BRL, histórico da consulta)", fmt.brl(brl["Valor_Liquido_Faturado"].sum(), 2)),
            ]
        )
        if nao_brl:
            p.caption(
                f":material/info: {fmt.num(nao_brl)} item(ns) deste cliente têm pedido em moeda diferente de BRL — "
                "não entram nos totais acima, mas aparecem no detalhe com a coluna `Moeda`."
            )
    credito = r["Crédito"]
    if not credito.empty:
        bloqueado = (credito["Flag_Cliente_Bloqueado"] == "X").any()
        disponivel = credito["Valor_Credito_Disponivel"].min()
        if bloqueado or disponivel < 0:
            p.warning(
                f"Cliente **{'bloqueado por crédito' if bloqueado else 'sem limite de crédito disponível'}** — pior caso "
                f"entre áreas de crédito: {fmt.brl(disponivel, 2)} disponível."
            )
    p.divider()
    abas = p.tabs_locais(["Pedido / Pendência / Fatura", "Crédito", "Devoluções (12 meses)", "Oportunidade", "Remessas"])
    _aba_pedidos(abas[0], pedidos)
    _tabela_ou_aviso(abas[1], credito, "Nenhum registro de crédito encontrado para esse cliente.", "credito_cliente")
    _tabela_ou_aviso(abas[2], r["Devoluções / abatimentos (últimos 12 meses)"], "Nenhuma devolução/abatimento nos últimos 12 meses.", "devolucoes_cliente")
    _aba_oportunidade(abas[3], r["Oportunidade"])
    rem = r["Remessas"]
    colunas_rem = ["Numero_Entrega", "Item_Entrega", "Numero_Pedido_Origem", "Data_Remessa", "Tipo_Remessa", "Codigo_Produto",
                   "Codigo_Centro", "Charg_Numero_Do_Lote", "Qtd_Remetida"]
    _tabela_ou_aviso(abas[4], rem[colunas_rem] if not rem.empty else rem, "Nenhuma remessa nos últimos 24 meses.", "remessas_cliente")


def _tabela_ou_aviso(p: Node, df: pd.DataFrame, aviso: str, arquivo: str) -> None:
    if df.empty:
        p.info(aviso)
    else:
        p.card().table(df, nome_arquivo=arquivo)


def _aba_pedidos(p: Node, pedidos: pd.DataFrame) -> None:
    if pedidos.empty:
        p.info("Nenhum pedido encontrado para esse filtro.")
        return
    colunas = [
        "Numero_Pedido", "Item_Pedido", "Data_Inclusao_Pedido", "Codigo_Produto", "Descricao_Produto", "Nome_Centro",
        "Qtd_Pedida", "Qtd_Faturada", "Qtd_Pendente_Operacional", "Moeda", "Valor_Liquido_Pedido", "Valor_Liquido_Faturado",
        "Valor_Pendente_Faturamento", "Status_Pendencia", "Status_Faturamento",
    ]
    p.card().table(pedidos[colunas], nome_arquivo="pedidos_cliente")


def _aba_oportunidade(p: Node, opp: pd.DataFrame) -> None:
    casados = opp[opp["Nome_Oportunidade"].notna()] if not opp.empty else opp
    if casados.empty:
        p.info("Nenhuma Oportunidade vinculada nos últimos 24 meses (cobertura de ~73% medida — ver docs).")
        return
    p.caption("Valor de Oportunidade (deduplicado), por moeda:")
    p.valor_por_moeda(casados.drop_duplicates(subset=["Nome_Oportunidade", "Data_Criacao_Oportunidade"]), "Valor_Oportunidade", moeda_col="Moeda_Oportunidade")
    colunas = [
        "Numero_Pedido", "Item_Pedido", "Codigo_Produto", "Descricao_Produto", "Nome_Oportunidade", "Estagio_Oportunidade",
        "Oportunidade_Ganha", "Moeda_Oportunidade", "Valor_Oportunidade", "Valor_Item_Oportunidade", "Status_Pendencia",
    ]
    p.card().table(casados[colunas], nome_arquivo="oportunidades_cliente")
