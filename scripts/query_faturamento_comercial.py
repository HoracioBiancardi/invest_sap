"""Consultas de Faturamento agrupado por dimensão comercial (Canal/Divisional/Regional/
Distrital/Setor/Família/Produto/Cliente/Estado), com Meta.

**Fonte: `GOLD.vendas_sap.fct_faturamento_itens_sap`** — mesma medida (`SUM(Valor_Liquido_
Faturamento)`, sem filtro de Org Vendas/moeda/tipo de documento) já usada em
`scripts/query_vendas_sap.py::faturamento_mensal`/`faturamento_por_org_vendas_linha_negocio`,
pra ficar consistente com o resto do app — só uma noção de "Faturamento" circulando, não
duas competindo. A hierarquia comercial (Divisional/Regional/Distrital/Setor/Linha de
Negócio) vem do **mesmo crosswalk** já usado nessas duas funções: `Codigo_Cliente` ->
`vendas.dim_cliente_setor` (`periodo` mais recente por cliente) -> `vendas.dim_estrutura`
(por `cod_setor`) — cobertura ~52% dos clientes faturados (medida em
`docs/CONTEXTO_VENDAS_SAP.md` §8.1/§8.2); cliente sem match cai em `'NAO ALOCADO'`, igual às
outras páginas que já usam esse crosswalk.

**Histórico (2026-08-25)**: a primeira versão deste módulo usava `GOLD.vendas.fat_faturamento`
(schema legado, Salesforce) como fonte de medida — batia com muito mais precisão contra um
Painel Vendas (Power BI) de referência enviado pelo usuário (~2% de diferença, contra o >2x
de diferença que `vendas_sap` sem filtro dá). **Essa versão foi descartada por decisão do
usuário**: o schema `vendas` (legado) só deve seguir sendo usado pra `fat_meta_equipe`
(Meta — não tem alternativa SAP, ver §8.3) e pras tabelas de crosswalk/dimensão já
estabelecidas noutras páginas (`dim_cliente_setor`, `dim_estrutura`, `dim_produto`) — não
mais como fonte de **medida** de faturamento (`fat_faturamento` competia diretamente com
`vendas_sap.fct_faturamento_itens_sap`, criando 2 números de "Faturamento" divergentes e sem
reconciliação no mesmo app). Ver `docs/CONTEXTO_VENDAS_SAP.md` §10 pro registro completo —
inclusive por que os números aqui **não batem** com um Painel Vendas externo que use
`fat_faturamento` como fonte (e por que isso é aceito: consistência interna do app pesa mais
que bater com uma fonte de fora que a própria auditoria já achou "concorrente").
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

try:
    from scripts.db import read_sql
except ImportError:
    from db import read_sql

SCHEMA = "vendas_sap"

# CTEs reusadas por toda função que precisa da hierarquia comercial (Divisional/Regional/
# Distrital/Setor/Linha de Negócio/Canal) — mesmo crosswalk de
# `query_vendas_sap.py::faturamento_por_org_vendas_linha_negocio`. Sem "WITH" na frente de
# propósito: quem usa concatena com as próprias CTEs (ex.: `meta_vs_realizado_por_dimensao`).
_CTE_ESTRUTURA_COMERCIAL = """
    cliente_setor_atual AS (
        SELECT cod_cliente, cod_setor,
               ROW_NUMBER() OVER (PARTITION BY cod_cliente ORDER BY periodo DESC) AS rn
        FROM vendas.dim_cliente_setor
    ),
    estrutura_comercial AS (
        SELECT cs.cod_cliente, e.cod_setor, e.org_vendas, e.divisional, e.regional,
               e.distrital, e.descricao
        FROM cliente_setor_atual cs
        JOIN vendas.dim_estrutura e ON cs.cod_setor = e.cod_setor
        WHERE cs.rn = 1
    )
"""

# JOIN de cliente (nome/CNPJ/UF, via `vendas_sap.dim_cliente_sap` — não crosswalk, cobertura
# alta) + estrutura comercial (via a CTE acima, cobertura ~52%). Ambos baratos o bastante
# (~4s testado) pra incluir sempre, mesmo quando a dimensão pedida não precisa dos dois —
# simplifica o código e evita bug de "join faltando" por engano.
_JOIN_CLIENTE_ESTRUTURA = """
    LEFT JOIN (
        SELECT DISTINCT Codigo_Cliente, Nome_Cliente, CNPJ_CPF, Estado_UF
        FROM vendas_sap.dim_cliente_sap
    ) cl ON f.Codigo_Cliente = cl.Codigo_Cliente
    LEFT JOIN estrutura_comercial ec
        ON CAST(f.Codigo_Cliente AS BIGINT) = CAST(ec.cod_cliente AS BIGINT)
"""

_JOIN_PRODUTO = "LEFT JOIN vendas.dim_produto p ON f.Codigo_Produto = p.material"
_JOIN_MATERIAL_SAP = f"""
    LEFT JOIN {SCHEMA}.dim_material_sap m
        ON f.Mandante = m.Mandante AND f.Codigo_Produto = m.Codigo_Produto
"""

# Cliente Ministério da Saúde está pulverizado em várias `Codigo_Cliente` em
# `dim_cliente_sap` (unidades hospitalares distintas, ex. "MINISTERIO DA SAUDE HOSP. GERAL
# DE...", "MINISTERIO DA SAUDE-HOSPITAL DA LAG") — por isso `LIKE`, não igualdade exata.
CLIENTE_MS_LIKE = "MINISTERIO DA SAUDE"

# cod_setor de "<Org Vendas comercial> - Publico" em `vendas.dim_estrutura` — um por Org
# Vendas comercial (ONCO/HEMATO, FARMA, AESTHETICS). Ver `docs/CONTEXTO_VENDAS_SAP.md` §10.
SETORES_PUBLICO = (601000000, 602000000, 603000000)

# Canal Venda (Privado/MS/Publico/NAO ALOCADO) — mesma regra descoberta e validada contra o
# Painel Vendas (ver §10), agora expressa sobre o crosswalk em vez de uma coluna nativa:
# 'NAO ALOCADO' aparece quando o cliente não tem `cod_setor` mapeado no crosswalk (~48% dos
# clientes) — diferente da versão anterior (fonte com `cod_setor` nativo, cobertura maior),
# aqui esse balde fica bem maior e deve ser tratado como dado real, não bug.
_CANAL_VENDA_SQL = f"""
    CASE
        WHEN cl.Nome_Cliente LIKE '%{CLIENTE_MS_LIKE}%' THEN 'MS'
        WHEN ec.cod_setor IN {SETORES_PUBLICO} THEN 'Publico'
        WHEN ec.cod_setor IS NOT NULL THEN 'Privado'
        ELSE 'NAO ALOCADO'
    END
"""


def _expr_dimensao_hierarquia(coluna_estrutura: str) -> str:
    """Expressão SQL de uma coluna de `vendas.dim_estrutura` (`org_vendas`/`divisional`/
    `regional`/`distrital`/`descricao`), com o ajuste "cliente Ministério da Saúde -> nó MS"
    aplicado linha a linha (mesmo raciocínio do Canal Venda) e COALESCE pra 'NAO ALOCADO'
    quando o cliente não tem `cod_setor` mapeado no crosswalk."""
    return (
        f"COALESCE("
        f"CASE WHEN cl.Nome_Cliente LIKE '%{CLIENTE_MS_LIKE}%' "
        f"THEN REPLACE(ec.{coluna_estrutura}, '- Publico', '- MS') "
        f"ELSE ec.{coluna_estrutura} END, 'NAO ALOCADO')"
    )


# Dimensões suportadas por `faturamento_por_dimensao` — nome exibido -> expressão SQL da
# coluna de agrupamento. Whitelist (não é input livre de usuário, mesmo vindo de selectbox).
DIMENSOES_FATURAMENTO = {
    "Canal": _CANAL_VENDA_SQL,
    "Linha de Negócio": _expr_dimensao_hierarquia("org_vendas"),
    "Divisional": _expr_dimensao_hierarquia("divisional"),
    "Regional": _expr_dimensao_hierarquia("regional"),
    "Distrital": _expr_dimensao_hierarquia("distrital"),
    "Setor": _expr_dimensao_hierarquia("descricao"),
    "Família": "COALESCE(p.familia, 'NAO INFORMADO')",
    "Produto": "COALESCE(m.Descricao_Produto, 'NAO INFORMADO')",
    "Cliente": "COALESCE(cl.Nome_Cliente, 'NAO INFORMADO')",
    "Estado (UF)": "COALESCE(cl.Estado_UF, 'NAO INFORMADO')",
    "Tipo Documento Faturamento": "COALESCE(f.Tipo_Documento_Faturamento, 'NAO INFORMADO')",
}

# Dimensões que exigem o JOIN extra de `vendas.dim_produto` (Família) ou
# `vendas_sap.dim_material_sap` (Produto) — as demais já vêm de `_JOIN_CLIENTE_ESTRUTURA`
# (sempre presente) ou de coluna nativa de `fct_faturamento_itens_sap`.
_DIMS_PRODUTO = {"Família"}
_DIMS_MATERIAL = {"Produto"}

# Dimensões que vêm de `vendas.dim_estrutura` (via `_expr_dimensao_hierarquia`) — usado por
# `valores_dimensao` pra saber quando buscar o vocabulário ali em vez de nas outras tabelas.
_DIMS_HIERARQUIA = {"Linha de Negócio", "Divisional", "Regional", "Distrital", "Setor"}

# Dimensões com meta associada (via `vendas.fat_meta_equipe`, grão setor — não dá pra ter
# meta por Produto/Cliente/Estado/Tipo Documento, que não existem nesse grão).
DIMENSOES_META = {
    "Canal",
    "Linha de Negócio",
    "Divisional",
    "Regional",
    "Distrital",
    "Setor",
    "Família",
}


def _aplicar_filtros(
    filtros: Optional[dict[str, str]], params: dict[str, object]
) -> tuple[str, bool, bool]:
    """Monta condições WHERE extra a partir de `{dimensao: valor}` (valor exato, vindo de um
    selectbox — não é busca livre), pra recortar o resultado a um valor específico de uma ou
    mais dimensões enquanto se olha outra dimensão/série. `dimensao` deve ser uma chave de
    `DIMENSOES_FATURAMENTO` — validado contra essa whitelist antes de virar SQL.

    Returns:
        (where_extra, precisa_produto, precisa_material) — `where_extra` já vem prefixado
        com " AND ..." por condição; os outros dois dizem se algum filtro exige o JOIN de
        `_JOIN_PRODUTO` (Família) ou `_JOIN_MATERIAL_SAP` (Produto) além do que a dimensão de
        agrupamento já pediria sozinha.
    """
    if not filtros:
        return "", False, False
    condicoes = []
    precisa_produto = False
    precisa_material = False
    for i, (dimensao, valor) in enumerate(filtros.items()):
        if dimensao not in DIMENSOES_FATURAMENTO:
            raise ValueError(
                f"filtro com dimensao inválida: {dimensao!r} (deve ser uma de "
                f"{list(DIMENSOES_FATURAMENTO)})"
            )
        expr = DIMENSOES_FATURAMENTO[dimensao]
        param_key = f"filtro_{i}"
        condicoes.append(f"({expr}) = :{param_key}")
        params[param_key] = valor
        if dimensao in _DIMS_PRODUTO:
            precisa_produto = True
        if dimensao in _DIMS_MATERIAL:
            precisa_material = True
    return "".join(f" AND {c}" for c in condicoes), precisa_produto, precisa_material


def valores_dimensao(dimensao: str) -> list[str]:
    """Valores possíveis de uma dimensão de `DIMENSOES_FATURAMENTO`, pra popular um filtro
    (selectbox) na UI.

    Deliberadamente **não** consulta `fct_faturamento_itens_sap` (446 mil linhas, sem índice
    útil pra um DISTINCT sobre expressão calculada com CTE — medido em ~20-25s por dimensão,
    inviável somar 7-11 chamadas numa carga de página): busca direto nas tabelas de dimensão
    pequenas que definem o *vocabulário* possível (`vendas.dim_estrutura`,
    `vendas.dim_produto`, `vendas_sap.dim_cliente_sap`, `vendas_sap.dim_material_sap`) —
    ordens de magnitude mais rápido, e mais completo (traz todo valor cadastrado, mesmo o que
    não teve faturamento no histórico consultado por outras funções deste módulo).
    """
    if dimensao not in DIMENSOES_FATURAMENTO:
        raise ValueError(
            f"dimensao deve ser um de {list(DIMENSOES_FATURAMENTO)}, recebido: {dimensao!r}"
        )

    if dimensao == "Canal":
        # Canal é 100% derivado (não existe como coluna) — o domínio é fixo por construção,
        # ver `_CANAL_VENDA_SQL`. Não precisa de consulta nenhuma.
        return ["Privado", "MS", "Publico", "NAO ALOCADO"]

    if dimensao in _DIMS_HIERARQUIA:
        coluna = {
            "Linha de Negócio": "org_vendas",
            "Divisional": "divisional",
            "Regional": "regional",
            "Distrital": "distrital",
            "Setor": "descricao",
        }[dimensao]
        # Une os valores nativos de dim_estrutura com a variante "... - MS" que o ajuste do
        # cliente Ministério da Saúde pode gerar (REPLACE de "- Publico" por "- MS") — ver
        # `_expr_dimensao_hierarquia`. 'NAO ALOCADO' é adicionado à parte (cliente sem
        # crosswalk), não existe em `dim_estrutura`.
        query = f"""
            SELECT DISTINCT {coluna} AS Valor FROM vendas.dim_estrutura WHERE {coluna} IS NOT NULL
            UNION
            SELECT DISTINCT REPLACE({coluna}, '- Publico', '- MS') FROM vendas.dim_estrutura
                WHERE {coluna} LIKE '% - Publico'
        """  # nosec B608
        valores = read_sql(query, database="GOLD")["Valor"].tolist()
        return sorted({v for v in valores if v is not None} | {"NAO ALOCADO"})

    query_por_dimensao = {
        "Família": (
            "SELECT DISTINCT familia AS Valor FROM vendas.dim_produto WHERE familia IS NOT NULL"
        ),
        # "Produto" não usa dim_material_sap sozinho de propósito: o cadastro completo tem
        # ~80 mil materiais (a maioria matéria-prima/embalagem, nunca faturado a cliente —
        # inviável num selectbox). Restringe a produtos com ao menos 1 fatura no histórico —
        # ainda ~1.700, mas um domínio que faz sentido escolher. Sem a CTE de crosswalk
        # cliente→setor (não precisa dela aqui), então continua rápido (~6s, não ~25s).
        "Produto": f"""
            SELECT DISTINCT m.Descricao_Produto AS Valor
            FROM {SCHEMA}.fct_faturamento_itens_sap f
            JOIN {SCHEMA}.dim_material_sap m
                ON f.Mandante = m.Mandante AND f.Codigo_Produto = m.Codigo_Produto
            WHERE m.Descricao_Produto IS NOT NULL
        """,
        "Cliente": (
            f"SELECT DISTINCT Nome_Cliente AS Valor FROM {SCHEMA}.dim_cliente_sap "
            f"WHERE Nome_Cliente IS NOT NULL"
        ),
        "Estado (UF)": (
            f"SELECT DISTINCT Estado_UF AS Valor FROM {SCHEMA}.dim_cliente_sap "
            f"WHERE Estado_UF IS NOT NULL"
        ),
        "Tipo Documento Faturamento": (
            f"SELECT DISTINCT Tipo_Documento_Faturamento AS Valor "
            f"FROM {SCHEMA}.fct_faturamento_itens_sap WHERE Tipo_Documento_Faturamento IS NOT NULL"
        ),
    }  # nosec B608
    df = read_sql(query_por_dimensao[dimensao], database="GOLD")
    return sorted(v for v in df["Valor"].tolist() if v is not None)


def faturamento_por_dimensao(
    data_inicio: date,
    data_fim: date,
    dimensao: str,
    granularidade: str = "total",
    tipo_cliente: Optional[str] = None,
    filtros: Optional[dict[str, str]] = None,
) -> pd.DataFrame:
    """Faturamento (`vendas_sap.fct_faturamento_itens_sap`) agrupado por uma dimensão
    comercial, no grão pedido (total do período, mês ou dia).

    Args:
        data_inicio, data_fim: período de `Data_Faturamento` (inclusive).
        dimensao: uma chave de `DIMENSOES_FATURAMENTO` — a dimensão a **agrupar**.
        granularidade: "total" (1 linha por valor de dimensão), "mes" (+ coluna `Mes`) ou
            "dia" (+ coluna `Dia`) — "dia" só faz sentido pra período curto (ex.: mês atual).
        tipo_cliente: "Governo" ou "Privado" (proxy via Canal Venda: Governo = MS+Publico).
            None = todos.
        filtros: `{dimensao: valor}` opcional — **recorta** o resultado a um valor específico
            de outra(s) dimensão(ões), sem mudar o que aparece nas linhas. Ver
            `_aplicar_filtros`.
    """
    if dimensao not in DIMENSOES_FATURAMENTO:
        raise ValueError(
            f"dimensao deve ser um de {list(DIMENSOES_FATURAMENTO)}, recebido: {dimensao!r}"
        )
    if granularidade not in ("total", "mes", "dia"):
        raise ValueError(
            f"granularidade deve ser 'total', 'mes' ou 'dia', recebido: {granularidade!r}"
        )

    dim_expr = DIMENSOES_FATURAMENTO[dimensao]
    params: dict[str, object] = {"data_inicio": data_inicio, "data_fim": data_fim}
    where_filtros, precisa_produto_filtro, precisa_material_filtro = _aplicar_filtros(
        filtros, params
    )
    where_tipo_cliente = ""
    if tipo_cliente == "Governo":
        where_tipo_cliente = f" AND ({_CANAL_VENDA_SQL}) IN ('MS', 'Publico')"
    elif tipo_cliente == "Privado":
        where_tipo_cliente = f" AND ({_CANAL_VENDA_SQL}) = 'Privado'"

    colunas_tempo = ""
    group_tempo = ""
    if granularidade == "mes":
        colunas_tempo = "FORMAT(f.Data_Faturamento, 'yyyy-MM') AS Mes,"
        group_tempo = "FORMAT(f.Data_Faturamento, 'yyyy-MM'),"
    elif granularidade == "dia":
        colunas_tempo = "CAST(f.Data_Faturamento AS date) AS Dia,"
        group_tempo = "CAST(f.Data_Faturamento AS date),"

    join_produto = _JOIN_PRODUTO if (dimensao in _DIMS_PRODUTO or precisa_produto_filtro) else ""
    join_material = (
        _JOIN_MATERIAL_SAP if (dimensao in _DIMS_MATERIAL or precisa_material_filtro) else ""
    )

    query = f"""
        WITH {_CTE_ESTRUTURA_COMERCIAL}
        SELECT
            {colunas_tempo}
            {dim_expr} AS Dimensao,
            SUM(f.Valor_Liquido_Faturamento) AS Valor_Faturado,
            SUM(f.Qtd_Faturada) AS Qtd_Faturada,
            COUNT(*) AS Qtd_Itens
        FROM {SCHEMA}.fct_faturamento_itens_sap f
        {_JOIN_CLIENTE_ESTRUTURA}
        {join_produto}
        {join_material}
        WHERE f.Data_Faturamento BETWEEN :data_inicio AND :data_fim
            {where_tipo_cliente}{where_filtros}
        GROUP BY {group_tempo} {dim_expr}
        ORDER BY Valor_Faturado DESC
        OPTION (RECOMPILE)
    """  # nosec B608
    return read_sql(query, database="GOLD", params=params)


def faturamento_serie(
    data_inicio: date,
    data_fim: date,
    granularidade: str = "dia",
    tipo_cliente: Optional[str] = None,
    filtros: Optional[dict[str, str]] = None,
) -> pd.DataFrame:
    """Série temporal simples de Faturamento (sem quebra por dimensão) — pra gráfico de
    evolução diária (MTD) ou mensal (YTD/trimestral/anual).

    Args:
        granularidade: "dia" ou "mes".
        tipo_cliente, filtros: ver `faturamento_por_dimensao`.
    """
    if granularidade not in ("dia", "mes"):
        raise ValueError(f"granularidade deve ser 'dia' ou 'mes', recebido: {granularidade!r}")
    params: dict[str, object] = {"data_inicio": data_inicio, "data_fim": data_fim}
    where_filtros, precisa_produto, precisa_material = _aplicar_filtros(filtros, params)
    where_tipo_cliente = ""
    if tipo_cliente == "Governo":
        where_tipo_cliente = f" AND ({_CANAL_VENDA_SQL}) IN ('MS', 'Publico')"
    elif tipo_cliente == "Privado":
        where_tipo_cliente = f" AND ({_CANAL_VENDA_SQL}) = 'Privado'"
    tempo_expr = (
        "CAST(f.Data_Faturamento AS date)"
        if granularidade == "dia"
        else "FORMAT(f.Data_Faturamento, 'yyyy-MM')"
    )
    col_tempo = "Dia" if granularidade == "dia" else "Mes"
    join_produto = _JOIN_PRODUTO if precisa_produto else ""
    join_material = _JOIN_MATERIAL_SAP if precisa_material else ""
    query = f"""
        WITH {_CTE_ESTRUTURA_COMERCIAL}
        SELECT
            {tempo_expr} AS {col_tempo},
            SUM(f.Valor_Liquido_Faturamento) AS Valor_Faturado,
            SUM(f.Qtd_Faturada) AS Qtd_Faturada
        FROM {SCHEMA}.fct_faturamento_itens_sap f
        {_JOIN_CLIENTE_ESTRUTURA}
        {join_produto}
        {join_material}
        WHERE f.Data_Faturamento BETWEEN :data_inicio AND :data_fim
            {where_tipo_cliente}{where_filtros}
        GROUP BY {tempo_expr}
        ORDER BY {col_tempo}
        OPTION (RECOMPILE)
    """  # nosec B608
    return read_sql(query, database="GOLD", params=params)


def _par_expr_meta(dimensao: str) -> tuple[str, str, str]:
    """Pra uma `dimensao` de `DIMENSOES_META`, devolve `(expr_realizado, expr_meta,
    join_meta_extra)`: a expressão de agrupamento/filtro no lado Realizado (alias `f`/`cl`/
    `ec`, igual a `DIMENSOES_FATURAMENTO`), a expressão equivalente no lado Meta (alias `m`/
    `e`/`mp` de `meta_vs_realizado_por_dimensao`) e um JOIN adicional só necessário no lado
    Meta (ex.: `dim_produto` pra Família)."""
    if dimensao not in DIMENSOES_META:
        raise ValueError(
            f"dimensao deve ser um de {sorted(DIMENSOES_META)}, recebido: {dimensao!r}"
        )
    if dimensao == "Família":
        return (
            "COALESCE(p.familia, 'NAO INFORMADO')",
            "COALESCE(mp.familia, 'NAO INFORMADO')",
            "LEFT JOIN vendas.dim_produto mp ON m.material = mp.material",
        )
    if dimensao == "Canal":
        return (
            _CANAL_VENDA_SQL,
            """
                CASE
                    WHEN e.descricao LIKE '% - Publico' THEN 'Publico'
                    WHEN e.descricao LIKE '% - MS' THEN 'MS'
                    ELSE 'Privado'
                END
            """,
            "",
        )
    coluna = {
        "Linha de Negócio": "org_vendas",
        "Divisional": "divisional",
        "Regional": "regional",
        "Distrital": "distrital",
        "Setor": "descricao",
    }[dimensao]
    return (_expr_dimensao_hierarquia(coluna), f"COALESCE(e.{coluna}, 'NAO ALOCADO')", "")


def meta_vs_realizado_por_dimensao(
    data_inicio: date,
    data_fim: date,
    dimensao: str,
    tipo_cliente: Optional[str] = None,
    filtros: Optional[dict[str, str]] = None,
) -> pd.DataFrame:
    """Meta (`vendas.fat_meta_equipe`) x Realizado (`vendas_sap.fct_faturamento_itens_sap`),
    por mês x dimensão comercial (uma chave de `DIMENSOES_META`).

    Meta é grão mês x `cod_setor` x `material`; Realizado é atribuído ao mesmo `cod_setor`
    via o crosswalk `vendas.dim_cliente_setor` (mesmo usado em
    `scripts/query_vendas_sap.py::meta_vs_realizado_mensal`) — herda a cobertura ~52%:
    faturamento de cliente sem `cod_setor` mapeado não desaparece, cai em `'NAO ALOCADO'`.

    Canal='MS' isola o cliente Ministério da Saúde por nome (ver `CLIENTE_MS_LIKE`) — a Meta
    não tem esse recorte nativamente (o cliente não é separado do resto do 'Publico' da Org
    Vendas na meta orçamentária), então Meta de Canal='MS' aparece sempre 0/NULL.

    Args:
        dimensao: uma chave de `DIMENSOES_META` — a dimensão a **agrupar**.
        tipo_cliente: aplica só ao lado Realizado — o lado Meta não tem essa informação.
        filtros: `{dimensao: valor}` opcional, **só chaves de `DIMENSOES_META`** — aplicado
            nos dois lados. Uma chave que exista em `DIMENSOES_FATURAMENTO` mas não em
            `DIMENSOES_META` (ex.: "Cliente", "Produto") é ignorada aqui silenciosamente.
    """
    if dimensao not in DIMENSOES_META:
        raise ValueError(
            f"dimensao deve ser um de {sorted(DIMENSOES_META)}, recebido: {dimensao!r}"
        )

    params: dict[str, object] = {"data_inicio": data_inicio, "data_fim": data_fim}
    where_realizado_extra = ""
    if tipo_cliente == "Governo":
        where_realizado_extra = f" AND ({_CANAL_VENDA_SQL}) IN ('MS', 'Publico')"
    elif tipo_cliente == "Privado":
        where_realizado_extra = f" AND ({_CANAL_VENDA_SQL}) = 'Privado'"

    dim_realizado, dim_meta, join_meta_extra = _par_expr_meta(dimensao)

    where_meta_extra = ""
    joins_meta_extra_filtro = set()
    for i, (f_dimensao, f_valor) in enumerate(dict(filtros or {}).items()):
        if f_dimensao not in DIMENSOES_META:
            continue  # Meta não tem esse grão (Cliente/Produto/Estado/Tipo Documento) — ignora.
        f_dim_realizado, f_dim_meta, f_join_meta = _par_expr_meta(f_dimensao)
        param_key = f"filtro_meta_{i}"
        where_realizado_extra += f" AND ({f_dim_realizado}) = :{param_key}"
        where_meta_extra += f" AND ({f_dim_meta}) = :{param_key}"
        params[param_key] = f_valor
        if f_join_meta:
            joins_meta_extra_filtro.add(f_join_meta)
    join_meta_extra = " ".join({join_meta_extra, *joins_meta_extra_filtro} - {""})

    query = f"""
        WITH {_CTE_ESTRUTURA_COMERCIAL},
        realizado AS (
            SELECT
                FORMAT(f.Data_Faturamento, 'yyyy-MM') AS mes,
                {dim_realizado} AS Dimensao,
                SUM(f.Valor_Liquido_Faturamento) AS valor_realizado,
                SUM(f.Qtd_Faturada) AS unidades_realizado
            FROM {SCHEMA}.fct_faturamento_itens_sap f
            {_JOIN_CLIENTE_ESTRUTURA}
            {_JOIN_PRODUTO if dimensao == "Família" else ""}
            WHERE f.Data_Faturamento BETWEEN :data_inicio AND :data_fim
                {where_realizado_extra}
            GROUP BY FORMAT(f.Data_Faturamento, 'yyyy-MM'), {dim_realizado}
        ),
        meta AS (
            SELECT
                FORMAT(m.data_meta, 'yyyy-MM') AS mes,
                {dim_meta} AS Dimensao,
                SUM(m.meta) AS meta_valor,
                SUM(m.unidades) AS meta_unidades
            FROM vendas.fat_meta_equipe m
            LEFT JOIN vendas.dim_estrutura e ON m.cod_setor = e.cod_setor
            {join_meta_extra}
            WHERE m.data_meta BETWEEN :data_inicio AND :data_fim{where_meta_extra}
            GROUP BY FORMAT(m.data_meta, 'yyyy-MM'), {dim_meta}
        )
        SELECT
            COALESCE(r.mes, mt.mes) AS Mes,
            COALESCE(r.Dimensao, mt.Dimensao) AS Dimensao,
            SUM(mt.meta_valor) AS Meta_Valor,
            SUM(mt.meta_unidades) AS Meta_Unidades,
            SUM(r.valor_realizado) AS Valor_Realizado,
            SUM(r.unidades_realizado) AS Unidades_Realizado
        FROM realizado r
        FULL OUTER JOIN meta mt ON r.mes = mt.mes AND r.Dimensao = mt.Dimensao
        GROUP BY COALESCE(r.mes, mt.mes), COALESCE(r.Dimensao, mt.Dimensao)
        ORDER BY Mes, Dimensao
        OPTION (RECOMPILE)
    """  # nosec B608
    return read_sql(query, database="GOLD", params=params)


def faturamento_anual_comparativo(
    dimensao: str, ano_atual: Optional[int] = None, filtros: Optional[dict[str, str]] = None
) -> pd.DataFrame:
    """Compara Faturamento do ano corrente (YTD) contra o mesmo período do ano anterior
    (YTD) e o ano anterior inteiro, quebrado por `dimensao`.

    Faz 3 chamadas a `faturamento_por_dimensao` e junta em pandas — mais simples e legível
    que uma única query com 3 CTEs quase idênticas, e o custo de 3 idas ao banco é
    desprezível perto de uma consulta interativa de dashboard.

    Args:
        dimensao: uma chave de `DIMENSOES_FATURAMENTO` — a dimensão a **agrupar**.
        ano_atual: ano de referência (default: ano corrente).
        filtros: ver `faturamento_por_dimensao` / `_aplicar_filtros` — repassado às 3
            chamadas internas.
    """
    if ano_atual is None:
        ano_atual = date.today().year
    ano_anterior = ano_atual - 1
    hoje = date.today()
    corte_mes_dia = (hoje.month, hoje.day)

    df_ano_anterior = faturamento_por_dimensao(
        date(ano_anterior, 1, 1), date(ano_anterior, 12, 31), dimensao, filtros=filtros
    )
    df_ytd_ano_anterior = faturamento_por_dimensao(
        date(ano_anterior, 1, 1), date(ano_anterior, *corte_mes_dia), dimensao, filtros=filtros
    )
    df_ytd_atual = faturamento_por_dimensao(date(ano_atual, 1, 1), hoje, dimensao, filtros=filtros)

    def _serie(df: pd.DataFrame) -> pd.Series:
        return (
            df.set_index("Dimensao")["Valor_Faturado"] if not df.empty else pd.Series(dtype=float)
        )

    resultado = pd.DataFrame(
        {
            f"Faturamento_{ano_anterior}": _serie(df_ano_anterior),
            f"Faturamento_YTD_{ano_anterior}": _serie(df_ytd_ano_anterior),
            f"Faturamento_YTD_{ano_atual}": _serie(df_ytd_atual),
        }
    ).fillna(0.0)
    resultado["Evolucao_YTD_Pct"] = (
        resultado[f"Faturamento_YTD_{ano_atual}"] - resultado[f"Faturamento_YTD_{ano_anterior}"]
    ) / resultado[f"Faturamento_YTD_{ano_anterior}"].replace(0, pd.NA)
    return resultado.reset_index().sort_values(f"Faturamento_YTD_{ano_atual}", ascending=False)


def top_clientes_periodo(
    data_inicio: date,
    data_fim: date,
    n: int = 20,
    tipo_cliente: Optional[str] = None,
    filtros: Optional[dict[str, str]] = None,
) -> pd.DataFrame:
    """Top N clientes por Faturamento no período, com preço médio e unidades."""
    params: dict[str, object] = {"data_inicio": data_inicio, "data_fim": data_fim}
    where_filtros, precisa_produto, precisa_material = _aplicar_filtros(filtros, params)
    where_tipo_cliente = ""
    if tipo_cliente == "Governo":
        where_tipo_cliente = f" AND ({_CANAL_VENDA_SQL}) IN ('MS', 'Publico')"
    elif tipo_cliente == "Privado":
        where_tipo_cliente = f" AND ({_CANAL_VENDA_SQL}) = 'Privado'"
    join_produto = _JOIN_PRODUTO if precisa_produto else ""
    join_material = _JOIN_MATERIAL_SAP if precisa_material else ""
    query = f"""
        WITH {_CTE_ESTRUTURA_COMERCIAL}
        SELECT TOP {int(n)}
            COALESCE(cl.Nome_Cliente, 'NAO INFORMADO') AS Nome_Cliente,
            SUM(f.Valor_Liquido_Faturamento) AS Valor_Faturado,
            SUM(f.Qtd_Faturada) AS Qtd_Faturada,
            SUM(f.Valor_Liquido_Faturamento) / NULLIF(SUM(f.Qtd_Faturada), 0) AS Preco_Medio
        FROM {SCHEMA}.fct_faturamento_itens_sap f
        {_JOIN_CLIENTE_ESTRUTURA}
        {join_produto}
        {join_material}
        WHERE f.Data_Faturamento BETWEEN :data_inicio AND :data_fim
            {where_tipo_cliente}{where_filtros}
        GROUP BY cl.Nome_Cliente
        ORDER BY Valor_Faturado DESC
        OPTION (RECOMPILE)
    """  # nosec B608
    return read_sql(query, database="GOLD", params=params)


def skus_ativos_periodo(
    data_inicio: date,
    data_fim: date,
    tipo_cliente: Optional[str] = None,
    filtros: Optional[dict[str, str]] = None,
) -> pd.DataFrame:
    """Contagem de SKUs (produtos distintos) faturados e clientes atendidos, por mês."""
    params: dict[str, object] = {"data_inicio": data_inicio, "data_fim": data_fim}
    where_filtros, precisa_produto, precisa_material = _aplicar_filtros(filtros, params)
    where_tipo_cliente = ""
    if tipo_cliente == "Governo":
        where_tipo_cliente = f" AND ({_CANAL_VENDA_SQL}) IN ('MS', 'Publico')"
    elif tipo_cliente == "Privado":
        where_tipo_cliente = f" AND ({_CANAL_VENDA_SQL}) = 'Privado'"
    join_produto = _JOIN_PRODUTO if precisa_produto else ""
    join_material = _JOIN_MATERIAL_SAP if precisa_material else ""
    query = f"""
        WITH {_CTE_ESTRUTURA_COMERCIAL}
        SELECT
            FORMAT(f.Data_Faturamento, 'yyyy-MM') AS Mes,
            COUNT(DISTINCT f.Codigo_Produto) AS Qtd_SKUs_Vendidos,
            COUNT(DISTINCT f.Codigo_Cliente) AS Qtd_Clientes_Atendidos
        FROM {SCHEMA}.fct_faturamento_itens_sap f
        {_JOIN_CLIENTE_ESTRUTURA}
        {join_produto}
        {join_material}
        WHERE f.Data_Faturamento BETWEEN :data_inicio AND :data_fim
            {where_tipo_cliente}{where_filtros}
        GROUP BY FORMAT(f.Data_Faturamento, 'yyyy-MM')
        ORDER BY Mes
        OPTION (RECOMPILE)
    """  # nosec B608
    return read_sql(query, database="GOLD", params=params)


# Colunas de `relatorio_analitico` — nome exibido -> expressão SQL. Whitelist na mesma linha
# de `DIMENSOES_FATURAMENTO`, mas aqui é grão linha (1 linha = 1 item de fatura).
COLUNAS_RELATORIO_ANALITICO = {
    "Ano": "YEAR(f.Data_Faturamento)",
    "Mês": "MONTH(f.Data_Faturamento)",
    "Data Faturamento": "f.Data_Faturamento",
    "Nr Pedido": "f.Numero_Pedido_Origem",
    "Nota Fiscal": "f.Numero_NFe",
    "Canal": _CANAL_VENDA_SQL,
    "Linha de Negócio": _expr_dimensao_hierarquia("org_vendas"),
    "Divisional": _expr_dimensao_hierarquia("divisional"),
    "Regional": _expr_dimensao_hierarquia("regional"),
    "Distrital": _expr_dimensao_hierarquia("distrital"),
    "Setor": _expr_dimensao_hierarquia("descricao"),
    "Cód Cliente": "f.Codigo_Cliente",
    "CNPJ": "cl.CNPJ_CPF",
    "Nome Cliente": "cl.Nome_Cliente",
    "Estado (UF)": "cl.Estado_UF",
    "Família": "p.familia",
    "Cód Produto": "f.Codigo_Produto",
    "Nome Produto": "m.Descricao_Produto",
    "Tipo Documento Faturamento": "f.Tipo_Documento_Faturamento",
    "Qtd Faturada": "f.Qtd_Faturada",
    "Valor Faturado": "f.Valor_Liquido_Faturamento",
    "Preço Unitário": "f.Valor_Unitario_Faturado",
}

# Colunas de/pra que dependem do JOIN extra de dim_produto/dim_material_sap (as demais já
# vêm de `_JOIN_CLIENTE_ESTRUTURA`, sempre presente, ou de coluna nativa do fato).
_COLUNAS_PRECISAM_PRODUTO = {"Família"}
_COLUNAS_PRECISAM_MATERIAL = {"Nome Produto"}

# "Nome Produto" fica fora do padrão de propósito: é a única coluna que exige o JOIN com
# `dim_material_sap` (~80 mil linhas) — mesmo resolvido em 2 passos (ver `relatorio_analitico`)
# pra evitar o plano catastrófico do otimizador, ainda é a parte mais lenta da consulta.
# Continua disponível pra quem quiser marcar.
COLUNAS_RELATORIO_ANALITICO_PADRAO = [
    "Data Faturamento",
    "Nr Pedido",
    "Nota Fiscal",
    "Canal",
    "Nome Cliente",
    "Família",
    "Cód Produto",
    "Qtd Faturada",
    "Valor Faturado",
]


def relatorio_analitico(
    data_inicio: date,
    data_fim: date,
    colunas: Optional[list[str]] = None,
    tipo_cliente: Optional[str] = None,
    filtros: Optional[dict[str, str]] = None,
    limit: int = 5000,
) -> pd.DataFrame:
    """Detalhe linha a linha de `vendas_sap.fct_faturamento_itens_sap` (1 linha = 1 item de
    fatura), com as dimensões comerciais já resolvidas — pra inspecionar/exportar linhas
    específicas, não pra métrica de dashboard (que sempre agrega).

    Args:
        colunas: quais chaves de `COLUNAS_RELATORIO_ANALITICO` trazer (None = todas). A
            ordem do resultado segue a ordem de `colunas`.
        filtros: ver `faturamento_por_dimensao` / `_aplicar_filtros`.
        limit: teto de linhas (ordenado por data mais recente primeiro) — se
            `len(df) == limit`, o resultado está truncado.
    """
    colunas_selecionadas = colunas or list(COLUNAS_RELATORIO_ANALITICO)
    invalidas = [c for c in colunas_selecionadas if c not in COLUNAS_RELATORIO_ANALITICO]
    if invalidas:
        raise ValueError(
            f"colunas inválidas: {invalidas} (deve ser de {list(COLUNAS_RELATORIO_ANALITICO)})"
        )

    params: dict[str, object] = {"data_inicio": data_inicio, "data_fim": data_fim}
    where_filtros, precisa_produto_filtro, precisa_material_filtro = _aplicar_filtros(
        filtros, params
    )
    where_tipo_cliente = ""
    if tipo_cliente == "Governo":
        where_tipo_cliente = f" AND ({_CANAL_VENDA_SQL}) IN ('MS', 'Publico')"
    elif tipo_cliente == "Privado":
        where_tipo_cliente = f" AND ({_CANAL_VENDA_SQL}) = 'Privado'"

    precisa_produto = precisa_produto_filtro or any(
        c in _COLUNAS_PRECISAM_PRODUTO for c in colunas_selecionadas
    )
    join_produto = _JOIN_PRODUTO if precisa_produto else ""

    # "Nome Produto" (`dim_material_sap`) é tratado à parte: testado ao vivo (2026-08-25) que
    # juntar essa tabela (~80 mil linhas) *na mesma consulta* que já tem a CTE de crosswalk +
    # TOP N + ORDER BY faz o otimizador do SQL Server escolher um plano catastrófico
    # (>100s, contra ~3s pra tudo o resto igual sem esse JOIN) — mesmo com `OPTION
    # (RECOMPILE)`. Resolvido igual, mas em 2 passos: TOP N primeiro (rápido), junta
    # `dim_material_sap` só nas linhas já selecionadas depois (rápido de novo). Só não dá pra
    # fazer isso quando "Produto" é usado como **filtro** (precisa restringir as linhas antes
    # do TOP N pra dar o resultado certo) — nesse caso aceita o join mais caro dentro da
    # consulta principal mesmo.
    nome_produto_pedido = "Nome Produto" in colunas_selecionadas
    material_no_filtro = precisa_material_filtro
    colunas_base = [c for c in colunas_selecionadas if c != "Nome Produto" or material_no_filtro]
    join_material_base = _JOIN_MATERIAL_SAP if material_no_filtro else ""

    select_base = ", ".join(f"{COLUNAS_RELATORIO_ANALITICO[c]} AS [{c}]" for c in colunas_base)
    if nome_produto_pedido and not material_no_filtro:
        select_base += ", f.Mandante AS [_Mandante], f.Codigo_Produto AS [_Codigo_Produto]"

    query_base = f"""
        WITH {_CTE_ESTRUTURA_COMERCIAL}
        SELECT TOP {int(limit)} {select_base}
        FROM {SCHEMA}.fct_faturamento_itens_sap f
        {_JOIN_CLIENTE_ESTRUTURA}
        {join_produto}
        {join_material_base}
        WHERE f.Data_Faturamento BETWEEN :data_inicio AND :data_fim
            {where_tipo_cliente}{where_filtros}
        ORDER BY f.Data_Faturamento DESC
        OPTION (RECOMPILE)
    """  # nosec B608
    df = read_sql(query_base, database="GOLD", params=params)

    if nome_produto_pedido and not material_no_filtro:
        pares = df[["_Mandante", "_Codigo_Produto"]].drop_duplicates()
        if pares.empty:
            df["Nome Produto"] = pd.Series(dtype="object")
        else:
            codigos = pares["_Codigo_Produto"].unique().tolist()
            params_codigos = {f"cod_{i}": c for i, c in enumerate(codigos)}
            placeholders = ", ".join(f":{k}" for k in params_codigos)
            materiais_query = f"""
                SELECT Mandante, Codigo_Produto, Descricao_Produto
                FROM {SCHEMA}.dim_material_sap
                WHERE Codigo_Produto IN ({placeholders})
            """  # nosec B608
            df_materiais = read_sql(materiais_query, database="GOLD", params=params_codigos)
            df = df.merge(
                df_materiais.rename(
                    columns={"Mandante": "_Mandante", "Codigo_Produto": "_Codigo_Produto"}
                ),
                on=["_Mandante", "_Codigo_Produto"],
                how="left",
            ).rename(columns={"Descricao_Produto": "Nome Produto"})
        df = df.drop(columns=["_Mandante", "_Codigo_Produto"])
        df = df[colunas_selecionadas]
    return df


if __name__ == "__main__":
    print("Canal Venda — ano corrente até hoje:")
    hoje = date.today()
    print(
        faturamento_por_dimensao(hoje.replace(month=1, day=1), hoje, "Canal").to_string(index=False)
    )
