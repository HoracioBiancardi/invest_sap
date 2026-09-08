# Proposta — Ingestão de `TCURR` (taxa de câmbio) e conversão pra BRL no `data-platform`

> Spec pra levar ao time de dados propor no repo `data-platform`
> (`/home/swordpower/Documentos/REPO/TRABALHO/data-platform`). Documento separado de
> `CONTEXTO_VENDAS_SAP.md` §10 (que registra o achado do bug de moeda) porque este aqui é a
> proposta de implementação — schema real, config de pipeline, modelo dbt — pronta pra
> copiar/colar e ajustar, não só o resumo do achado.
>
> Duas peças independentes, pode implementar uma sem a outra: **(A)** ingestão de `TCURR`
> (Bronze+Silver) e **(B)** conversão pra BRL nos models Gold multi-moeda (usa a Parte A).
>
> **Status (2026-09-08): implementado e validado com dado real.** Parte A já existia
> (ingerida em 2026-08-25) mas tinha um bug real (`gdatu` sempre `NULL`, corrigido nesta
> sessão). Parte B implementada e testada contra produção — ver
> `docs/REGRAS_E_MELHORIAS_DW.md` §5.11 e `docs/CONTEXTO_VENDAS_SAP.md` §6.12. Branch
> `feature/prioridade-pedido-carimbado-sap` (`data-platform`, commit `64509e2e`), ainda sem
> push/PR.

## Contexto (resumo — ver `CONTEXTO_VENDAS_SAP.md` §10 pro achado completo)

Investigação de 2026-09-04 (dashboard `invest_sap`, pedido do usuário: "cruzar os números
direto na base do SAP antes de confiar neles") achou que `fct_faturamento_itens_sap`/
`fct_vendas_itens_sap`/`fct_pendencia_sap` somavam valor em **5 moedas diferentes
(BRL/UYU/COP/USD/CLP) como se fossem reais**, sem filtro nem conversão — o "Faturamento
Total" do dashboard chegou a mostrar R$ 2,43 bilhões contra um real de R$ 1,04 bilhão (mesmo
período). Corrigido no app (`invest_sap`) separando/convertendo por moeda — mas a conversão
hoje é feita **fora do Data Warehouse**, direto no app:

- `scripts/query_vendas_sap.py::taxas_cambio_brl()` — consulta ao vivo em `TCURR` **direto
  no HANA/Datasphere** (mesma conexão de `scripts/ddic_lookup.py`), toda vez que uma página
  carrega.
- `scripts/query_vendas_sap.py::converter_para_brl()` — `pd.merge_asof` em pandas, taxa mais
  próxima da data de cada linha (ou do meio do período, quando a consulta agrega sem grão de
  data — perde precisão nesse caso).

Funciona, mas tem os mesmos problemas de qualquer conversão feita fora do DW: ida e volta ao
HANA toda hora, lógica de "taxa mais próxima" reimplementada em Python em vários lugares (com
precisão inconsistente — data exata quando o grão permite, aproximação quando não), sem
`dbt test`, e inacessível a qualquer outro projeto que só tenha acesso ao SQL Server.

## Parte A — Ingerir `TCURR` (Bronze + Silver)

### A.1 O que é

Confirmado ao vivo em 2026-09-04 via `SELECT` direto no HANA/Datasphere (schema
`IB_SAPECC`): **`TCURR`** ("Taxas de câmbio", 65.398 linhas) — grão
`MANDT+KURST+FCURR+TCURR+GDATU`:

| Campo | Tipo | Significado |
|---|---|---|
| `MANDT` | CLNT | Mandante |
| `KURST` | CHAR(4) | Categoria da taxa de câmbio (`'M'` = taxa média, o padrão do SAP pra conversão geral — 59.665 das 65.398 linhas; o resto é `MCA`/`EURX`/`EURO`/`P`/outros, uso pontual/histórico) |
| `FCURR` | CUKY | Moeda de procedência (origem) |
| `TCURR` | CUKY | Moeda de destino |
| `GDATU` | CHAR(8) | **Data a partir da qual a taxa é válida, armazenada invertida** — ver A.2 |
| `UKURS` | DEC | Taxa de câmbio |
| `FFACT` | DEC | Fator de unidades da moeda de procedência |
| `TFACT` | DEC | Fator de unidades da moeda de destino |

Fórmula de conversão (confirmada — `FFACT`/`TFACT` sempre `1.0` nas linhas limpas desta
base, mas a fórmula geral do SAP é esta): `valor_destino = valor_origem * UKURS * TFACT /
FFACT`.

### A.2 `GDATU` vem invertido — decodificação obrigatória

Convenção SAP clássica pra permitir "taxa válida nesta data ou na mais recente antes dela"
com busca eficiente (índice ascendente): `GDATU = 99999999 - AAAAMMDD`. Confirmado ao vivo:
`GDATU=79739095` decodifica pra `99999999 - 79739095 = 20260904` (04/09/2026, a data da
consulta). **Qualquer consumo de `TCURR` precisa decodificar isso** — não é uma data direta.

### A.3 Achado — linhas de placeholder/lixo (filtrar antes de usar)

Nem toda linha de `TCURR` é uma cotação real:

- **1.916 de 8.461 linhas** (moedas estrangeiras usadas nesta base, `KURST='M'`,
  `TCURR='BRL'`) têm `FFACT=0` e/ou `TFACT=0` — dividir por isso quebra a fórmula (`/0`).
  Concentradas nas datas mais antigas (ex.: uma linha "placeholder" datada de 2001-01-01
  com `UKURS` negativo).
- **Filtro que usa taxa real**: `WHERE KURST='M' AND UKURS > 0 AND FFACT > 0 AND TFACT > 0`.

### A.4 Cobertura real por moeda (medida ao vivo, 2026-09-04) — lacunas a documentar

Depois do filtro acima, cobertura pras moedas que aparecem em `fct_faturamento_itens_sap`/
`fct_vendas_itens_sap` desta base:

| Moeda | 1ª cotação limpa | Cobertura | Nota |
|---|---|---|---|
| USD | 2018-03-03 | ~99% dos dias desde então | Fatura em USD **anterior** a 2018-03-03 existe (desde 2014-01-15, ~26,6% do valor faturado em USD) e não tem taxa exata — precisa de fallback (taxa mais próxima disponível, ver Parte B) |
| UYU | 2018-03-03 | ~96% dos dias desde então | Fatura em UYU anterior a 2018-03-03 existe (desde 2017-05-02, ~1,6% do valor faturado em UYU) — mesmo fallback |
| COP | 2025-07-02 | ~97% dos dias desde então | Fatura em COP só começa em 2025-07-04 — cobertura praticamente completa, sem gap relevante |
| CLP | — | **nenhuma taxa real** | Só existe 1 linha de placeholder (2001-01-01, mesmo padrão do item A.3) — 8 faturas em CLP nesta base (imaterial) ficam sem conversão possível até o SAP ter uma cotação de verdade cadastrada |

### A.5 Bronze — novo grupo de ingestão

Nenhuma tabela `TCURR` está em nenhum grupo de
`data_pipelines/ingestion/bronze/pipelines/dataspherev3/config.py` hoje. Proposta — grupo
diário (a tabela é pequena — 65k linhas no total — e cotação nova é publicada todo dia útil;
`load_type="replace"` porque `GDATU` não é uma coluna de data nativa pra filtro incremental
direto, e o volume é pequeno o bastante pra full reload diário sair barato):

```python
"datasphere_fi_daily": {
    "schedule": "0 7 * * *",  # diário, 07h — depois do fechamento do dia anterior
    "dataset_suffix": "fi_daily",
    "schema": "IB_SAPECC",
    "tables": [
        create_table("TCURR", ["MANDT", "KURST", "FCURR", "TCURR", "GDATU"], "replace"),
    ],
},
```

Adicionar em `PIPELINE_ORCHESTRATION_CONFIG` (mesmo dicionário de `mm_daily_X`/
`mm_daily_h7`/`mm_master_daily_h12`, ver `CONTEXTO_VENDAS_SAP.md` §5). Se já existir um grupo
diário de tabelas FI (financeiro) na config atual, encaixar `TCURR` nele em vez de criar um
grupo novo só pra essa tabela.

### A.6 Silver — 1 model novo, já com a data decodificada

Mesmo padrão de `airflow/dags/dbt/models/silver/dataspherev2/mchb/mchb.sql`.

`airflow/dags/dbt/models/silver/dataspherev2/tcurr/tcurr.sql`:

```sql
{{
   config(
        tags=['dataspherev2', 'silver'],
        alias='tcurr',
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key='hash_pk'
    )
}}

SELECT
    {{ nullif_empty('MANDT') }} AS mandt,
    {{ nullif_empty('KURST') }} AS kurst,
    {{ nullif_empty('FCURR') }} AS fcurr,
    {{ nullif_empty('TCURR') }} AS tcurr_destino,
    -- GDATU vem invertido (99999999 - AAAAMMDD) — decodifica pra data de verdade aqui,
    -- não deixa pro consumidor Gold reinventar isso (achado 2026-09-04, invest_sap).
    DATEFROMPARTS(
        LEFT(CAST(99999999 - TRY_CAST(GDATU AS INT) AS VARCHAR(8)), 4),
        SUBSTRING(CAST(99999999 - TRY_CAST(GDATU AS INT) AS VARCHAR(8)), 5, 2),
        RIGHT(CAST(99999999 - TRY_CAST(GDATU AS INT) AS VARCHAR(8)), 2)
    ) AS data_taxa,
    {{ to_decimal('UKURS') }} AS ukurs,
    {{ to_decimal('FFACT') }} AS ffact,
    {{ to_decimal('TFACT') }} AS tfact,

    {{ to_timestamp('dt_ingestao') }} AS dt_ingestao,
    hash_pk,
    source

FROM {{ source('dataspherev2', 'tcurr') }}
```

Companheiro `.yml` — copiar estrutura de `mchb.yml` (`sources: dataspherev2.tcurr`,
`database: BRONZE`), ajustando campos pela tabela do item A.1.

## Parte B — Conversão pra BRL nos models Gold multi-moeda

Depende da Parte A (usa `SILVER.dataspherev2.tcurr`).

### B.1 Onde aplicar

As 3 fatos que carregam valor em moeda do documento sem converter, todas com coluna `Moeda`
nativa: `fct_faturamento_itens_sap` (`Valor_Liquido_Faturamento`), `fct_vendas_itens_sap`
(`Valor_Liquido_Pedido`), e por herança `fct_pendencia_sap` (que deriva de
`fct_vendas_itens_sap`, ver `CONTEXTO_VENDAS_SAP.md` §3).

### B.2 Design proposto — CTE de taxa limpa + join "taxa vigente na data" via `CROSS APPLY`

```sql
WITH taxa_limpa AS (
    SELECT fcurr, tcurr_destino, data_taxa, ukurs * tfact / ffact AS taxa_brl
    FROM {{ ref('tcurr') }}
    WHERE kurst = 'M' AND tcurr_destino = 'BRL'
        AND ukurs > 0 AND ffact > 0 AND tfact > 0  -- acháo A.3, nunca remover este filtro
)
SELECT
    f.*,
    CASE WHEN f.moeda = 'BRL' THEN f.valor_liquido_faturamento ELSE f.valor_liquido_faturamento * tc.taxa_brl END
        AS valor_liquido_faturamento_brl,
    tc.taxa_brl,
    tc.data_taxa AS taxa_brl_data_referencia  -- auditoria: qual taxa foi usada
FROM {{ ref('fct_faturamento_itens_sap_staging') }} f
OUTER APPLY (
    SELECT TOP 1 taxa_brl, data_taxa
    FROM taxa_limpa t
    WHERE t.fcurr = f.moeda
    ORDER BY ABS(DATEDIFF(day, t.data_taxa, f.data_faturamento)) ASC
)
```

`OUTER APPLY` com `ORDER BY` na diferença absoluta de dias replica o `direction="nearest"`
do `pd.merge_asof` usado hoje no app (`converter_para_brl`) — pega a taxa exata do dia se
existir, senão a mais próxima (antes ou depois), cobrindo os gaps documentados no item A.4
sem deixar de converter. Índice em `tcurr(fcurr, data_taxa)` deixa isso eficiente mesmo em
fato com milhões de linhas (diferente do `mseg` sem índice útil, citado como problema em
`PROPOSTA_INGESTAO_MOVIMENTO_ESTOQUE.md`).

`taxa_brl_data_referencia` value a pena expor: deixa auditável quando a conversão usou a
taxa exata do dia vs. uma aproximação (gap de cobertura pré-2018 pra USD/UYU, ou CLP sem
taxa nenhuma — nesse último caso `tc.taxa_brl` fica `NULL`, e `valor..._brl` deveria virar
`NULL` também, não silenciosamente 0, pra quem consumir saber que não converteu).

### B.3 Depois de implementado — o que muda no `invest_sap`

- `scripts/query_vendas_sap.py::taxas_cambio_brl()`/`converter_para_brl()` (consulta HANA +
  `merge_asof` em pandas) somem — todo `SUM(Valor_..._BRL)` já vem pronto do Gold, um
  `SELECT` normal em vez de round-trip ao HANA + lógica Python.
- Todo o padrão "achado 2026-09-04, converte com `render_valor_convertido_brl`" espalhado
  pelas páginas (`0_Home.py`, `12_Painel_Vendas.py`, `22_Faturamento.py`,
  `scripts/ui_theme.py::render_valor_convertido_brl`) fica mais simples: sem round-trip
  extra, com taxa exata por transação (hoje o app aproxima pra taxa do meio do período nas
  consultas que agregam sem grão de data — `faturamento_por_org_vendas_linha_negocio`,
  `top_clientes_periodo`, etc. — porque não dá pra fazer join com o HANA a partir do SQL
  Server; no Gold isso deixa de ser um problema).
- `meta_vs_realizado_mensal`/`meta_vs_realizado_por_dimensao` (hoje fazem 2 consultas +
  merge em pandas só pra evitar juntar Meta com Realizado multi-moeda antes de converter —
  ver docstring de ambas) voltam a ser 1 query só.

## Quem mexe em quê

- **Parte A** (Bronze config + 1 model Silver): time de dados, repo `data-platform`
  (`data_pipelines/ingestion/bronze/pipelines/dataspherev3/config.py` +
  `airflow/dags/dbt/models/silver/dataspherev2/tcurr/`).
- **Parte B** (colunas `_brl`/`taxa_brl`/`taxa_brl_data_referencia` nos 3 models Gold
  afetados): time de dados, repo `data-platform`
  (`airflow/dags/dbt/models/gold/vendas_sap/{fct_faturamento_itens_sap,fct_vendas_itens_sap,
  fct_pendencia_sap}/`) — depois de existir, atualizar `docs/CONTEXTO_VENDAS_SAP.md` §3/§10
  e trocar o consumo em `invest_sap` (`scripts/query_vendas_sap.py`,
  `scripts/query_faturamento_comercial.py`, `scripts/ui_theme.py::render_valor_convertido_brl`)
  de conversão em pandas pra `SELECT` direto na coluna `_brl` do Gold.
