"""Pendência x Estoque — visão global do backlog aberto (porte de `pages/27_Pendencia_x_Estoque.py`).

Classifica cada item num `Motivo_Principal` (Sem Estoque / Estoque Parcial / Financeiro-Crédito /
Fiscal-Faturamento / Logístico-Remessa), quebra por Org Vendas e Linha de Negócio, ranqueia
materiais sem cobertura e abre 1 item com o estoque REAL na data do pedido (`IB_SAPECC.MCHBH`).
Ver a docstring da página Streamlit original para o histórico dos achados (falso positivo
já faturado corrigido na fonte em 2026-09-14, intercompany, pedido zumbi, moeda).

Diferenças do Streamlit:
- a base inteira (merges de Org Vendas, crédito, zumbi, Linha de Negócio) é montada 1x e fica
  no cache (`_base`), então trocar filtro só refaz o recorte em pandas, sem ir ao DW;
- o contexto do item (Oportunidade, Remessas, crédito, MCHBH) é um bloco carregado à parte —
  a lista de pedidos aparece na hora e o HANA responde depois.
"""

from __future__ import annotations

import datetime as _dt

import pandas as pd

from scripts import app_db
from scripts.query_vendas_sap import (
    CLIENTE_INTERCOMPANY_LIKE,
    ORG_VENDAS_INTERNACIONAL_LIKE,
    chave_org_vda_cli_pandas,
    correlacao_oportunidade_pedido_pendencia_fatura,
    credito_disponivel_clientes,
    estoque_reservado_por_material_centro,
    estoque_restrito_disponivel,
    flag_intercompany,
    flag_org_vendas_internacional,
    linha_negocio_por_cliente,
    movimento_estoque_resumo_material_centro,
    organizacoes_vendas_texto,
    pendencia_x_estoque_global,
    remessas,
)
from scripts.trace_lote import estoque_historico_material_centro
from web import fmt
from web.cache import cached, em_paralelo
from web.ui import Ctx, Node, Pagina
from web.views._comum import config

MOTIVOS_SEM_ESTOQUE = ["Sem Estoque", "Estoque Parcial"]
MOTIVOS_LOGISTICOS = ("Logístico e Fiscal", "Logístico (Remessa)", "Fiscal (Faturamento)")


# ── consultas (cache) ────────────────────────────────────────────────────────


@cached(ttl=300, warm=True, nome="pendência x estoque: fontes")
def _fontes() -> dict[str, pd.DataFrame]:
    return em_paralelo(
        pendencia=pendencia_x_estoque_global,
        org=organizacoes_vendas_texto,
        linha=linha_negocio_por_cliente,
        reservado=estoque_reservado_por_material_centro,
    )


@cached(ttl=1800, warm=True, nome="pendência x estoque: estoque por material")
def _estoque(excluir_internacional: bool) -> pd.DataFrame:
    # limit alto: todo Material+Centro com estoque físico > 0, não só o top 500 por valor.
    return estoque_restrito_disponivel(limit=100_000, excluir_paises_internacionais=excluir_internacional)


@cached(ttl=1800, warm=True, nome="pendência x estoque: movimento MSEG 24m")
def _movimento() -> pd.DataFrame:
    return movimento_estoque_resumo_material_centro(meses=24)


@cached(ttl=3600, nome="MCHBH: estoque na data do pedido")
def _estoque_historico(material: str, centro: str, data_corte: str) -> dict:
    return estoque_historico_material_centro(material, centro, data_corte)


@cached(ttl=300, nome="crédito de 1 cliente")
def _credito_cliente(codigo_cliente: str) -> pd.DataFrame:
    return credito_disponivel_clientes(codigo_cliente=codigo_cliente)


@cached(ttl=300, nome="oportunidade de 1 pedido")
def _oportunidade_pedido(numero_pedido: str) -> pd.DataFrame:
    return correlacao_oportunidade_pedido_pendencia_fatura(numero_pedido=numero_pedido, apenas_pendentes=False)


@cached(ttl=300, nome="remessas de 1 pedido")
def _remessas_pedido(numero_pedido: str) -> pd.DataFrame:
    return remessas(numero_pedido=numero_pedido)


# ── classificação ────────────────────────────────────────────────────────────


def _classificar_motivo_principal(row: pd.Series) -> str:
    """1. Financeiro (bloqueado ou crédito disponível < 0, pior caso entre áreas);
    2. Sem Estoque / Estoque Parcial; 3. Fiscal/Logístico (tem estoque, travado em documento)."""
    if row.get("Cliente_Bloqueado") == 1 or (pd.notna(row.get("Valor_Credito_Disponivel")) and row["Valor_Credito_Disponivel"] < 0):
        return "Financeiro (crédito)"
    status_estoque = row.get("Status_Pendencia_Estoque")
    if status_estoque == "Pendente sem Estoque":
        return "Sem Estoque"
    if status_estoque == "Pendente com Estoque Parcial":
        return "Estoque Parcial"
    status_pendencia = row.get("Status_Pendencia")
    if status_pendencia == "Pendente Fiscal (Faturamento)":
        return "Fiscal (Faturamento)"
    if status_pendencia == "Pendente Logistico (Remessa)":
        return "Logístico (Remessa)"
    if status_pendencia == "Pendente Logistico e Fiscal":
        return "Logístico e Fiscal"
    return "Outro"


def _classificar_causa_estoque_material(row: pd.Series) -> str:
    """Motivo provável de falta de estoque no Material+Centro (snapshot atual x MSEG 24 meses)."""
    if row.get("Qtd_Qualidade", 0) > 0:
        return "Preso em qualidade agora"
    if row.get("Qtd_Bloqueado", 0) > 0:
        return "Bloqueado agora (não é etapa normal)"
    if pd.isna(row.get("Data_Ultima_Entrada")):
        return "Sem entrada registrada (24 meses) — nunca produzido/recebido nessa janela"
    if row.get("Qtd_Disponivel_Venda", 0) > 0:
        return "Tem estoque livre — não alocado a este pedido na fila FIFO"
    if pd.notna(row.get("Data_Ultima_Saida")):
        return "Já vendido/consumido — produção não repôs desde a última saída"
    return "Sem dado suficiente pra classificar"


@cached(ttl=300, warm=True, nome="pendência x estoque: base classificada")
def _base(limiar_zumbi_dias: int) -> pd.DataFrame:
    """Backlog inteiro com Org Vendas, Motivo_Principal, flags, zumbi e Linha de Negócio."""
    fontes = _fontes()
    df = fontes["pendencia"]
    if df.empty:
        return df
    df = df.merge(fontes["org"], on="Codigo_Org_Vendas", how="left")
    df["Nome_Org_Vendas"] = df["Nome_Org_Vendas"].fillna(df["Codigo_Org_Vendas"])
    df["Motivo_Principal"] = df.apply(_classificar_motivo_principal, axis=1)
    df["Flag_Intercompany"] = flag_intercompany(df["Nome_Cliente"])
    df["Flag_Org_Internacional"] = flag_org_vendas_internacional(df["Nome_Org_Vendas"])

    # Possivel_Zumbi: propriedade do Material+Centro, calculada sobre TODO o backlog sem
    # estoque (antes de qualquer filtro da tela), pra não oscilar conforme o filtro.
    sem_estoque = df[df["Motivo_Principal"].isin(MOTIVOS_SEM_ESTOQUE)]
    zumbi = (
        sem_estoque.groupby(["Codigo_Produto", "Codigo_Centro"])
        .agg(
            Dias_Pedido_Mais_Antigo=("Dias_Desde_Inclusao_Pedido", "max"),
            Qtd_Sem_Estoque_Material=("Qtd_Pendente_Operacional", "sum"),
        )
        .reset_index()
        .merge(fontes["reservado"], on=["Codigo_Produto", "Codigo_Centro"], how="left")
    )
    zumbi["Qtd_Reservada"] = zumbi["Qtd_Reservada"].fillna(0)
    zumbi["Possivel_Zumbi"] = (zumbi["Dias_Pedido_Mais_Antigo"] > limiar_zumbi_dias) & (
        zumbi["Qtd_Reservada"] < zumbi["Qtd_Sem_Estoque_Material"]
    )
    df = df.merge(
        zumbi[["Codigo_Produto", "Codigo_Centro", "Dias_Pedido_Mais_Antigo", "Qtd_Reservada", "Possivel_Zumbi"]],
        on=["Codigo_Produto", "Codigo_Centro"], how="left",
    )
    # .astype(bool): o merge deixa dtype object (True/False/NaN) e `~` viraria NOT de inteiro.
    df["Possivel_Zumbi"] = df["Possivel_Zumbi"].fillna(False).astype(bool)

    # Linha de Negócio: MANUAL casa por cliente+Org (`chave_org_vda_cli`), HEURISTICA_PRODUTO
    # só por cliente; quem sobra vira NAO ALOCADO.
    lookup = fontes["linha"]
    manual = lookup[lookup["Origem_Linha_Negocio"] == "MANUAL"][["chave_org_vda_cli", "Linha_Negocio", "Origem_Linha_Negocio"]]
    heuristica = lookup[lookup["Origem_Linha_Negocio"] == "HEURISTICA_PRODUTO"].rename(
        columns={"Codigo_Cliente": "_cliente_num"}
    )[["_cliente_num", "Linha_Negocio", "Origem_Linha_Negocio"]]
    df["_chave"] = chave_org_vda_cli_pandas(df["Codigo_Cliente"], df["Codigo_Org_Vendas"])
    df = df.merge(manual, left_on="_chave", right_on="chave_org_vda_cli", how="left").drop(columns=["_chave", "chave_org_vda_cli"])
    df["_cliente_num"] = pd.to_numeric(df["Codigo_Cliente"], errors="coerce")
    sem_match = df["Linha_Negocio"].isna()
    df = df.merge(heuristica, on="_cliente_num", how="left", suffixes=("", "_h")).drop(columns="_cliente_num")
    df.loc[sem_match, "Linha_Negocio"] = df.loc[sem_match, "Linha_Negocio"].fillna(df.loc[sem_match, "Linha_Negocio_h"])
    df.loc[sem_match, "Origem_Linha_Negocio"] = df.loc[sem_match, "Origem_Linha_Negocio"].fillna(df.loc[sem_match, "Origem_Linha_Negocio_h"])
    df = df.drop(columns=["Linha_Negocio_h", "Origem_Linha_Negocio_h"])
    df["Linha_Negocio"] = df["Linha_Negocio"].fillna("NAO ALOCADO")
    df["Origem_Linha_Negocio"] = df["Origem_Linha_Negocio"].fillna("NAO_ALOCADO")
    # Todo total em R$ usa esta coluna (0 fora de BRL) — nunca mistura moeda numa soma.
    df["Valor_Pendente_Faturamento_BRL"] = df["Valor_Pendente_Faturamento"].where(df["Moeda"] == "BRL", 0.0)
    return df


def _limiar() -> int:
    return int(app_db.get_setting("limiar_zumbi_dias"))


# ── componentes ──────────────────────────────────────────────────────────────


def _resumo_por(p: Node, df: pd.DataFrame, coluna: str, valor: str, qtd: str) -> None:
    """Tabela Valor/Qtd por dimensão + barras de % do total (Valor x Quantidade)."""
    resumo = (
        df.groupby(coluna)
        .agg(**{valor: ("Valor_Pendente_Faturamento_BRL" if valor == "Valor_Pendente_Faturamento" else valor, "sum"), qtd: (qtd, "sum")})
        .sort_values(valor, ascending=False)
    )
    card = p.card()
    a, b = card.columns([1, 1])
    a.table(resumo, {valor: "brl2", qtd: "num0"}, indice=True, nome_arquivo=f"por_{coluna.lower()}")
    b.caption("% do total — Valor x Quantidade (escalas bem diferentes, comparar em R$/un direto não faz sentido)")
    pct = pd.DataFrame(
        {
            "% do Valor": resumo[valor] / resumo[valor].sum() * 100 if resumo[valor].sum() else 0,
            "% da Quantidade": resumo[qtd] / resumo[qtd].sum() * 100 if resumo[qtd].sum() else 0,
        }
    )
    b.bar_chart(pct, horizontal=True, fmt_valor="pctv1", altura=max(180, 46 * len(pct) + 60))


def _pivots(p: Node, df: pd.DataFrame, colunas: str) -> None:
    valor_tab, qtd_tab = p.tabs_locais(["Valor pendente (R$)", "Quantidade pendente (un)"])
    pivot_valor = df.pivot_table(index="Motivo_Principal", columns=colunas, values="Valor_Pendente_Faturamento_BRL", aggfunc="sum", fill_value=0)
    pivot_qtd = df.pivot_table(index="Motivo_Principal", columns=colunas, values="Qtd_Pendente_Operacional", aggfunc="sum", fill_value=0)
    valor_tab.card().table(pivot_valor, formato_todas="brl0", indice=True, nome_arquivo="pivot_valor")
    qtd_tab.card().table(pivot_qtd, formato_todas="num0", indice=True, nome_arquivo="pivot_qtd")


# ── página ───────────────────────────────────────────────────────────────────


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Pendência x Estoque", "fact_check")
    p.caption(
        "Todo o backlog aberto (`Flag_Pendencia = 1`), classificado por `Motivo_Principal` — cruza "
        "`Status_Pendencia_Estoque` (item vs. estoque do Material+Centro), crédito do cliente "
        "(`fct_limite_credito_sap`, pior caso entre áreas) e `Status_Pendencia` (documento de "
        "remessa/fatura pendente mesmo com estoque). Clique num pedido pra ver os itens, e num item "
        "pro contexto completo."
    )

    # Ações que mexem nos filtros antes dos widgets serem desenhados (equivalente aos
    # callbacks `on_click`/`on_select` do Streamlit).
    if ctx.get("_btn") == "pxe_limpar_mc":
        ctx.params["pxe_material"], ctx.params["pxe_centro"] = [""], [""]
    selecao_ranking = ctx.get("pxe_rank_sel") or ""
    if "|" in selecao_ranking:
        material, centro = selecao_ranking.split("|", 1)
        ctx.params["pxe_material"], ctx.params["pxe_centro"] = [material], [centro]
        ctx.params.pop("pxe_pedido", None)

    limiar = _limiar()
    df = _base(limiar)
    if df.empty:
        p.info("Nenhum backlog aberto encontrado.")
        return

    n_interco = int(df["Flag_Intercompany"].sum())
    if n_interco:
        p.caption(
            f":material/info: {fmt.num(n_interco)} item(ns) ({fmt.num(df.loc[df['Flag_Intercompany'], 'Qtd_Pendente_Operacional'].sum())} "
            "unidades) são transferência intercompany entre filiais do próprio grupo Blau (`Nome_Cliente` "
            f'contém "{CLIENTE_INTERCOMPANY_LIKE}"), não cliente comercial — concentram boa parte da '
            "quantidade do backlog e nunca têm Linha de Negócio. Fora das métricas abaixo por padrão."
        )
    n_org = int(df["Flag_Org_Internacional"].sum())
    if n_org:
        termos = "/".join(f'"{t}"' for t in ORG_VENDAS_INTERNACIONAL_LIKE)
        p.caption(
            f":material/info: {fmt.num(n_org)} item(ns) ({fmt.num(df.loc[df['Flag_Org_Internacional'], 'Qtd_Pendente_Operacional'].sum())} "
            f"unidades) são de Organização de Vendas Colômbia/Uruguai (`Nome_Org_Vendas` contém {termos}) — "
            "diferente do intercompany acima (esse é por cliente): pode ser venda real pra cliente externo "
            "processada pela filial comercial do país."
        )
    n_zumbi = int(df["Possivel_Zumbi"].sum())
    if n_zumbi:
        p.caption(
            f":material/warning: {fmt.num(n_zumbi)} item(ns) ({fmt.num(df.loc[df['Possivel_Zumbi'], 'Qtd_Pendente_Operacional'].sum())} "
            f"unidades) são de Material+Centro com pedido mais antigo passando de {fmt.num(limiar)} dias "
            "(limiar do Admin) **e** reserva SAP menor que a quantidade sem estoque (`Possivel_Zumbi`) — sinal "
            'de pedido nunca baixado/cancelado no SAP, não falta de estoque real. Ver "Radar de pedido zumbi" em **Pedidos**.'
        )

    c1, c2, c3 = p.columns(3)
    incluir_interco = c1.checkbox(
        "Incluir transferência intercompany nas métricas abaixo", "pxe_interco",
        default=not config("excluir_intercompany"),
        help="Desmarcado = só backlog comercial real. Default vem da config do Admin.",
    )
    incluir_org = c2.checkbox(
        "Incluir Organização de Vendas Colômbia/Uruguai", "pxe_org_int",
        default=not config("excluir_org_vendas_internacional"),
        help="Desmarcado = só Organização de Vendas Brasil. Default vem da config do Admin.",
    )
    incluir_zumbi = c3.checkbox(
        "Incluir possíveis pedidos zumbi", "pxe_zumbi",
        default=not config("excluir_possivel_zumbi"),
        help="Desmarcado = tira Material+Centro marcado como Possivel_Zumbi de tudo (KPIs, pivots, ranking, detalhe).",
    )
    base = df
    if not incluir_interco:
        base = base[~base["Flag_Intercompany"]]
    if not incluir_org:
        base = base[~base["Flag_Org_Internacional"]]
    if not incluir_zumbi:
        base = base[~base["Possivel_Zumbi"]]

    f1, f2, f3, f4, f5, f6 = p.columns([1.1, 1.1, 1.1, 0.7, 0.7, 0.8])
    filtro_org = f1.multiselect("Organização de Vendas", sorted(df["Nome_Org_Vendas"].dropna().unique()), "pxe_org", help="Vazio = todas.")
    filtro_linha = f2.multiselect(
        "Linha de Negócio", sorted(df["Linha_Negocio"].dropna().unique()), "pxe_linha",
        help="Vazio = todas. Dado manual (planilha), cobertura parcial — ver 'NAO ALOCADO'.",
    )
    filtro_motivo = f3.multiselect("Motivo_Principal", sorted(df["Motivo_Principal"].dropna().unique()), "pxe_motivo", help="Vazio = todos.")
    filtro_centro = f4.text_input("Centro", "pxe_centro").strip()
    filtro_material = f5.text_input("Material", "pxe_material").strip().upper()
    filtro_cliente = f6.text_input("Cliente (nome)", "pxe_cliente").strip().upper()
    if filtro_material or filtro_centro:
        p.button("Limpar filtro de Material/Centro", "pxe_limpar_mc", icone_nome="filter_alt_off")

    filtrado = base
    if filtro_org:
        filtrado = filtrado[filtrado["Nome_Org_Vendas"].isin(filtro_org)]
    if filtro_linha:
        filtrado = filtrado[filtrado["Linha_Negocio"].isin(filtro_linha)]
    if filtro_motivo:
        filtrado = filtrado[filtrado["Motivo_Principal"].isin(filtro_motivo)]
    if filtro_centro:
        filtrado = filtrado[filtrado["Codigo_Centro"] == filtro_centro]
    if filtro_material:
        filtrado = filtrado[filtrado["Codigo_Produto"].str.upper() == filtro_material]
    if filtro_cliente:
        filtrado = filtrado[filtrado["Nome_Cliente"].str.upper().str.contains(filtro_cliente, na=False, regex=False)]

    n_nao_brl = int((filtrado["Moeda"] != "BRL").sum())
    valor_total = filtrado["Valor_Pendente_Faturamento_BRL"].sum()
    qtd_total = filtrado["Qtd_Pendente_Operacional"].sum()
    m_sem = filtrado["Motivo_Principal"].isin(MOTIVOS_SEM_ESTOQUE)
    m_fin = filtrado["Motivo_Principal"] == "Financeiro (crédito)"
    valor_sem, qtd_sem = filtrado.loc[m_sem, "Valor_Pendente_Faturamento_BRL"].sum(), filtrado.loc[m_sem, "Qtd_Pendente_Operacional"].sum()
    valor_fin, qtd_fin = filtrado.loc[m_fin, "Valor_Pendente_Faturamento_BRL"].sum(), filtrado.loc[m_fin, "Qtd_Pendente_Operacional"].sum()
    if n_nao_brl:
        p.caption(
            f":material/info: {fmt.num(n_nao_brl)} item(ns) no filtro atual têm pedido em moeda diferente de BRL "
            "(USD/UYU/COP/EUR) — não entram em nenhum total em R$ desta página, mas aparecem no detalhe "
            "por item com a coluna `Moeda`."
        )
    p.card().metrics(
        [
            ("Itens de pedido (filtro atual)", fmt.num(len(filtrado))),
            ("Valor pendente total (BRL)", fmt.brl(valor_total), f"{fmt.num(qtd_total)} un pendentes"),
            ("Sem estoque + parcial", fmt.brl(valor_sem), f"{fmt.num(qtd_sem)} un ({fmt.pct(valor_sem / valor_total if valor_total else 0, 0)} do valor)"),
            ("Financeiro (crédito)", fmt.brl(valor_fin), f"{fmt.num(qtd_fin)} un ({fmt.pct(valor_fin / valor_total if valor_total else 0, 0)} do valor)"),
        ]
    )

    if filtrado.empty:
        p.info("Nenhum item no filtro atual.")
        return

    p.markdown("**Valor e quantidade pendente por Motivo_Principal**")
    _resumo_por(p, filtrado, "Motivo_Principal", "Valor_Pendente_Faturamento", "Qtd_Pendente_Operacional")
    p.markdown("**Valor e quantidade pendente por Linha de Negócio**")
    p.caption(
        "Dado manual (`dim_estrutura`, planilha), não SAP — cobertura parcial. 'NAO ALOCADO' aqui é gap "
        "de cadastro real (intercompany já excluído acima por padrão), diferente de Organização de "
        "Vendas (SAP, sempre 100% preenchida)."
    )
    _resumo_por(p, filtrado, "Linha_Negocio", "Valor_Pendente_Faturamento", "Qtd_Pendente_Operacional")
    p.markdown("**Motivo_Principal x Organização de Vendas**")
    _pivots(p, filtrado, "Nome_Org_Vendas")
    p.markdown("**Motivo_Principal x Linha de Negócio**")
    p.caption(
        "Mesmo caveat da Linha de Negócio acima: dado manual, cobertura parcial — Org Vendas x Linha "
        "de Negócio são dimensões independentes, então as colunas aqui não são as do pivot acima."
    )
    _pivots(p, filtrado, "Linha_Negocio")
    p.divider()

    _ranking_materiais(p, ctx, filtrado)
    p.divider()
    _detalhe_pedidos(p, ctx, filtrado)


def _ranking_materiais(p: Pagina, ctx: Ctx, filtrado: pd.DataFrame) -> None:
    sem_cobertura = filtrado[filtrado["Motivo_Principal"].isin(MOTIVOS_SEM_ESTOQUE)]
    if sem_cobertura.empty:
        p.caption(
            ':material/inventory_2: Sem itens "Sem Estoque"/"Estoque Parcial" no filtro atual — a visão por '
            "material (suprimento) não se aplica aqui. Veja o motivo real no detalhe por pedido abaixo."
        )
        return
    p.header(":material/inventory_2: Visão por material — suprimento")
    p.caption(
        'Aqui a pergunta muda: não é "por que ESTE pedido está pendente" (`Motivo_Principal`, por '
        'pedido+cliente), e sim "por que ESTE MATERIAL não tem estoque, somando TODOS os pedidos que '
        'esperam por ele" (`Causa_Estoque_Material`, por Material+Centro). Útil pra priorizar compra/produção.'
    )
    p.markdown("**Ranking de materiais sem cobertura de estoque (priorizar compra/produção)**")
    p.caption(
        "`Causa_Estoque_Material` cruza o snapshot atual de estoque com o histórico de movimento dos "
        "últimos 24 meses (`SILVER.dataspherev2.mseg`/`mkpf` — aba **Rastreio de Lote** em **Estoque** "
        "pra abrir 1 lote)."
    )
    ranking = (
        sem_cobertura.groupby(["Codigo_Produto", "Descricao_Produto", "Codigo_Centro", "Nome_Centro"])
        .agg(
            Itens_Total=("Numero_Pedido", "count"),
            Qtd_Sem_Estoque=("Qtd_Pendente_Operacional", "sum"),
            Valor_Sem_Estoque=("Valor_Pendente_Faturamento_BRL", "sum"),
            Dias_Pedido_Mais_Antigo=("Dias_Pedido_Mais_Antigo", "first"),
            Qtd_Reservada=("Qtd_Reservada", "first"),
            Possivel_Zumbi=("Possivel_Zumbi", "first"),
        )
        .reset_index()
    )
    ranking = ranking[ranking["Valor_Sem_Estoque"] > 0].sort_values("Valor_Sem_Estoque", ascending=False)
    estoque = _estoque(config("excluir_estoque_internacional")).rename(columns={"Codigo_Material": "Codigo_Produto"})[
        ["Codigo_Produto", "Codigo_Centro", "Qtd_Qualidade", "Qtd_Bloqueado", "Qtd_Disponivel_Venda"]
    ]
    ranking = ranking.merge(estoque, on=["Codigo_Produto", "Codigo_Centro"], how="left").merge(
        _movimento(), on=["Codigo_Produto", "Codigo_Centro"], how="left"
    )
    ranking["Causa_Estoque_Material"] = ranking.apply(_classificar_causa_estoque_material, axis=1)
    for col in ("Data_Ultima_Entrada", "Data_Ultima_Liberacao_Qualidade", "Data_Ultima_Saida", "Data_Ultimo_Movimento"):
        ranking[col] = pd.to_datetime(ranking[col])
    ranking["Dias_Desde_Ultimo_Movimento"] = (pd.Timestamp(_dt.date.today()) - ranking["Data_Ultimo_Movimento"]).dt.days
    if int(ranking["Possivel_Zumbi"].sum()):
        p.caption(
            ":material/warning: A coluna `Possivel_Zumbi` sinaliza Material+Centro suspeito (ver aviso no "
            'topo) — a opção "Incluir possíveis pedidos zumbi" controla se essas linhas aparecem aqui.'
        )
    c, _ = p.columns([1, 1])
    filtro_causa = c.multiselect(
        "Causa_Estoque_Material (filtra o ranking abaixo)", sorted(ranking["Causa_Estoque_Material"].dropna().unique()),
        "pxe_causa", help="Vazio = todos.",
    )
    if filtro_causa:
        ranking = ranking[ranking["Causa_Estoque_Material"].isin(filtro_causa)]

    p.markdown("**Valor e quantidade sem estoque por Causa_Estoque_Material**")
    _resumo_por(p, ranking, "Causa_Estoque_Material", "Valor_Sem_Estoque", "Qtd_Sem_Estoque")

    colunas = [
        "Codigo_Produto", "Descricao_Produto", "Codigo_Centro", "Nome_Centro", "Itens_Total", "Qtd_Sem_Estoque",
        "Valor_Sem_Estoque", "Dias_Pedido_Mais_Antigo", "Qtd_Reservada", "Possivel_Zumbi", "Causa_Estoque_Material",
        "Data_Ultima_Entrada", "Data_Ultima_Saida", "Dias_Desde_Ultimo_Movimento",
    ]
    mostrar = ranking[colunas].reset_index(drop=True)
    mostrar.insert(0, "_chave", mostrar["Codigo_Produto"].astype(str) + "|" + mostrar["Codigo_Centro"].astype(str))
    p.card().metrics(
        [
            ("Materiais (Material+Centro)", fmt.num(len(mostrar))),
            ("Itens de pedido", fmt.num(int(mostrar["Itens_Total"].sum()))),
            ("Qtd sem estoque", fmt.num(mostrar["Qtd_Sem_Estoque"].sum())),
            ("Valor sem estoque (BRL)", fmt.brl(mostrar["Valor_Sem_Estoque"].sum(), 2)),
        ]
    )
    p.caption("Clique numa linha pra filtrar a página inteira nesse Material+Centro (preenche Centro/Material acima).")
    p.card().table(
        mostrar.rename(columns={"_chave": "Material|Centro"}),
        {
            "Qtd_Sem_Estoque": "num0", "Valor_Sem_Estoque": "brl2", "Dias_Desde_Ultimo_Movimento": "num0",
            "Dias_Pedido_Mais_Antigo": "num0", "Qtd_Reservada": "num0",
        },
        selecao="pxe_rank_sel", coluna_selecao="Material|Centro", nome_arquivo="ranking_materiais_sem_estoque",
    )


_COLUNAS_DETALHE = [
    "Numero_Pedido", "Item_Pedido", "Tipo_Ordem_Venda", "Data_Inclusao_Pedido", "Dias_Desde_Inclusao_Pedido",
    "Codigo_Produto", "Descricao_Produto", "Codigo_Centro", "Nome_Centro",
    "Nome_Org_Vendas", "Linha_Negocio", "Codigo_Cliente", "Nome_Cliente", "Qtd_Pendente_Operacional",
    "Moeda", "Valor_Pendente_Faturamento",
    "Motivo_Principal", "Status_Pendencia", "Status_Pendencia_Estoque", "Flag_Totalmente_Faturado",
    "Valor_Credito_Disponivel", "Cliente_Bloqueado",
    "Qtd_Pedida", "Qtd_Remetida", "Qtd_Faturada",
    "Primeira_Data_Remessa", "Ultima_Data_Remessa", "Primeira_Data_Faturamento", "Ultima_Data_Faturamento",
]


def _detalhe_pedidos(p: Pagina, ctx: Ctx, filtrado: pd.DataFrame) -> None:
    p.header(":material/list_alt: Detalhe por pedido")
    p.caption("1 linha por pedido (não por item) — clique num pedido pra ver os itens dele, depois num item pro contexto completo.")

    def _resumo_motivo(serie: pd.Series) -> str:
        valores = serie.unique()
        return valores[0] if len(valores) == 1 else f"Vários ({len(valores)})"

    pedidos = (
        filtrado.groupby("Numero_Pedido")
        .agg(
            Data_Inclusao_Pedido=("Data_Inclusao_Pedido", "min"),
            Tipo_Ordem_Venda=("Tipo_Ordem_Venda", "first"),
            Nome_Cliente=("Nome_Cliente", "first"),
            Nome_Org_Vendas=("Nome_Org_Vendas", "first"),
            Linha_Negocio=("Linha_Negocio", "first"),
            Itens_Total=("Item_Pedido", "count"),
            Qtd_Pendente_Total=("Qtd_Pendente_Operacional", "sum"),
            Valor_Pendente_Total=("Valor_Pendente_Faturamento_BRL", "sum"),
            Motivo_Resumo=("Motivo_Principal", _resumo_motivo),
        )
        .reset_index()
        .sort_values("Data_Inclusao_Pedido", ascending=False)
    )
    p.card().metrics(
        [
            ("Pedidos", fmt.num(len(pedidos))),
            ("Itens", fmt.num(int(pedidos["Itens_Total"].sum()))),
            ("Qtd pendente total", fmt.num(pedidos["Qtd_Pendente_Total"].sum())),
            ("Valor pendente total (BRL)", fmt.brl(pedidos["Valor_Pendente_Total"].sum(), 2)),
        ]
    )
    pedido_sel = ctx.get("pxe_pedido") or ""
    if pedido_sel and pedido_sel not in set(pedidos["Numero_Pedido"].astype(str)):
        pedido_sel = ""
    p.card().table(
        pedidos, {"Valor_Pendente_Total": "brl2", "Qtd_Pendente_Total": "num0"},
        selecao="pxe_pedido", coluna_selecao="Numero_Pedido", selecionado=pedido_sel, nome_arquivo="pedidos_pendentes",
    )
    if not pedido_sel:
        p.caption("Clique num pedido acima pra ver os itens dele.")
        return

    p.markdown(f"**Itens do pedido {pedido_sel}**")
    itens = (
        filtrado[filtrado["Numero_Pedido"].astype(str) == pedido_sel][_COLUNAS_DETALHE]
        .sort_values("Item_Pedido")
        .reset_index(drop=True)
    )
    # A seleção do item carrega o pedido junto: trocar de pedido "esquece" o item anterior.
    itens.insert(0, "_chave", pedido_sel + "|" + itens["Item_Pedido"].astype(str))
    item_sel = ctx.get("pxe_item") or ""
    if not item_sel.startswith(pedido_sel + "|"):
        item_sel = ""
    # Valor_Pendente_Faturamento sem "R$": vem na moeda do pedido (coluna Moeda ao lado).
    p.card().table(
        itens.rename(columns={"_chave": "Pedido|Item"}),
        {"Valor_Pendente_Faturamento": "num2", "Valor_Credito_Disponivel": "brl2"},
        selecao="pxe_item", coluna_selecao="Pedido|Item", selecionado=item_sel, nome_arquivo=f"itens_pedido_{pedido_sel}",
    )
    if not item_sel:
        p.caption("Clique numa linha da tabela de itens acima pra abrir o contexto completo do item.")
        return
    p.lazy("item", altura=320, pedido=pedido_sel, item=item_sel.split("|", 1)[1])


# ── bloco: contexto completo de 1 item ───────────────────────────────────────


def _bloco_item(p: Node, ctx: Ctx) -> None:
    df = _base(_limiar())
    pedido, item = ctx.get("pedido") or "", ctx.get("item") or ""
    linhas = df[(df["Numero_Pedido"].astype(str) == pedido) & (df["Item_Pedido"].astype(str) == item)]
    if linhas.empty:
        p.info("Item não encontrado no backlog aberto (pode ter sido faturado desde a última atualização).")
        return
    linha = linhas.iloc[0]
    p.subheader(
        f"Pedido {linha['Numero_Pedido']} / item {linha['Item_Pedido']} — {linha['Codigo_Produto']} ({linha['Descricao_Produto']})"
    )
    moeda = linha.get("Moeda") or "BRL"
    p.metrics(
        [
            ("Cliente", linha["Nome_Cliente"]),
            ("Motivo_Principal", linha["Motivo_Principal"]),
            ("Data do pedido", fmt.data(linha["Data_Inclusao_Pedido"])),
            (f"Valor pendente ({moeda})", fmt.num(linha["Valor_Pendente_Faturamento"], 2)),
        ]
    )
    p.caption(
        f"Centro {linha['Codigo_Centro']} ({linha['Nome_Centro']}) — Org. Vendas {linha['Nome_Org_Vendas']}. "
        f"`Status_Pendencia`: {linha['Status_Pendencia']}. `Status_Pendencia_Estoque`: {linha['Status_Pendencia_Estoque']}."
    )
    p.expander("Oportunidade (Salesforce)").lazy("oportunidade", altura=90, gatilho="intersect once", pedido=pedido, item=item)
    p.expander("Remessas (entregas) deste item").lazy("remessas", altura=90, gatilho="intersect once", pedido=pedido, item=item)

    if linha["Motivo_Principal"] == "Financeiro (crédito)":
        _credito_do_item(p, linha)
    if linha["Motivo_Principal"] in MOTIVOS_LOGISTICOS:
        _travamento_documento(p, linha)
    _estoque_na_data(p, linha)


def _credito_do_item(p: Node, linha: pd.Series) -> None:
    p.markdown("**Por que está bloqueado financeiramente?**")
    credito = _credito_cliente(str(linha["Codigo_Cliente"]))
    if credito.empty:
        p.caption("Cliente sem linha em `fct_limite_credito_sap` — não achei detalhe de crédito pra ele.")
        return
    colunas = [
        "Area_Controle_Credito", "Classe_Risco_Cliente", "Flag_Cliente_Bloqueado", "Valor_Limite_Credito_Concedido",
        "Valor_Exposicao_Total_SAP", "Valor_Saldo_A_Vencer", "Valor_Saldo_Vencido", "Valor_Saldo_Aberto_Total",
        "Valor_Credito_Disponivel",
    ]
    p.caption(
        f"{len(credito)} área(s) de controle de crédito pra este cliente — o pior caso entre elas é o que "
        f"trava o pedido ({fmt.brl(linha['Valor_Credito_Disponivel'], 2)} disponível, "
        f"{'BLOQUEADO' if linha['Cliente_Bloqueado'] == 1 else 'não bloqueado pela flag'})."
    )
    p.table(credito[colunas], {c: "brl2" for c in colunas if c.startswith("Valor_")}, nome_arquivo="credito_cliente")
    if (credito["Valor_Saldo_Vencido"] > 0).any():
        p.caption(
            ":material/warning: Tem saldo VENCIDO em pelo menos 1 área de crédito — é o motivo mais provável "
            "do bloqueio, não só limite estourado por pedidos em aberto."
        )


def _travamento_documento(p: Node, linha: pd.Series) -> None:
    p.markdown("**Onde está travado: remessa ou fatura?**")
    pedida, remetida, faturada = linha["Qtd_Pedida"], linha["Qtd_Remetida"], linha["Qtd_Faturada"]
    falta_remeter = max(pedida - remetida, 0)
    falta_faturar = max(remetida - faturada, 0)
    p.metrics(
        [
            ("Pedida", fmt.num(pedida)),
            ("Remetida", fmt.num(remetida), f"falta {fmt.num(falta_remeter)}" if falta_remeter else "completa"),
            ("Faturada", fmt.num(faturada), f"falta {fmt.num(falta_faturar)} (do já remetido)" if falta_faturar else "em dia"),
        ]
    )
    if falta_remeter > 0 and falta_faturar > 0:
        texto = (
            f"Falta **remeter {fmt.num(falta_remeter)} un.** (dos {fmt.num(pedida)} pedidos) E falta **faturar "
            f"{fmt.num(falta_faturar)} un.** do que já foi remetido — travado nos dois documentos ao mesmo tempo."
        )
    elif falta_remeter > 0:
        texto = f"Falta **remeter {fmt.num(falta_remeter)} un.** — nada a faturar ainda porque não saiu do depósito."
    elif falta_faturar > 0:
        texto = f"Já foi **tudo remetido** — falta só **faturar {fmt.num(falta_faturar)} un.** que já saíram do depósito."
    else:
        texto = "Remessa e faturamento já batem com o pedido — se ainda aparece pendente, vale conferir `Status_Pendencia` direto."
    p.caption(texto)
    datas = [
        f"{rotulo}: {fmt.data(linha[col])}"
        for rotulo, col in (
            ("1ª remessa", "Primeira_Data_Remessa"), ("última remessa", "Ultima_Data_Remessa"),
            ("1ª fatura", "Primeira_Data_Faturamento"), ("última fatura", "Ultima_Data_Faturamento"),
        )
        if pd.notna(linha[col])
    ]
    p.caption(" · ".join(datas) if datas else "Nunca teve remessa nem fatura registrada pra este item.")


def _estoque_na_data(p: Node, linha: pd.Series) -> None:
    p.markdown("**Tinha estoque na data do pedido? (dado real do SAP)**")
    p.caption(
        "Fonte: `IB_SAPECC.MCHBH` — fechamento de estoque por período, calculado pelo próprio SAP (não é "
        "estimativa). Granularidade de MÊS fechado — usa o fechamento do mês do pedido, ou o mais recente "
        "ANTES dele se o mês ainda não fechou."
    )
    r = _estoque_historico(str(linha["Codigo_Produto"]), str(linha["Codigo_Centro"]), str(linha["Data_Inclusao_Pedido"])[:10])
    qtd_pedida = linha["Qtd_Pendente_Operacional"]
    livre_periodo = r["Qtd_Livre_Periodo"]
    total_periodo = livre_periodo + r["Qtd_Qualidade_Periodo"] + r["Qtd_Bloqueado_Periodo"]
    livre_atual = r["Qtd_Livre_Atual_Real"]
    total_atual = livre_atual + r["Qtd_Qualidade_Atual_Real"] + r["Qtd_Bloqueado_Atual_Real"]
    # Mês do pedido ainda não fechado = pedido do mês corrente: o estoque de HOJE é referência
    # mais atual que o fechamento de 1+ mês antes (achado 2026-09-03, pedido 0000139216).
    usar_hoje = r["Cobertura_Suficiente"] and not r["Periodo_E_Exato"]
    livre_base = livre_atual if usar_hoje else livre_periodo
    total_base = total_atual if usar_hoje else total_periodo
    fonte = "estoque de HOJE (real)" if usar_hoje else f"fechamento de {r['Periodo_Mes']}/{r['Periodo_Ano']}"
    if not r["Cobertura_Suficiente"]:
        p.warning("Nenhum fechamento de período encontrado em MCHBH pra este Material+Centro — sem dado real disponível.")
    elif usar_hoje:
        p.caption(
            f":material/info: Mês do pedido ainda não fechado no SAP — o fechamento mais recente é de "
            f"**{r['Periodo_Mes']}/{r['Periodo_Ano']}**, mas como o pedido é do mês corrente, o **estoque de "
            "hoje (real)** é referência mais atual, e é o que o veredito usa."
        )
    # Só "Livre" é vendável: checar Livre primeiro evita contar estoque preso em Qualidade
    # como disponível (achado 2026-09-03, pedido 0000138952/PA8116).
    if not r["Cobertura_Suficiente"]:
        veredito, tipo = "Não dá pra confirmar — sem fechamento de período disponível pra esse Material+Centro.", "info"
    elif livre_base >= qtd_pedida:
        veredito, tipo = (
            f"**TINHA estoque disponível pra venda** (base: {fonte} — {fmt.num(livre_base)} un. livres, "
            f"pedido pedia {fmt.num(qtd_pedida)}).", "ok",
        )
    elif total_base >= qtd_pedida:
        veredito, tipo = (
            f"Existia estoque fisicamente ({fmt.num(total_base)} un., base: {fonte}), mas a maior parte presa em "
            f"Qualidade/Bloqueado — só {fmt.num(livre_base)} un. estavam **livres pra venda** (pedido pedia "
            f"{fmt.num(qtd_pedida)}). Provavelmente **NÃO estava disponível** pra este pedido.", "aviso",
        )
    else:
        veredito, tipo = (
            f"**NÃO tinha estoque**, nem contando o restrito (base: {fonte} — {fmt.num(total_base)} un. no "
            f"total, pedido pedia {fmt.num(qtd_pedida)}).", "erro",
        )
    a, b = p.columns([2, 1])
    {"ok": a.success, "aviso": a.warning, "erro": a.error, "info": a.info}[tipo](veredito)
    b.metric(f"Livre pra venda — {fonte}", fmt.num(livre_base), f"pedido pedia {fmt.num(qtd_pedida)}")
    b.caption(f"Total físico (Livre+Qualidade+Bloqueado): {fmt.num(total_base)}")
    p.expander("Detalhe por tipo de estoque (Livre/Qualidade/Bloqueado)").metrics(
        [
            ("Livre — fechamento do período", fmt.num(r["Qtd_Livre_Periodo"]), f"hoje real: {fmt.num(r['Qtd_Livre_Atual_Real'])}"),
            ("Qualidade — fechamento do período", fmt.num(r["Qtd_Qualidade_Periodo"]), f"hoje real: {fmt.num(r['Qtd_Qualidade_Atual_Real'])}"),
            ("Bloqueado — fechamento do período", fmt.num(r["Qtd_Bloqueado_Periodo"]), f"hoje real: {fmt.num(r['Qtd_Bloqueado_Atual_Real'])}"),
        ]
    )


def _bloco_oportunidade(p: Node, ctx: Ctx) -> None:
    pedido, item = ctx.get("pedido") or "", ctx.get("item") or ""
    opp = _oportunidade_pedido(pedido)
    if not opp.empty:
        opp = opp[opp["Item_Pedido"].astype(str) == item]
        opp = opp[opp["Nome_Oportunidade"].notna()]
    if opp.empty:
        p.info("Nenhuma Oportunidade vinculada a este item (73% de cobertura medida — ver docs).")
        return
    o = opp.iloc[0]
    p.metrics(
        [
            ("Oportunidade", o["Nome_Oportunidade"]),
            ("Estágio", o["Estagio_Oportunidade"]),
            ("Valor do item (Salesforce)", fmt.brl(o["Valor_Item_Oportunidade"], 2)),
        ]
    )
    p.caption(
        f"Ganha: {'sim' if o['Oportunidade_Ganha'] else 'não'} · Valor total da Oportunidade (todos os itens): "
        f"{fmt.brl(o['Valor_Oportunidade'], 2)} · Criada em {fmt.data(str(o['Data_Criacao_Oportunidade'])[:10])}."
    )


def _bloco_remessas(p: Node, ctx: Ctx) -> None:
    pedido, item = ctx.get("pedido") or "", ctx.get("item") or ""
    rem = _remessas_pedido(pedido)
    if not rem.empty:
        rem = rem[rem["Item_Pedido_Origem"].astype(str) == item]
    if rem.empty:
        p.info("Nenhuma remessa registrada pra este item ainda.")
        return
    colunas = [
        "Numero_Entrega", "Item_Entrega", "Data_Remessa", "Tipo_Remessa", "Codigo_Centro",
        "Charg_Numero_Do_Lote", "Qtd_Remetida", "Peso_Liquido",
    ]
    p.table(rem[[c for c in colunas if c in rem.columns]], nome_arquivo=f"remessas_{pedido}_{item}")


BLOCOS = {"item": _bloco_item, "oportunidade": _bloco_oportunidade, "remessas": _bloco_remessas}


AQUECER = [
    lambda: _base(_limiar()),
    lambda: _estoque(config("excluir_estoque_internacional")),
    _movimento,
]
