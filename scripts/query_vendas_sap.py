"""Consultas prontas sobre GOLD.vendas_sap para investigações de vendas/pendências.

Cada função retorna um pandas DataFrame. Pensado para uso em notebook/REPL:

    from scripts.query_vendas_sap import pendencias_abertas, aging_pendencias
    df = pendencias_abertas()
    df.groupby("Nome_Centro")["Valor_Pendente_Faturamento"].sum().sort_values(ascending=False)

Todas as tabelas fonte e regras de negócio usadas aqui estão documentadas em
docs/CONTEXTO_VENDAS_SAP.md — leia lá antes de confiar cegamente num número,
principalmente as notas sobre Prioridade_Pedido (nunca é 2/3) e Codigo_Vendedor
(hoje vem quase sempre do Salesforce, não do SAP).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pandas as pd

try:
    from scripts.db import read_sql
except ImportError:
    from db import read_sql

SCHEMA = "vendas_sap"

# Canais de distribuição classificados como "Governo" em dim_cliente_sap.Descricao_Canal_Distribuicao
# — reusado em todo filtro Governo x Privado deste módulo (funil, faturamento, pendências, crédito).
CANAIS_GOVERNO = ("GOVERNO / PÚBLICO", "PUBLICO")


def _filtro_periodo_tipo_cliente(
    data_inicio: Optional[date],
    data_fim: Optional[date],
    tipo_cliente: Optional[str],
    params: dict[str, object],
    alias_pedido: str = "p",
) -> tuple[str, str]:
    """Monta JOIN + WHERE extra pra filtrar por período de datas e Governo/Privado.

    Reusado por toda função de `fct_pendencia_sap` que ganhou os filtros globais do
    dashboard (período, tipo_cliente) — ver `pages/1_Pendencias.py`. Só faz o JOIN com
    dim_cliente_sap quando tipo_cliente é passado (Governo/Privado), pra não pagar o custo
    do JOIN em toda consulta sem necessidade.

    Args:
        data_inicio, data_fim: se ambos informados, filtra Data_Inclusao_Pedido no
            intervalo [data_inicio, data_fim] (inclusive). Se algum for None, não filtra
            por data.
        tipo_cliente: "Governo" ou "Privado" (None = sem filtro de tipo de cliente).
        params: dict de parâmetros do SQLAlchemy — mutado in-place com os binds usados.
        alias_pedido: alias da tabela principal (fct_pendencia_sap) na query.

    Returns:
        (join_sql, where_extra_sql) — where_extra_sql já vem prefixado com " AND ...",
        pronto pra concatenar depois de "WHERE {alias}.Flag_Pendencia = 1".
    """
    p = alias_pedido
    join_sql = ""
    condicoes = []
    if data_inicio and data_fim:
        condicoes.append(f"{p}.Data_Inclusao_Pedido BETWEEN :data_inicio AND :data_fim")
        params["data_inicio"] = data_inicio
        params["data_fim"] = data_fim
    if tipo_cliente in ("Governo", "Privado"):
        join_sql = f"""
            LEFT JOIN {SCHEMA}.dim_cliente_sap c
                ON {p}.Mandante = c.Mandante
                AND {p}.Codigo_Cliente = c.Codigo_Cliente
                AND {p}.Codigo_Org_Vendas = c.Org_Vendas
                AND {p}.Codigo_Canal_Distribuicao = c.Canal_Distribuicao
                AND {p}.Codigo_Setor_Atividade = c.Setor_Atividade
        """
        if tipo_cliente == "Governo":
            condicoes.append(f"c.Descricao_Canal_Distribuicao IN {CANAIS_GOVERNO}")
        else:
            condicoes.append(
                f"(c.Descricao_Canal_Distribuicao NOT IN {CANAIS_GOVERNO} OR c.Descricao_Canal_Distribuicao IS NULL)"
            )
    where_extra = "".join(f" AND {c}" for c in condicoes)
    return join_sql, where_extra


def pendencias_abertas(limit: Optional[int] = None) -> pd.DataFrame:
    """Todo o backlog aberto (Flag_Pendencia = 1) de fct_pendencia_sap."""
    top = f"TOP {int(limit)} " if limit else ""
    query = f"""
        SELECT {top}*
        FROM {SCHEMA}.fct_pendencia_sap
        WHERE Flag_Pendencia = 1
        ORDER BY Dias_Desde_Inclusao_Pedido DESC
    """  # nosec B608
    return read_sql(query, database="GOLD")


def aging_pendencias(
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
    tipo_cliente: Optional[str] = None,
) -> pd.DataFrame:
    """Backlog aberto agrupado em faixas de aging (dias desde a inclusão do pedido).

    Args:
        data_inicio, data_fim: se ambos informados, restringe a pedidos incluídos nesse período.
        tipo_cliente: "Governo" ou "Privado" (None = os dois).
    """
    params: dict[str, object] = {}
    join_sql, where_extra = _filtro_periodo_tipo_cliente(
        data_inicio, data_fim, tipo_cliente, params
    )
    query = f"""
        SELECT
            CASE
                WHEN p.Dias_Desde_Inclusao_Pedido <= 7 THEN '0-7 dias'
                WHEN p.Dias_Desde_Inclusao_Pedido <= 15 THEN '8-15 dias'
                WHEN p.Dias_Desde_Inclusao_Pedido <= 30 THEN '16-30 dias'
                WHEN p.Dias_Desde_Inclusao_Pedido <= 60 THEN '31-60 dias'
                ELSE '60+ dias'
            END AS Faixa_Aging,
            COUNT(*) AS Qtd_Itens,
            SUM(p.Qtd_Pendente_Operacional) AS Qtd_Pendente_Total,
            SUM(p.Valor_Pendente_Faturamento) AS Valor_Pendente_Total
        FROM {SCHEMA}.fct_pendencia_sap p
        {join_sql}
        WHERE p.Flag_Pendencia = 1{where_extra}
        GROUP BY
            CASE
                WHEN p.Dias_Desde_Inclusao_Pedido <= 7 THEN '0-7 dias'
                WHEN p.Dias_Desde_Inclusao_Pedido <= 15 THEN '8-15 dias'
                WHEN p.Dias_Desde_Inclusao_Pedido <= 30 THEN '16-30 dias'
                WHEN p.Dias_Desde_Inclusao_Pedido <= 60 THEN '31-60 dias'
                ELSE '60+ dias'
            END
    """  # nosec B608
    return read_sql(query, database="GOLD", params=params)


def pendencia_status_estoque(
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
    tipo_cliente: Optional[str] = None,
) -> pd.DataFrame:
    """Distribuição do backlog por cobertura de estoque (Status_Pendencia_Estoque)."""
    params: dict[str, object] = {}
    join_sql, where_extra = _filtro_periodo_tipo_cliente(
        data_inicio, data_fim, tipo_cliente, params
    )
    query = f"""
        SELECT
            p.Status_Pendencia_Estoque,
            COUNT(*) AS Qtd_Itens,
            SUM(p.Qtd_Pendente_Operacional) AS Qtd_Pendente_Total,
            SUM(p.Valor_Pendente_Faturamento) AS Valor_Pendente_Total
        FROM {SCHEMA}.fct_pendencia_sap p
        {join_sql}
        WHERE p.Flag_Pendencia = 1{where_extra}
        GROUP BY p.Status_Pendencia_Estoque
        ORDER BY Valor_Pendente_Total DESC
    """  # nosec B608
    return read_sql(query, database="GOLD", params=params)


def top_clientes_pendentes(
    n: int = 20,
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
    tipo_cliente: Optional[str] = None,
) -> pd.DataFrame:
    """Top N clientes por valor financeiro pendente de faturamento."""
    params: dict[str, object] = {}
    join_sql, where_extra = _filtro_periodo_tipo_cliente(
        data_inicio, data_fim, tipo_cliente, params
    )
    query = f"""
        SELECT TOP {int(n)}
            p.Codigo_Cliente,
            p.Nome_Cliente,
            COUNT(*) AS Qtd_Itens_Pendentes,
            SUM(p.Qtd_Pendente_Operacional) AS Qtd_Pendente_Total,
            SUM(p.Valor_Pendente_Faturamento) AS Valor_Pendente_Total
        FROM {SCHEMA}.fct_pendencia_sap p
        {join_sql}
        WHERE p.Flag_Pendencia = 1{where_extra}
        GROUP BY p.Codigo_Cliente, p.Nome_Cliente
        ORDER BY Valor_Pendente_Total DESC
    """  # nosec B608
    return read_sql(query, database="GOLD", params=params)


def alocacao_virtual_fifo(codigo_centro: Optional[str] = None) -> pd.DataFrame:
    """Simulação FIFO de alocação de estoque por pedido (fct_pendencia_status_sap).

    Status_Alocacao_Virtual possíveis: 'EM REMESSA', 'SEM ESTOQUE', 'CARIMBAGEM'
    (hoje nunca ocorre — depende de Prioridade_Pedido IN (2,3), que nunca acontece
    nesta base), 'EXC_COMERCIAL'.
    """
    where = "WHERE Codigo_Centro = :centro" if codigo_centro else ""
    query = f"""
        SELECT *
        FROM {SCHEMA}.fct_pendencia_status_sap
        {where}
        ORDER BY Codigo_Produto, Codigo_Centro, Posicao_Fila_Prioridade
    """  # nosec B608
    params = {"centro": codigo_centro} if codigo_centro else None
    return read_sql(query, database="GOLD", params=params)


def faturamento_periodo(data_inicio: str, data_fim: str) -> pd.DataFrame:
    """Itens de faturamento (notas fiscais) emitidos no período [data_inicio, data_fim].

    Datas no formato 'YYYY-MM-DD'.
    """
    query = f"""
        SELECT *
        FROM {SCHEMA}.fct_faturamento_itens_sap
        WHERE Data_Faturamento BETWEEN :inicio AND :fim
        ORDER BY Data_Faturamento
    """  # nosec B608
    return read_sql(query, database="GOLD", params={"inicio": data_inicio, "fim": data_fim})


def correlacao_oportunidade_pedido_pendencia_fatura(
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
    apenas_pendentes: bool = True,
    numero_pedido: Optional[str] = None,
    nome_cliente: Optional[str] = None,
    tipo_cliente: Optional[str] = None,
    limit: int = 20000,
) -> pd.DataFrame:
    """Correlaciona Oportunidade (Salesforce) -> Pedido -> Pendência -> Faturado.

    Preenche a lacuna citada em docs/CONTEXTO_VENDAS_SAP.md §9: não existe hoje um model
    Gold que concilie Salesforce com `vendas_sap`, então a junção é feita aqui em pandas
    (mesmo padrão de `scripts/trace_pedido.py`) — uma query em GOLD.vendas_sap.fct_pendencia_sap
    (que já traz pedido+pendente+faturado agregados por Pedido+Item) e outra em
    SILVER.salesforce.OpportunityLineItem/Opportunity, ligadas pelo elo documentado no §8
    (`Ordem_de_Venda_Sap__c`/`ItemNumero__c`).

    Uma linha = Pedido+Item. Cobertura de Oportunidade não é 100% (medida em ~73% em
    2026-08-13, ver §6.1) — pedidos sem match aparecem com as colunas de Oportunidade em
    branco, não são descartados.

    Duas colunas de valor de Oportunidade, propositalmente diferentes: `Valor_Oportunidade`
    é `Opportunity.amount` (total do negócio inteiro, repetido em toda linha daquela
    Oportunidade — não comparável 1:1 com `Valor_Liquido_Pedido`); `Valor_Item_Oportunidade`
    é `OpportunityLineItem.TotalPrice` (valor do item específico, esse sim comparável 1:1).
    Comparar `Valor_Oportunidade` (cabeçalho) contra `Valor_Liquido_Pedido` (item) sempre vai
    divergir para Oportunidades com mais de um item — não é anomalia de negócio, é grão errado.

    O retorno **não é paginado/amostrado** para exibição — traz todas as linhas que batem
    com o filtro (até `limit`), porque totais (soma de valor/quantidade, % com Oportunidade)
    precisam ser calculados sobre a população inteira do período, não sobre uma amostra. Se
    a UI quiser mostrar só as N linhas mais recentes numa tabela, deve fatiar o DataFrame
    retornado (`df.head(n)`) depois de já ter calculado os totais em cima do `df` completo —
    nunca reduzir `limit` para controlar quantas linhas aparecem numa tela.

    Args:
        data_inicio, data_fim: período de Data_Inclusao_Pedido. Se algum for None, usa o
            default (últimos 90 dias corridos até hoje).
        apenas_pendentes: se True, restringe a Flag_Pendencia = 1 (backlog aberto). Se
            False, inclui itens já totalmente faturados dentro do período.
        numero_pedido: filtra um pedido específico (com ou sem zeros à esquerda).
        nome_cliente: filtra por trecho do nome do cliente (LIKE, case-insensitive).
        tipo_cliente: "Governo" (canal de distribuição 'GOVERNO / PÚBLICO' ou 'PUBLICO',
            via `dim_cliente_sap.Descricao_Canal_Distribuicao`) ou "Privado" (qualquer outro
            canal, incluindo sem match de dimensão). None traz os dois.
        limit: teto de segurança (não um controle de paginação de UI) para evitar que um
            período muito amplo combinado com `apenas_pendentes=False` traga a tabela inteira.
            Se `len(df) == limit`, o resultado pode estar truncado — o chamador deve checar
            isso e avisar o usuário, já que a partir daí os totais deixam de ser exatos.

    Nota de performance: o lado Salesforce é buscado por **filtro de data** (não por
    `WHERE ... IN (<centenas de pedidos>)`) porque medido na prática um `IN` com ~200
    literais contra `Ordem_de_Venda_Sap__c`/`id` nesse warehouse leva 25-85s (plano sem
    índice utilizável), contra ~5s pra um filtro de data trazendo 100-200 mil linhas — a
    junção com o pedido é feita depois, em pandas. Consequência: uma Oportunidade criada
    muito antes do início do período de busca (`data_inicio` - 730 dias de folga) não é
    encontrada; para investigar um pedido específico antigo, use `numero_pedido` (path de
    busca exata, sem esse limite).
    """
    if data_fim is None:
        data_fim = date.today()
    if data_inicio is None:
        data_inicio = data_fim - timedelta(days=90)

    filtros = []
    params: dict[str, object] = {}
    if numero_pedido:
        # Busca exata por pedido: ignora o período de propósito (o pedido pode ser mais
        # antigo que o período padrão) — sem isso, buscar um pedido antigo com o período
        # global de 30 dias (default do dashboard) simplesmente não achava nada.
        filtros.append("p.Numero_Pedido = :numero_pedido")
        params["numero_pedido"] = numero_pedido.strip().zfill(10)
    else:
        filtros.append("p.Data_Inclusao_Pedido BETWEEN :data_inicio AND :data_fim")
        params["data_inicio"] = data_inicio
        params["data_fim"] = data_fim
    if apenas_pendentes:
        filtros.append("p.Flag_Pendencia = 1")
    if nome_cliente:
        filtros.append("p.Nome_Cliente LIKE :nome_cliente")
        params["nome_cliente"] = f"%{nome_cliente.strip()}%"
    # CANAIS_GOVERNO é uma constante fixa do código (não input externo) — seguro interpolar
    # direto; bind param nomeado não expande bem uma tupla numa cláusula IN via SQLAlchemy text().
    if tipo_cliente == "Governo":
        filtros.append(f"c.Descricao_Canal_Distribuicao IN {CANAIS_GOVERNO}")
    elif tipo_cliente == "Privado":
        filtros.append(
            f"(c.Descricao_Canal_Distribuicao NOT IN {CANAIS_GOVERNO} OR c.Descricao_Canal_Distribuicao IS NULL)"
        )
    where = "WHERE " + " AND ".join(filtros)

    # JOIN com dim_cliente_sap (canal governo/público vs privado) e com um resumo de
    # fct_limite_credito_sap (cliente bloqueado por crédito, agregado por Codigo_Cliente
    # porque o grão original é Cliente+Área de Crédito — sem agregar, duplicaria linha de
    # pedido por área). Mesma database (GOLD) nos dois casos, então é um JOIN normal, sem o
    # problema de performance do IN (...) contra o Salesforce descrito na nota acima.
    pedido_query = f"""
        SELECT TOP {int(limit)}
            p.Numero_Pedido, p.Item_Pedido, p.Data_Inclusao_Pedido,
            p.Codigo_Cliente, p.Nome_Cliente, p.Codigo_Produto, p.Descricao_Produto, p.Nome_Centro,
            p.Valor_Liquido_Pedido, p.Valor_Liquido_Faturado, p.Valor_Pendente_Faturamento,
            p.Qtd_Pedida, p.Qtd_Faturada, p.Qtd_Pendente_Operacional,
            p.Status_Faturamento, p.Status_Pendencia, p.Flag_Pendencia,
            p.Primeira_Data_Faturamento, p.Dias_Desde_Inclusao_Pedido,
            c.Descricao_Canal_Distribuicao,
            CASE WHEN c.Descricao_Canal_Distribuicao IN {CANAIS_GOVERNO} THEN 'Governo' ELSE 'Privado' END AS Tipo_Cliente,
            ISNULL(cr.Cliente_Bloqueado, 0) AS Cliente_Bloqueado
        FROM {SCHEMA}.fct_pendencia_sap p
        LEFT JOIN {SCHEMA}.dim_cliente_sap c
            ON p.Mandante = c.Mandante
            AND p.Codigo_Cliente = c.Codigo_Cliente
            AND p.Codigo_Org_Vendas = c.Org_Vendas
            AND p.Codigo_Canal_Distribuicao = c.Canal_Distribuicao
            AND p.Codigo_Setor_Atividade = c.Setor_Atividade
        LEFT JOIN (
            SELECT Mandante, Codigo_Cliente,
                   MAX(CASE WHEN Flag_Cliente_Bloqueado = 'X' THEN 1 ELSE 0 END) AS Cliente_Bloqueado
            FROM {SCHEMA}.fct_limite_credito_sap
            GROUP BY Mandante, Codigo_Cliente
        ) cr ON p.Mandante = cr.Mandante AND p.Codigo_Cliente = cr.Codigo_Cliente
        {where}
        ORDER BY p.Data_Inclusao_Pedido DESC
    """  # nosec B608
    df_pedido = read_sql(pedido_query, database="GOLD", params=params)
    df_pedido["Cliente_Bloqueado"] = df_pedido["Cliente_Bloqueado"].astype(bool)

    colunas_opp = [
        "Nome_Oportunidade",
        "Estagio_Oportunidade",
        "Oportunidade_Ganha",
        "Valor_Oportunidade",
        "Valor_Item_Oportunidade",
        "Data_Criacao_Oportunidade",
        "Data_Fechamento_Oportunidade",
    ]
    if df_pedido.empty:
        return df_pedido.assign(**{col: pd.Series(dtype="object") for col in colunas_opp})

    # Chave de junção: Numero_Pedido/Ordem_de_Venda_Sap__c têm zero-padding diferente do
    # de Item_Pedido/ItemNumero__c (10 vs 6 dígitos) — normaliza pra int nos dois lados.
    df_pedido["_pedido_num"] = (
        df_pedido["Numero_Pedido"].astype(str).str.lstrip("0").replace("", "0").astype(int)
    )
    df_pedido["_item_num"] = (
        df_pedido["Item_Pedido"].astype(str).str.lstrip("0").replace("", "0").astype(int)
    )

    sf_data_inicio = data_inicio - timedelta(days=730)
    if numero_pedido:
        # Busca de um único pedido: equality direta é barata, sem precisar do filtro de data.
        oli_query = """
            SELECT OpportunityId, Ordem_de_Venda_Sap__c, ItemNumero__c, TotalPrice
            FROM salesforce.OpportunityLineItem
            WHERE RTRIM(LTRIM(Ordem_de_Venda_Sap__c)) = :numero_pedido
        """  # nosec B608
        df_oli = read_sql(
            oli_query, database="SILVER", params={"numero_pedido": params["numero_pedido"]}
        )
    else:
        oli_query = """
            SELECT OpportunityId, Ordem_de_Venda_Sap__c, ItemNumero__c, TotalPrice
            FROM salesforce.OpportunityLineItem
            WHERE CreatedDate >= :sf_data_inicio
              AND Ordem_de_Venda_Sap__c IS NOT NULL
              AND RTRIM(Ordem_de_Venda_Sap__c) != ''
        """  # nosec B608
        df_oli = read_sql(oli_query, database="SILVER", params={"sf_data_inicio": sf_data_inicio})

    if df_oli.empty:
        return df_pedido.drop(columns=["_pedido_num", "_item_num"]).assign(
            **{col: pd.Series(dtype="object") for col in colunas_opp}
        )

    # Ordem_de_Venda_Sap__c nem sempre é um VBELN puro (achado: valores como 'CO48290' —
    # pedido de outro sistema/legado) — to_numeric(errors="coerce") descarta essas linhas
    # em vez de estourar exceção.
    df_oli["_pedido_num"] = pd.to_numeric(
        df_oli["Ordem_de_Venda_Sap__c"].astype(str).str.lstrip("0"), errors="coerce"
    )
    df_oli["_item_num"] = pd.to_numeric(
        df_oli["ItemNumero__c"].astype(str).str.lstrip("0"), errors="coerce"
    )
    df_oli = df_oli.dropna(subset=["_pedido_num", "_item_num"])
    df_oli["_pedido_num"] = df_oli["_pedido_num"].astype(int)
    df_oli["_item_num"] = df_oli["_item_num"].astype(int)
    # Restringe aos pedidos que de fato estão no resultado antes de seguir pro merge com
    # Opportunity — evita carregar/mergear oportunidades irrelevantes da janela ampla.
    df_oli = df_oli[df_oli["_pedido_num"].isin(df_pedido["_pedido_num"])]

    opp_ids = set(df_oli["OpportunityId"].dropna().unique().tolist())
    if not opp_ids:
        return df_pedido.drop(columns=["_pedido_num", "_item_num"]).assign(
            **{col: pd.Series(dtype="object") for col in colunas_opp}
        )

    if numero_pedido:
        opp_query = """
            SELECT id, name, stage_name, is_won, amount, created_date, close_date
            FROM salesforce.Opportunity
            WHERE id = :opp_id
        """  # nosec B608
        df_opp = pd.concat(
            [read_sql(opp_query, database="SILVER", params={"opp_id": oid}) for oid in opp_ids],
            ignore_index=True,
        )
    else:
        opp_query = """
            SELECT id, name, stage_name, is_won, amount, created_date, close_date
            FROM salesforce.Opportunity
            WHERE created_date >= :sf_data_inicio
        """  # nosec B608
        df_opp_janela = read_sql(
            opp_query, database="SILVER", params={"sf_data_inicio": sf_data_inicio}
        )
        df_opp = df_opp_janela[df_opp_janela["id"].isin(opp_ids)]

    df_oli = df_oli.merge(df_opp, left_on="OpportunityId", right_on="id", how="left")
    df_oli = df_oli.drop_duplicates(subset=["_pedido_num", "_item_num"])
    df_oli = df_oli.rename(
        columns={
            "name": "Nome_Oportunidade",
            "stage_name": "Estagio_Oportunidade",
            "is_won": "Oportunidade_Ganha",
            "amount": "Valor_Oportunidade",
            "TotalPrice": "Valor_Item_Oportunidade",
            "created_date": "Data_Criacao_Oportunidade",
            "close_date": "Data_Fechamento_Oportunidade",
        }
    )

    df_pedido = df_pedido.merge(
        df_oli[["_pedido_num", "_item_num", *colunas_opp]],
        on=["_pedido_num", "_item_num"],
        how="left",
    )
    return df_pedido.drop(columns=["_pedido_num", "_item_num"])


def _condicao_tipo_cliente_por_codigo(
    tipo_cliente: Optional[str], alias_codigo_cliente: str
) -> tuple[str, str]:
    """JOIN + condição pra filtrar Governo/Privado quando só temos Codigo_Cliente solto (sem
    Org_Vendas/Canal/Setor pra bater na chave composta de dim_cliente_sap, como em
    `_filtro_periodo_tipo_cliente`) — classifica por MAX(canal=governo) agregado por cliente,
    pra não gerar fanout com o grão real da dimensão (Cliente+OrgVendas+Canal+Setor, um
    cliente pode aparecer em vários canais/organizações).
    """
    if tipo_cliente not in ("Governo", "Privado"):
        return "", ""
    join_sql = f"""
        LEFT JOIN (
            SELECT Codigo_Cliente,
                   MAX(CASE WHEN Descricao_Canal_Distribuicao IN {CANAIS_GOVERNO} THEN 1 ELSE 0 END) AS is_governo
            FROM {SCHEMA}.dim_cliente_sap
            GROUP BY Codigo_Cliente
        ) tc ON CAST({alias_codigo_cliente} AS BIGINT) = CAST(tc.Codigo_Cliente AS BIGINT)
    """
    condicao = (
        " AND tc.is_governo = 1"
        if tipo_cliente == "Governo"
        else " AND (tc.is_governo = 0 OR tc.is_governo IS NULL)"
    )
    return join_sql, condicao


def credito_disponivel_clientes(
    apenas_bloqueados: bool = False, tipo_cliente: Optional[str] = None
) -> pd.DataFrame:
    """Limite/exposição de crédito por cliente (fct_limite_credito_sap)."""
    join_sql, condicao_tipo = _condicao_tipo_cliente_por_codigo(tipo_cliente, "cr.Codigo_Cliente")
    filtros = []
    if apenas_bloqueados:
        filtros.append("cr.Flag_Cliente_Bloqueado = 'X'")
    where = ("WHERE " + " AND ".join(filtros)) if filtros else ""
    if condicao_tipo:
        where = f"{where}{condicao_tipo}" if where else f"WHERE 1=1{condicao_tipo}"
    query = f"""
        SELECT cr.*
        FROM {SCHEMA}.fct_limite_credito_sap cr
        {join_sql}
        {where}
        ORDER BY Valor_Credito_Disponivel ASC
    """  # nosec B608
    return read_sql(query, database="GOLD")


def pendencia_por_tipo_ordem_venda(
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
    tipo_cliente: Optional[str] = None,
) -> pd.DataFrame:
    """Backlog aberto (Flag_Pendencia = 1) quebrado por Tipo_Ordem_Venda (SAP AUART)."""
    params: dict[str, object] = {}
    join_sql, where_extra = _filtro_periodo_tipo_cliente(
        data_inicio, data_fim, tipo_cliente, params
    )
    query = f"""
        SELECT
            p.Tipo_Ordem_Venda,
            COUNT(*) AS Qtd_Itens,
            SUM(p.Qtd_Pendente_Operacional) AS Qtd_Pendente_Total,
            SUM(p.Valor_Pendente_Faturamento) AS Valor_Pendente_Total
        FROM {SCHEMA}.fct_pendencia_sap p
        {join_sql}
        WHERE p.Flag_Pendencia = 1{where_extra}
        GROUP BY p.Tipo_Ordem_Venda
        ORDER BY Valor_Pendente_Total DESC
    """  # nosec B608
    return read_sql(query, database="GOLD", params=params)


# Tipo_Material de produto acabado em dim_material_sap: ZFER = "PRODUTO TERMINADO"
# (o grosso, 1.802 materiais), ZPFA = "PROD TERMINAD IFA BIOTECH" (variante biotech, 10
# materiais) — todo o resto (ZROH matéria-prima, ZEMB embalagem, ZHAL granel, consumíveis
# etc.) é considerado "não acabado" aqui.
TIPOS_MATERIAL_PRODUTO_ACABADO = ("ZFER", "ZPFA")


# Moeda por país do centro (achado 2026-08-25, ver T001 no data-platform): BWKEY em MBEW =
# Codigo_Centro aqui, e a maioria dos centros é BR/BRL, mas 2000/2100/2400/2500/2600/2700
# (Montevidéu/Canelones, UY) valoram em UYU e CO10 (Colômbia) em COP — `Valor_Custo_Unitario`
# não converte pra BRL, então somar tudo junto mistura moeda. Não há tabela de câmbio (TCURR)
# disponível nesta base pra converter de verdade — por enquanto só sinalizamos.
MOEDA_POR_PAIS_CENTRO = {"BR": "BRL", "UY": "UYU", "CO": "COP", "DE": "EUR"}


def _moeda_case_sql(alias_pais: str = "c.Pais_Centro") -> str:
    when_clauses = " ".join(
        f"WHEN '{pais}' THEN '{moeda}'" for pais, moeda in MOEDA_POR_PAIS_CENTRO.items()
    )
    return f"CASE {alias_pais} {when_clauses} ELSE 'Desconhecida' END"


def estoque_restrito_disponivel(
    codigo_centro: Optional[str] = None,
    produto_acabado: Optional[bool] = None,
    pais_centro: Optional[str] = None,
    limit: int = 500,
) -> pd.DataFrame:
    """Estoque por Material+Centro, quebrando livre/qualidade/bloqueado/disponível pra venda.

    Fonte: `GOLD.vendas_sap.fct_estoque_lote_sap` (grão Material+Centro+Depósito+Lote),
    agregado aqui pra Material+Centro. `Qtd_Estoque_Bloqueado`/`Qtd_Estoque_Qualidade` são o
    "restrito" (não vendável agora); `Qtd_Disponivel_Venda` é o que já passou por todas as
    checagens e pode ser alocado num pedido.

    Traz também `Status_Material` (`ATIVO`/`MARCADO PARA EXCLUSAO` — material marcado pra
    descontinuar no cadastro SAP) e `Descricao_Status_Global_Material` (bloqueio de
    suprimento/depósito/roteiro, via `dim_material_sap`) — pra achar estoque de produto já
    sinalizado como "não vai mais ser usado", não só o que está fisicamente bloqueado no lote.
    E `Moeda` (ver `MOEDA_POR_PAIS_CENTRO`) — **não confie em `Valor_Financeiro_Estoque`
    pra centro fora de `Moeda='BRL'`**, o valor está na moeda local, não convertido pra Real.

    Args:
        codigo_centro: filtra um centro específico.
        produto_acabado: True = só produto acabado (`Tipo_Material` in
            `TIPOS_MATERIAL_PRODUTO_ACABADO`, via `dim_material_sap`); False = só o resto
            (matéria-prima, embalagem, granel, consumíveis etc.); None = os dois.
        pais_centro: filtra por país do centro (`dim_centro_sap.Pais_Centro`, ex.: 'BR',
            'UY', 'CO') — None traz todos os países.
        limit: teto de linhas (Material+Centro).
    """
    filtros = []
    params: dict[str, object] = {}
    if codigo_centro:
        filtros.append("e.Codigo_Centro = :centro")
        params["centro"] = codigo_centro
    if pais_centro:
        filtros.append("c.Pais_Centro = :pais_centro")
        params["pais_centro"] = pais_centro
    if produto_acabado is True:
        filtros.append(f"m.Tipo_Material IN {TIPOS_MATERIAL_PRODUTO_ACABADO}")
    elif produto_acabado is False:
        filtros.append(
            f"(m.Tipo_Material NOT IN {TIPOS_MATERIAL_PRODUTO_ACABADO} OR m.Tipo_Material IS NULL)"
        )
    where = ("WHERE " + " AND ".join(filtros)) if filtros else ""
    moeda_case = _moeda_case_sql()
    query = f"""
        SELECT TOP {int(limit)}
            e.Codigo_Material, e.Descricao_Material, e.Codigo_Centro, c.Nome_Centro, c.Pais_Centro,
            {moeda_case} AS Moeda,
            m.Tipo_Material, m.Descricao_Tipo_Material,
            CASE WHEN m.Tipo_Material IN {TIPOS_MATERIAL_PRODUTO_ACABADO} THEN 'Acabado' ELSE 'Não Acabado' END AS Produto_Acabado,
            COALESCE(m.Status_Material, 'NAO CADASTRADO') AS Status_Material,
            COALESCE(m.Descricao_Status_Global_Material, 'NAO CADASTRADO') AS Descricao_Status_Global_Material,
            SUM(e.Qtd_Estoque_Livre) AS Qtd_Livre,
            SUM(e.Qtd_Estoque_Qualidade) AS Qtd_Qualidade,
            SUM(e.Qtd_Estoque_Bloqueado) AS Qtd_Bloqueado,
            SUM(e.Qtd_Estoque_Transferencia) AS Qtd_Transferencia,
            SUM(e.Qtd_Estoque_Reservada) AS Qtd_Reservada,
            SUM(e.Qtd_Disponivel_Venda) AS Qtd_Disponivel_Venda,
            SUM(e.Qtd_Estoque_Fisico_Total) AS Qtd_Fisico_Total,
            SUM(e.Valor_Financeiro_Estoque) AS Valor_Financeiro_Estoque
        FROM {SCHEMA}.fct_estoque_lote_sap e
        LEFT JOIN (SELECT DISTINCT Codigo_Centro, Nome_Centro, Pais_Centro FROM {SCHEMA}.dim_centro_sap) c
            ON e.Codigo_Centro = c.Codigo_Centro
        LEFT JOIN {SCHEMA}.dim_material_sap m
            ON e.Mandante = m.Mandante AND e.Codigo_Material = m.Codigo_Produto
        {where}
        GROUP BY e.Codigo_Material, e.Descricao_Material, e.Codigo_Centro, c.Nome_Centro, c.Pais_Centro,
            m.Tipo_Material, m.Descricao_Tipo_Material, m.Status_Material, m.Descricao_Status_Global_Material
        HAVING SUM(e.Qtd_Estoque_Fisico_Total) > 0
        ORDER BY Valor_Financeiro_Estoque DESC
    """  # nosec B608
    return read_sql(query, database="GOLD", params=params)


def estoque_validade_resumo(
    codigo_centro: Optional[str] = None,
    produto_acabado: Optional[bool] = None,
    pais_centro: Optional[str] = None,
) -> pd.DataFrame:
    """Totais de estoque por faixa de validade (Vencido, 0-30/31-90/91-180/180+ dias).

    Agregado em SQL (sem teto de linhas) — use pra métricas/gráfico de resumo.
    `estoque_validade()` traz o detalhe lote a lote (aí sim com teto), ordenado pelos mais
    urgentes primeiro; combinar os dois é o mesmo padrão de `aging_pendencias()` +
    `pendencias_abertas()` pro backlog.

    `Valor_Financeiro_Estoque`: se `pais_centro` for informado, soma tudo (já é 1 moeda só,
    a do país filtrado — ver `MOEDA_POR_PAIS_CENTRO`); se não, soma só centros BRL pra não
    misturar moeda (ver nota em `estoque_totais()`). `Qtd_Fisico_Total`/`Qtd_Lotes` sempre
    somam todos os centros do filtro, independente de moeda.
    """
    filtros = [
        "e.Qtd_Estoque_Fisico_Total > 0",
        "e.Data_Validade IS NOT NULL",
        "e.Data_Validade <> '2999-12-31'",
    ]
    params: dict[str, object] = {}
    if codigo_centro:
        filtros.append("e.Codigo_Centro = :centro")
        params["centro"] = codigo_centro
    if pais_centro:
        filtros.append("c.Pais_Centro = :pais_centro")
        params["pais_centro"] = pais_centro
    if produto_acabado is True:
        filtros.append(f"m.Tipo_Material IN {TIPOS_MATERIAL_PRODUTO_ACABADO}")
    elif produto_acabado is False:
        filtros.append(
            f"(m.Tipo_Material NOT IN {TIPOS_MATERIAL_PRODUTO_ACABADO} OR m.Tipo_Material IS NULL)"
        )
    where = "WHERE " + " AND ".join(filtros)
    faixa_case = """
        CASE
            WHEN e.Data_Validade < CAST(GETDATE() AS date) THEN 'Vencido'
            WHEN DATEDIFF(day, CAST(GETDATE() AS date), e.Data_Validade) <= 30 THEN '0-30 dias'
            WHEN DATEDIFF(day, CAST(GETDATE() AS date), e.Data_Validade) <= 90 THEN '31-90 dias'
            WHEN DATEDIFF(day, CAST(GETDATE() AS date), e.Data_Validade) <= 180 THEN '91-180 dias'
            ELSE '180+ dias'
        END
    """
    moeda_case = _moeda_case_sql("c.Pais_Centro")
    valor_expr = (
        "e.Valor_Financeiro_Estoque"
        if pais_centro
        else f"CASE WHEN {moeda_case} = 'BRL' THEN e.Valor_Financeiro_Estoque ELSE 0 END"
    )
    query = f"""
        SELECT
            {faixa_case} AS Faixa_Validade,
            COUNT(*) AS Qtd_Lotes,
            SUM(e.Qtd_Estoque_Fisico_Total) AS Qtd_Fisico_Total,
            SUM({valor_expr}) AS Valor_Financeiro_Estoque
        FROM {SCHEMA}.fct_estoque_lote_sap e
        LEFT JOIN (SELECT DISTINCT Codigo_Centro, Pais_Centro FROM {SCHEMA}.dim_centro_sap) c
            ON e.Codigo_Centro = c.Codigo_Centro
        LEFT JOIN {SCHEMA}.dim_material_sap m
            ON e.Mandante = m.Mandante AND e.Codigo_Material = m.Codigo_Produto
        {where}
        GROUP BY {faixa_case}
    """  # nosec B608
    return read_sql(query, database="GOLD", params=params)


def estoque_validade(
    codigo_centro: Optional[str] = None,
    produto_acabado: Optional[bool] = None,
    pais_centro: Optional[str] = None,
    limit: int = 2000,
) -> pd.DataFrame:
    """Estoque por lote (não agregado) com data de validade, pra achar produto vencido/a vencer.

    Grão: Material+Centro+Lote — diferente de `estoque_restrito_disponivel()` (agregado em
    Material+Centro), porque validade varia por lote e agregar perderia essa informação.

    Descarta lotes sem `Data_Validade` (achado documentado em docs/CONTEXTO_VENDAS_SAP.md
    §6.4: SAP grava `'00000000'` quando validade não se aplica ao material, vira NULL via
    `TRY_CAST` — normal pra embalagem/material de manutenção, não é erro) e o sentinela SAP
    `'2999-12-31'` (~1 lote, "sem vencimento definido" — não é uma validade real).

    Args:
        codigo_centro: filtra um centro específico.
        produto_acabado: True = só produto acabado, False = só o resto, None = os dois
            (mesma classificação de `estoque_restrito_disponivel`).
        pais_centro: filtra por país do centro (ex.: 'BR', 'UY', 'CO') — None traz todos.
        limit: teto de linhas (lotes), ordenado por validade mais próxima primeiro.
    """
    filtros = [
        "e.Qtd_Estoque_Fisico_Total > 0",
        "e.Data_Validade IS NOT NULL",
        "e.Data_Validade <> '2999-12-31'",
    ]
    params: dict[str, object] = {}
    if codigo_centro:
        filtros.append("e.Codigo_Centro = :centro")
        params["centro"] = codigo_centro
    if pais_centro:
        filtros.append("c.Pais_Centro = :pais_centro")
        params["pais_centro"] = pais_centro
    if produto_acabado is True:
        filtros.append(f"m.Tipo_Material IN {TIPOS_MATERIAL_PRODUTO_ACABADO}")
    elif produto_acabado is False:
        filtros.append(
            f"(m.Tipo_Material NOT IN {TIPOS_MATERIAL_PRODUTO_ACABADO} OR m.Tipo_Material IS NULL)"
        )
    where = "WHERE " + " AND ".join(filtros)
    moeda_case = _moeda_case_sql("c.Pais_Centro")
    query = f"""
        SELECT TOP {int(limit)}
            e.Codigo_Material, e.Descricao_Material, e.Codigo_Centro, c.Nome_Centro, {moeda_case} AS Moeda,
            e.Numero_Lote, e.Data_Producao, e.Data_Validade,
            DATEDIFF(day, CAST(GETDATE() AS date), e.Data_Validade) AS Dias_Para_Vencer,
            CASE
                WHEN e.Data_Validade < CAST(GETDATE() AS date) THEN 'Vencido'
                WHEN DATEDIFF(day, CAST(GETDATE() AS date), e.Data_Validade) <= 30 THEN '0-30 dias'
                WHEN DATEDIFF(day, CAST(GETDATE() AS date), e.Data_Validade) <= 90 THEN '31-90 dias'
                WHEN DATEDIFF(day, CAST(GETDATE() AS date), e.Data_Validade) <= 180 THEN '91-180 dias'
                ELSE '180+ dias'
            END AS Faixa_Validade,
            m.Tipo_Material, m.Descricao_Tipo_Material,
            COALESCE(m.Status_Material, 'NAO CADASTRADO') AS Status_Material,
            e.Qtd_Estoque_Fisico_Total, e.Valor_Financeiro_Estoque
        FROM {SCHEMA}.fct_estoque_lote_sap e
        LEFT JOIN (SELECT DISTINCT Codigo_Centro, Nome_Centro, Pais_Centro FROM {SCHEMA}.dim_centro_sap) c
            ON e.Codigo_Centro = c.Codigo_Centro
        LEFT JOIN {SCHEMA}.dim_material_sap m
            ON e.Mandante = m.Mandante AND e.Codigo_Material = m.Codigo_Produto
        {where}
        ORDER BY e.Data_Validade ASC
    """  # nosec B608
    return read_sql(query, database="GOLD", params=params)


def devolucoes_credito_motivo(
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
    excluir_faturamento_rotina: bool = True,
    nome_cliente: Optional[str] = None,
    tipo_cliente: Optional[str] = None,
    limit: int = 2000,
) -> pd.DataFrame:
    """Lançamentos de crédito/devolução/abatimento de cliente, com motivo em texto livre.

    Fonte: `GOLD.vendas.dim_credito_devolucoes` (schema comercial/legado) — não
    `vendas_sap.fct_credito_devolucoes_sap`, porque só a tabela `vendas` tem o campo
    `Texto` preenchido (~93% de cobertura); a versão `vendas_sap` só tem código de tipo de
    documento (RV/AB/DR/...) e conta contábil, sem texto — ver docs/CONTEXTO_VENDAS_SAP.md.

    Args:
        data_inicio, data_fim: período de Data_documento. Se algum for None, usa o
            default (últimos 180 dias corridos até hoje).
        excluir_faturamento_rotina: se True (padrão), exclui `Tp_doc = 'RV'` — é o tipo de
            documento mais comum (>95% das linhas) e é só transferência de documento de
            faturamento de rotina, texto sempre "Transf.docs.faturam. ...", não é uma
            devolução/abatimento de negócio de fato.
        nome_cliente: filtra por trecho do nome do cliente (LIKE, case-insensitive).
        tipo_cliente: "Governo" ou "Privado" (None = os dois) — ver `_condicao_tipo_cliente_por_codigo`.
        limit: teto de linhas.

    Nota: os códigos de `Tp_doc` (RV, AB, DR, DG, DZ, LM, DA, EX, SA) não têm tradução pra
    texto disponível nesta base (a tabela SAP de descrição de tipo de documento, T003T, não
    está replicada no HANA) — use o campo `Texto` como motivo legível; `Tp_doc` fica só como
    código de apoio.
    """
    if data_fim is None:
        data_fim = date.today()
    if data_inicio is None:
        data_inicio = data_fim - timedelta(days=180)

    filtros = ["d.Data_documento BETWEEN :data_inicio AND :data_fim"]
    params: dict[str, object] = {"data_inicio": data_inicio, "data_fim": data_fim, "rv": "RV"}
    if excluir_faturamento_rotina:
        filtros.append("d.Tp_doc <> :rv")
    if nome_cliente:
        filtros.append("cl.Nome_Cliente LIKE :nome_cliente")
        params["nome_cliente"] = f"%{nome_cliente.strip()}%"
    join_tipo, condicao_tipo = _condicao_tipo_cliente_por_codigo(tipo_cliente, "d.Cliente")
    where = "WHERE " + " AND ".join(filtros) + condicao_tipo
    query = f"""
        SELECT TOP {int(limit)}
            d.N_documento, d.Cliente AS Codigo_Cliente, cl.Nome_Cliente,
            d.Data_documento, d.Tp_doc, d.Montante, d.Texto
        FROM vendas.dim_credito_devolucoes d
        LEFT JOIN (SELECT DISTINCT Codigo_Cliente, Nome_Cliente FROM {SCHEMA}.dim_cliente_sap) cl
            ON CAST(d.Cliente AS BIGINT) = CAST(cl.Codigo_Cliente AS BIGINT)
        {join_tipo}
        {where}
        ORDER BY d.Data_documento DESC
    """  # nosec B608
    return read_sql(query, database="GOLD", params=params)


def faturamento_por_org_vendas_linha_negocio(
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
    tipo_cliente: Optional[str] = None,
) -> pd.DataFrame:
    """Faturamento agregado por Organização de Vendas (SAP) x Linha de Negócio (comercial).

    São duas dimensões **independentes** (não é uma hierarquia 1:1) — uma mesma Organização
    de Vendas SAP (ex.: 1000 = BLAU HOSPITALAR) fatura pra clientes de várias linhas de
    negócio ao mesmo tempo (confirmado nos dados: 1000 aparece com ONCO/HEMATO, AESTHETICS,
    FARMA e NÃO ALOCADO simultaneamente). Por isso o resultado é uma matriz Org_Vendas x
    Linha_Negocio, não uma lista simples.

    Linha de Negócio vem em 2 camadas, nessa ordem de prioridade (ver
    `docs/CONTEXTO_VENDAS_SAP.md` §8.2 pra investigação completa e números de precisão):

    1. **Manual**: `Codigo_Cliente` -> `vendas.dim_cliente_setor` (`periodo` mais recente
       por cliente) -> `vendas.dim_estrutura.org_vendas`. Cobertura ~52% dos clientes.
    2. **Heurística por produto** (fallback só pra quem não tem match manual): categoria
       dominante (maior `Valor_Liquido_Faturamento` histórico) de `vendas.dim_produto.
       unidade_de_negocio` entre os produtos que o cliente comprou. Testado contra os
       clientes com rótulo manual conhecido (2026-08-25): precisão alta pra AESTHETICS
       (~96%) e FARMA (~100%, mas raro — poucos clientes têm produto FARMA como
       dominante), **precisão menor pra ONCO/HEMATO (~64%)** — cerca de 1/3 dos clientes
       que a heurística classifica como ONCO/HEMATO são na verdade FARMA (esses clientes
       compram muito produto de especialidade/onco por valor mesmo sendo atendidos
       comercialmente pelo time FARMA). Eleva a cobertura geral de ~52% pra ~87% dos
       clientes faturados.

    Cliente sem match em nenhuma camada cai em 'NAO ALOCADO', igual à categoria que já
    existe nativamente na tabela de estrutura. Coluna `Origem_Linha_Negocio` (`MANUAL` /
    `HEURISTICA_PRODUTO` / `NAO_ALOCADO`) indica qual camada resolveu cada linha — filtre
    por `Origem_Linha_Negocio == 'MANUAL'` se precisar só do dado confirmado, sem a
    heurística.

    Args:
        data_inicio, data_fim: período de Data_Faturamento. Se algum for None, usa o
            default (últimos 90 dias corridos até hoje).
        tipo_cliente: "Governo" ou "Privado" (None = os dois).
    """
    if data_fim is None:
        data_fim = date.today()
    if data_inicio is None:
        data_inicio = data_fim - timedelta(days=90)

    join_tipo, condicao_tipo = _condicao_tipo_cliente_por_codigo(tipo_cliente, "f.Codigo_Cliente")
    query = f"""
        WITH cliente_setor_atual AS (
            SELECT cod_cliente, cod_setor,
                   ROW_NUMBER() OVER (PARTITION BY cod_cliente ORDER BY periodo DESC) AS rn
            FROM vendas.dim_cliente_setor
        ),
        linha_manual AS (
            SELECT cs.cod_cliente, e.org_vendas AS Linha_Negocio
            FROM cliente_setor_atual cs
            JOIN vendas.dim_estrutura e ON cs.cod_setor = e.cod_setor
            WHERE cs.rn = 1
        ),
        produto_cliente AS (
            SELECT ff.Codigo_Cliente, p.unidade_de_negocio, SUM(ff.Valor_Liquido_Faturamento) AS valor,
                   ROW_NUMBER() OVER (
                       PARTITION BY ff.Codigo_Cliente ORDER BY SUM(ff.Valor_Liquido_Faturamento) DESC
                   ) AS rn
            FROM {SCHEMA}.fct_faturamento_itens_sap ff
            JOIN vendas.dim_produto p ON ff.Codigo_Produto = p.material
            WHERE p.unidade_de_negocio IS NOT NULL
            GROUP BY ff.Codigo_Cliente, p.unidade_de_negocio
        ),
        linha_heuristica AS (
            SELECT Codigo_Cliente,
                   CASE unidade_de_negocio
                       WHEN 'BLAU AESTHETICS' THEN 'AESTHETICS'
                       WHEN 'ESPECIALIDADES-ONCO HEMATO' THEN 'ONCO / HEMATO'
                       WHEN 'FARMA' THEN 'FARMA'
                   END AS Linha_Negocio
            FROM produto_cliente
            WHERE rn = 1
        )
        SELECT
            f.Codigo_Org_Vendas,
            ov.Descricao_Org_Vendas,
            COALESCE(lm.Linha_Negocio, lh.Linha_Negocio, 'NAO ALOCADO') AS Linha_Negocio,
            CASE
                WHEN lm.Linha_Negocio IS NOT NULL THEN 'MANUAL'
                WHEN lh.Linha_Negocio IS NOT NULL THEN 'HEURISTICA_PRODUTO'
                ELSE 'NAO_ALOCADO'
            END AS Origem_Linha_Negocio,
            SUM(f.Valor_Liquido_Faturamento) AS Valor_Faturado,
            SUM(f.Qtd_Faturada) AS Qtd_Faturada,
            COUNT(*) AS Qtd_Itens
        FROM {SCHEMA}.fct_faturamento_itens_sap f
        LEFT JOIN (SELECT DISTINCT Org_Vendas, Descricao_Org_Vendas FROM {SCHEMA}.dim_cliente_sap) ov
            ON f.Codigo_Org_Vendas = ov.Org_Vendas
        LEFT JOIN linha_manual lm
            ON CAST(f.Codigo_Cliente AS BIGINT) = CAST(lm.cod_cliente AS BIGINT)
        LEFT JOIN linha_heuristica lh
            ON CAST(f.Codigo_Cliente AS BIGINT) = CAST(lh.Codigo_Cliente AS BIGINT)
        {join_tipo}
        WHERE f.Data_Faturamento BETWEEN :data_inicio AND :data_fim{condicao_tipo}
        GROUP BY f.Codigo_Org_Vendas, ov.Descricao_Org_Vendas,
                 COALESCE(lm.Linha_Negocio, lh.Linha_Negocio, 'NAO ALOCADO'),
                 CASE
                     WHEN lm.Linha_Negocio IS NOT NULL THEN 'MANUAL'
                     WHEN lh.Linha_Negocio IS NOT NULL THEN 'HEURISTICA_PRODUTO'
                     ELSE 'NAO_ALOCADO'
                 END
        ORDER BY Valor_Faturado DESC
    """  # nosec B608
    return read_sql(
        query, database="GOLD", params={"data_inicio": data_inicio, "data_fim": data_fim}
    )


def meta_vs_realizado_mensal(
    data_inicio: Optional[date] = None, data_fim: Optional[date] = None, bu: Optional[str] = None
) -> pd.DataFrame:
    """Meta comercial (planejamento) x Realizado (faturamento SAP), agregado por mês x BU.

    Meta é um dado de **planejamento**, não uma transação — não existe (nem pode existir)
    fonte SAP/Salesforce equivalente, é decisão de diretoria/orçamento capturada só em
    `vendas.fat_meta_equipe` (SharePoint, grão mês+setor+material — ver
    `docs/CONTEXTO_VENDAS_SAP.md` §8.3). Diferente do problema de Linha de Negócio (§8.2),
    aqui não há heurística possível: meta não é algo observável em transação nenhuma.

    Realizado vem de `fct_faturamento_itens_sap`, atribuído ao mesmo `cod_setor` da meta via
    `vendas.dim_cliente_setor` (mesmo crosswalk cliente->setor de
    `faturamento_por_org_vendas_linha_negocio` — herda a cobertura ~52%: faturamento de
    cliente sem `cod_setor` mapeado não desaparece, só não casa com nenhuma meta e cai em
    BU 'NAO ALOCADO'). `BU` aqui é o valor literal de `fat_meta_equipe.bu` (`ONCO-HEMATO`,
    `FARMA`, `BLAU AESTHETICS`, `MS`, `Botulift`) — **não** é 1:1 com `Linha_Negocio` de
    `faturamento_por_org_vendas_linha_negocio` (nomenclatura ligeiramente diferente, ex.
    "ONCO-HEMATO" vs "ONCO / HEMATO", e "Botulift" é uma BU própria sem equivalente lá).

    Args:
        data_inicio, data_fim: janela por `Data_Faturamento`/`data_meta`. Default: ano
            corrente (1º de janeiro até hoje). Atenção: `data_fim` é comparado contra a
            data cheia da fatura, não truncado pro 1º dia do mês — um `data_fim` no meio do
            mês corrente vai mostrar Realizado parcial (mês incompleto) pra esse mês, o que
            é esperado, não um bug.
        bu: filtra 1 BU específica. None = todas.
    """
    if data_fim is None:
        data_fim = date.today()
    if data_inicio is None:
        data_inicio = data_fim.replace(month=1, day=1)

    condicao_bu = " AND m.bu = :bu" if bu else ""
    query = f"""
        WITH cliente_setor_atual AS (
            SELECT cod_cliente, cod_setor,
                   ROW_NUMBER() OVER (PARTITION BY cod_cliente ORDER BY periodo DESC) AS rn
            FROM vendas.dim_cliente_setor
        ),
        realizado AS (
            SELECT
                DATEFROMPARTS(YEAR(f.Data_Faturamento), MONTH(f.Data_Faturamento), 1) AS mes,
                cs.cod_setor,
                f.Codigo_Produto AS material,
                SUM(f.Valor_Liquido_Faturamento) AS valor_realizado,
                SUM(f.Qtd_Faturada) AS unidades_realizado
            FROM {SCHEMA}.fct_faturamento_itens_sap f
            LEFT JOIN cliente_setor_atual cs
                ON CAST(f.Codigo_Cliente AS BIGINT) = CAST(cs.cod_cliente AS BIGINT) AND cs.rn = 1
            WHERE f.Data_Faturamento BETWEEN :data_inicio AND :data_fim
            GROUP BY DATEFROMPARTS(YEAR(f.Data_Faturamento), MONTH(f.Data_Faturamento), 1),
                     cs.cod_setor, f.Codigo_Produto
        )
        SELECT
            COALESCE(m.data_meta, r.mes) AS Mes,
            COALESCE(m.bu, 'NAO ALOCADO') AS BU,
            SUM(m.meta) AS Meta_Valor,
            SUM(m.unidades) AS Meta_Unidades,
            SUM(r.valor_realizado) AS Valor_Realizado,
            SUM(r.unidades_realizado) AS Unidades_Realizado
        FROM vendas.fat_meta_equipe m
        FULL OUTER JOIN realizado r
            ON m.data_meta = r.mes AND m.cod_setor = r.cod_setor AND m.material = r.material
        WHERE COALESCE(m.data_meta, r.mes) BETWEEN :data_inicio AND :data_fim{condicao_bu}
        GROUP BY COALESCE(m.data_meta, r.mes), COALESCE(m.bu, 'NAO ALOCADO')
        ORDER BY Mes, BU
    """  # nosec B608
    params = {"data_inicio": data_inicio, "data_fim": data_fim}
    if bu:
        params["bu"] = bu
    return read_sql(query, database="GOLD", params=params)


def estoque_totais() -> pd.DataFrame:
    """Totais agregados de estoque (1 linha) — pra KPI de resumo, sem quebrar por Material+Centro.

    Mais barato que somar `estoque_restrito_disponivel()` (que traz linha por Material+Centro)
    quando só se quer os totais gerais.

    `Valor_Financeiro_Estoque` aqui soma **só centros BRL** (`Moeda='BRL'`, ver
    `MOEDA_POR_PAIS_CENTRO`) — centros do Uruguai/Colômbia valoram em moeda local não
    convertida, misturar geraria um "R$" que não é R$ de verdade. `Qtd_*` continua somando
    todos os centros (quantidade não depende de moeda). Use `estoque_restrito_disponivel()`
    se precisar do valor por centro, incluindo os não-BRL (com a moeda marcada na coluna).
    """
    moeda_case = _moeda_case_sql("c.Pais_Centro")
    query = f"""
        SELECT
            SUM(e.Qtd_Disponivel_Venda) AS Qtd_Disponivel_Venda,
            SUM(e.Qtd_Estoque_Qualidade + e.Qtd_Estoque_Bloqueado) AS Qtd_Restrito,
            SUM(e.Qtd_Estoque_Fisico_Total) AS Qtd_Fisico_Total,
            SUM(CASE WHEN {moeda_case} = 'BRL' THEN e.Valor_Financeiro_Estoque ELSE 0 END) AS Valor_Financeiro_Estoque
        FROM {SCHEMA}.fct_estoque_lote_sap e
        LEFT JOIN (SELECT DISTINCT Codigo_Centro, Pais_Centro FROM {SCHEMA}.dim_centro_sap) c
            ON e.Codigo_Centro = c.Codigo_Centro
    """  # nosec B608
    return read_sql(query, database="GOLD")


def faturamento_mensal(meses: int = 12) -> pd.DataFrame:
    """Faturamento mensal agregado dos últimos N meses — pra gráfico de evolução/tendência.

    Sem quebra por Org Vendas/Linha de Negócio (ver `faturamento_por_org_vendas_linha_negocio`
    pra isso) — aqui é só a série temporal simples.

    Nota: filtra também `Data_Faturamento <= hoje` — achado ao vivo (2026-08-25) tem pelo
    menos 1 linha com data no ano 2108 (corrompida), que sem esse teto entra na janela de
    qualquer jeito por ser "maior que" o limite inferior e polui o mês mais recente do gráfico.
    """
    query = f"""
        SELECT
            FORMAT(Data_Faturamento, 'yyyy-MM') AS Mes,
            SUM(Valor_Liquido_Faturamento) AS Valor_Faturado,
            SUM(Qtd_Faturada) AS Qtd_Faturada
        FROM {SCHEMA}.fct_faturamento_itens_sap
        WHERE Data_Faturamento >= DATEADD(month, :meses_neg, CAST(GETDATE() AS date))
            AND Data_Faturamento <= CAST(GETDATE() AS date)
        GROUP BY FORMAT(Data_Faturamento, 'yyyy-MM')
        ORDER BY Mes
    """  # nosec B608
    return read_sql(query, database="GOLD", params={"meses_neg": -abs(int(meses))})


def pedidos_mensal(meses: int = 24) -> pd.DataFrame:
    """Valor de pedido entrando no funil por mês (Data_Inclusao_Pedido), últimos N meses.

    Fonte: `fct_vendas_itens_sap` (histórico real desde 2014-01, diferente de
    `fct_pendencia_sap`/`fct_estoque_lote_sap`, que só guardam o estado de hoje — ver
    docs/CONTEXTO_VENDAS_SAP.md). Serve pra ver se o volume entrando no funil está subindo
    ou caindo ao longo do tempo — não é o mesmo que "backlog", que não tem série histórica.
    """
    query = f"""
        SELECT
            FORMAT(Data_Inclusao_Pedido, 'yyyy-MM') AS Mes,
            SUM(Valor_Liquido_Pedido) AS Valor_Pedido,
            SUM(Qtd_Pedida_Original) AS Qtd_Pedida,
            COUNT(DISTINCT Numero_Pedido) AS Qtd_Pedidos
        FROM {SCHEMA}.fct_vendas_itens_sap
        WHERE Data_Inclusao_Pedido >= DATEADD(month, :meses_neg, CAST(GETDATE() AS date))
            AND Data_Inclusao_Pedido <= CAST(GETDATE() AS date)
        GROUP BY FORMAT(Data_Inclusao_Pedido, 'yyyy-MM')
        ORDER BY Mes
    """  # nosec B608
    return read_sql(query, database="GOLD", params={"meses_neg": -abs(int(meses))})


def pedidos_por_cliente(
    data_inicio: date,
    data_fim: date,
    n: int = 20,
    tipo_cliente: Optional[str] = None,
) -> pd.DataFrame:
    """Pedidos entrando no funil (`Data_Inclusao_Pedido`) agregados por cliente: quantos
    pedidos, quantos itens no total, média de itens por pedido e valor médio por pedido.

    Fonte: `fct_vendas_itens_sap` (mesma de `pedidos_mensal`) — grão Pedido+Item, agregado
    aqui primeiro por Pedido (pra não confundir "item" com "pedido") e depois por cliente.
    """
    params: dict[str, object] = {}
    join_sql, where_extra = _filtro_periodo_tipo_cliente(
        data_inicio, data_fim, tipo_cliente, params, alias_pedido="p"
    )
    query = f"""
        WITH pedido_agg AS (
            SELECT
                p.Codigo_Cliente,
                p.Numero_Pedido,
                COUNT(*) AS Itens_Pedido,
                SUM(p.Valor_Liquido_Pedido) AS Valor_Pedido
            FROM {SCHEMA}.fct_vendas_itens_sap p
            {join_sql}
            WHERE p.Data_Inclusao_Pedido BETWEEN :data_inicio AND :data_fim{where_extra}
            GROUP BY p.Codigo_Cliente, p.Numero_Pedido
        )
        SELECT TOP {int(n)}
            pa.Codigo_Cliente,
            MAX(cl.Nome_Cliente) AS Nome_Cliente,
            COUNT(DISTINCT pa.Numero_Pedido) AS Qtd_Pedidos,
            SUM(pa.Itens_Pedido) AS Qtd_Itens_Total,
            AVG(CAST(pa.Itens_Pedido AS float)) AS Media_Itens_Pedido,
            AVG(pa.Valor_Pedido) AS Valor_Medio_Pedido
        FROM pedido_agg pa
        LEFT JOIN (SELECT DISTINCT Codigo_Cliente, Nome_Cliente FROM {SCHEMA}.dim_cliente_sap) cl
            ON pa.Codigo_Cliente = cl.Codigo_Cliente
        GROUP BY pa.Codigo_Cliente
        ORDER BY Qtd_Pedidos DESC
    """  # nosec B608
    return read_sql(query, database="GOLD", params=params)


def devolucoes_mensal(meses: int = 24, excluir_faturamento_rotina: bool = True) -> pd.DataFrame:
    """Devoluções/créditos/abatimentos de cliente por mês (Data_Documento), últimos N meses.

    Fonte: `vendas_sap.fct_credito_devolucoes_sap` — **não** `vendas.dim_credito_devolucoes`
    (a fonte de `devolucoes_credito_motivo()`, que só é necessária ali pelo campo `Texto`
    de motivo, ver docs/CONTEXTO_VENDAS_SAP.md §6.1 — como aqui é só soma por mês, sem
    motivo, dá pra ficar 100% em `vendas_sap`). Mesma profundidade de histórico
    (2011-09, confirmado ao vivo 2026-08-25).

    Args:
        meses: janela em meses.
        excluir_faturamento_rotina: se True (padrão), exclui `Tipo_Documento_Contabil = 'RV'`
            — ver nota em `devolucoes_credito_motivo`.
    """
    where_rv = "AND Tipo_Documento_Contabil <> 'RV'" if excluir_faturamento_rotina else ""
    query = f"""
        SELECT
            FORMAT(Data_Documento, 'yyyy-MM') AS Mes,
            SUM(Valor_Lancamento_Moeda_Local) AS Valor,
            COUNT(*) AS Qtd_Lancamentos
        FROM {SCHEMA}.fct_credito_devolucoes_sap
        WHERE Data_Documento >= DATEADD(month, :meses_neg, CAST(GETDATE() AS date))
            AND Data_Documento <= CAST(GETDATE() AS date)
            {where_rv}
        GROUP BY FORMAT(Data_Documento, 'yyyy-MM')
        ORDER BY Mes
    """  # nosec B608
    return read_sql(query, database="GOLD", params={"meses_neg": -abs(int(meses))})


def _vendedor_join_sql(alias_fato: str = "f") -> str:
    """LEFT JOIN pra resolver `Codigo_Vendedor` -> nome/metadado, via `dim_vendedor_sf`.

    Só casa quando `Origem_Vendedor = 'SALESFORCE'` — a origem SAP (`VBPA.PARVW='VE'`)
    está sempre vazia em produção (0 de milhões de linhas, confirmado ao vivo 2026-08-25),
    então nunca existe `Codigo_Vendedor` de origem SAP pra resolver, e comparar um código
    SAP (KUNNR) contra a chave Salesforce de `dim_vendedor_sf` seria comparar domínios
    incompatíveis por acaso. Cobertura real medida ao vivo (últimos 12 meses, 2026-08-26):
    ~82% dos itens de `fct_faturamento_itens_sap` têm `Origem_Vendedor = 'SALESFORCE'`
    (o resto é NULL, sem vendedor identificado em nenhuma fonte) — e desses, **100%** batem
    com `dim_vendedor_sf` (327 vendedores cadastrados, `Nome_Vendedor_SF` sempre
    preenchido). Metadado extra da dimensão (`Unidade_Negocio`, `Regiao`, `Divisao`,
    `Setor`) é bem mais raso — só ~25% preenchido — não confiar nele pra segmentação.
    """
    return f"""
        LEFT JOIN {SCHEMA}.dim_vendedor_sf v
            ON {alias_fato}.Origem_Vendedor = 'SALESFORCE'
            AND {alias_fato}.Codigo_Vendedor = v.Codigo_Vendedor_SF
    """


def faturamento_por_vendedor(
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
    tipo_cliente: Optional[str] = None,
) -> pd.DataFrame:
    """Faturamento agregado por vendedor (`Codigo_Vendedor` em `fct_faturamento_itens_sap`).

    Nome vem de `dim_vendedor_sf` — ver `_vendedor_join_sql` pro porquê de casar só
    `Origem_Vendedor = 'SALESFORCE'` e pra cobertura real medida (~82% dos itens com
    vendedor identificado, 100% desses com nome resolvido). Item sem `Codigo_Vendedor`
    (ou com origem que não é Salesforce) cai em **'Sem Vendedor Identificado'** — não é
    descartado, é uma fatia real e esperada do faturamento, do mesmo jeito que outras
    páginas usam 'NAO ALOCADO'.

    Args:
        data_inicio, data_fim: período de `Data_Faturamento`. Se algum for None, usa o
            default (mês corrente até hoje).
        tipo_cliente: "Governo" ou "Privado" (None = os dois).
    """
    if data_fim is None:
        data_fim = date.today()
    if data_inicio is None:
        data_inicio = data_fim.replace(day=1)

    join_tipo, condicao_tipo = _condicao_tipo_cliente_por_codigo(tipo_cliente, "f.Codigo_Cliente")
    query = f"""
        SELECT
            COALESCE(f.Codigo_Vendedor, 'SEM_VENDEDOR') AS Codigo_Vendedor,
            COALESCE(MAX(v.Nome_Vendedor_SF), 'Sem Vendedor Identificado') AS Nome_Vendedor,
            MAX(v.Unidade_Negocio) AS Unidade_Negocio,
            MAX(v.Regiao) AS Regiao,
            SUM(f.Valor_Liquido_Faturamento) AS Valor_Faturado,
            SUM(f.Qtd_Faturada) AS Qtd_Faturada,
            COUNT(DISTINCT f.Codigo_Cliente) AS Qtd_Clientes,
            COUNT(*) AS Qtd_Itens
        FROM {SCHEMA}.fct_faturamento_itens_sap f
        {_vendedor_join_sql("f")}
        {join_tipo}
        WHERE f.Data_Faturamento BETWEEN :data_inicio AND :data_fim{condicao_tipo}
        GROUP BY COALESCE(f.Codigo_Vendedor, 'SEM_VENDEDOR')
        ORDER BY Valor_Faturado DESC
    """  # nosec B608
    return read_sql(query, database="GOLD", params={"data_inicio": data_inicio, "data_fim": data_fim})


def faturamento_vendedor_mensal(codigo_vendedor: str, meses: int = 12) -> pd.DataFrame:
    """Evolução mensal de faturamento de 1 vendedor específico (`Codigo_Vendedor`).

    Mesma fonte/histórico de `faturamento_mensal()`, filtrado a 1 vendedor — pra drill-down
    de tendência individual em `pages/18_Visao_Vendedor.py`.
    """
    query = f"""
        SELECT
            FORMAT(f.Data_Faturamento, 'yyyy-MM') AS Mes,
            SUM(f.Valor_Liquido_Faturamento) AS Valor_Faturado,
            SUM(f.Qtd_Faturada) AS Qtd_Faturada,
            COUNT(DISTINCT f.Codigo_Cliente) AS Qtd_Clientes
        FROM {SCHEMA}.fct_faturamento_itens_sap f
        WHERE f.Codigo_Vendedor = :codigo_vendedor
            AND f.Data_Faturamento >= DATEADD(month, :meses_neg, CAST(GETDATE() AS date))
            AND f.Data_Faturamento <= CAST(GETDATE() AS date)
        GROUP BY FORMAT(f.Data_Faturamento, 'yyyy-MM')
        ORDER BY Mes
    """  # nosec B608
    return read_sql(
        query,
        database="GOLD",
        params={"codigo_vendedor": codigo_vendedor, "meses_neg": -abs(int(meses))},
    )


def top_clientes_por_vendedor(
    codigo_vendedor: str,
    data_inicio: date,
    data_fim: date,
    n: int = 15,
) -> pd.DataFrame:
    """Top N clientes faturados por 1 vendedor específico, no período."""
    query = f"""
        SELECT TOP {int(n)}
            f.Codigo_Cliente,
            MAX(cl.Nome_Cliente) AS Nome_Cliente,
            SUM(f.Valor_Liquido_Faturamento) AS Valor_Faturado,
            SUM(f.Qtd_Faturada) AS Qtd_Faturada
        FROM {SCHEMA}.fct_faturamento_itens_sap f
        LEFT JOIN (SELECT DISTINCT Codigo_Cliente, Nome_Cliente FROM {SCHEMA}.dim_cliente_sap) cl
            ON f.Codigo_Cliente = cl.Codigo_Cliente
        WHERE f.Codigo_Vendedor = :codigo_vendedor
            AND f.Data_Faturamento BETWEEN :data_inicio AND :data_fim
        GROUP BY f.Codigo_Cliente
        ORDER BY Valor_Faturado DESC
    """  # nosec B608
    return read_sql(
        query,
        database="GOLD",
        params={"codigo_vendedor": codigo_vendedor, "data_inicio": data_inicio, "data_fim": data_fim},
    )


if __name__ == "__main__":
    print("Aging do backlog aberto:")
    print(aging_pendencias().to_string(index=False))
