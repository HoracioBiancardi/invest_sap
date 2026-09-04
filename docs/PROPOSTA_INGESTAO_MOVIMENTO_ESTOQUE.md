# Proposta — Ingestão de histórico de estoque/movimento no `data-platform`

> Spec pra levar ao time de dados propor no repo `data-platform`
> (`/home/swordpower/Documentos/REPO/TRABALHO/data-platform`). Documento separado de
> `CONTEXTO_VENDAS_SAP.md` §11 (que registra o achado) porque este aqui é a proposta de
> implementação — schema real, config de pipeline, modelo dbt — pronta pra copiar/colar e
> ajustar, não só o resumo do achado.
>
> Duas peças independentes, pode implementar uma sem a outra: **(A)** ingestão de
> `MCHBH`/`MBEWH` (Bronze+Silver) e **(B)** o model Gold `fct_movimento_lote_sap` (usa
> `mseg`/`mkpf`, que já estão ingeridos — não depende de A).

## Contexto (resumo — ver `CONTEXTO_VENDAS_SAP.md` §11 pro achado completo)

O dashboard `invest_sap` precisou saber "quanto de estoque um material tinha numa data
passada" (investigação de pendência) e "qual a timeline de movimento de 1 lote". Hoje isso é
resolvido lendo `IB_SAPECC.MCHBH`/`MBEWH` **direto no HANA/Datasphere** (via
`scripts/trace_lote.py`, mesma conexão de `scripts/ddic_lookup.py`) — funciona, mas é uma
consulta ao vivo (~5-10s) fora do Data Warehouse, sem histórico de auditoria, sem `dbt test`,
e sem poder ser usada por outros projetos que só têm acesso ao SQL Server (`BRONZE`/
`SILVER`/`GOLD`), não ao HANA.

## Parte A — Ingerir `MCHBH`/`MBEWH` (Bronze + Silver)

### A.1 O que são

Confirmado ao vivo em 2026-09-03 via `SYS.VIEW_COLUMNS` no HANA/Datasphere
(`schema='IB_SAPECC'`):

**`MCHBH`** ("Estoques de lotes - histórico", 798.364 linhas) — snapshot de FECHAMENTO DE
PERÍODO por lote, grão `MANDT+MATNR+WERKS+LGORT+CHARG+LFGJA+LFMON`:

| Campo | Tipo | Significado |
|---|---|---|
| `MANDT` | NVARCHAR | Mandante |
| `MATNR` | NVARCHAR | Material |
| `WERKS` | NVARCHAR | Centro |
| `LGORT` | NVARCHAR | Depósito |
| `CHARG` | NVARCHAR | Lote |
| `LFGJA` | NVARCHAR | Ano do período de fechamento |
| `LFMON` | NVARCHAR | Mês do período de fechamento ('01'-'12') |
| `CLABS` | DECIMAL | Estoque livre (unrestricted) |
| `CUMLM` | DECIMAL | Estoque em trânsito/transferência |
| `CINSM` | DECIMAL | Estoque em qualidade |
| `CEINM` | DECIMAL | Estoque de uso restrito |
| `CSPEM` | DECIMAL | Estoque bloqueado |
| `CRETM` | DECIMAL | Estoque de devolução bloqueado |

Mesmas colunas de quantidade de `MCHB` (já ingerida, grupo `mm_daily_X`) — `MCHBH` é
literalmente o histórico por período dessa mesma tabela.

**`MBEWH`** ("Avaliação de material - histórico", 1.816.404 linhas) — equivalente de
`MBEW` (já ingerida) por período, grão `MANDT+MATNR+BWKEY+BWTAR+LFGJA+LFMON`:

| Campo | Tipo | Significado |
|---|---|---|
| `MANDT` | NVARCHAR | Mandante |
| `MATNR` | NVARCHAR | Material |
| `BWKEY` | NVARCHAR | Área de avaliação (= Centro nesta base, ver `CONTEXTO_VENDAS_SAP.md` §6.9) |
| `BWTAR` | NVARCHAR | Tipo de avaliação |
| `LFGJA` | NVARCHAR | Ano do período |
| `LFMON` | NVARCHAR | Mês do período |
| `LBKUM` | DECIMAL | Estoque total valorizado (quantidade) |
| `SALK3` | DECIMAL | Valor total do estoque |
| `VPRSV` | NVARCHAR | Controle de preço ('S'=padrão, 'V'=média móvel) |
| `VERPR` | DECIMAL | Preço médio móvel |
| `STPRS` | DECIMAL | Preço padrão |
| `PEINH` | DECIMAL | **Unidade de preço — mesmo achado de bug do `MBEW` (§6.9): dividir sempre, `NULLIF(PEINH,0)` com fallback 1** |
| `BKLAS` | NVARCHAR | Classe de valorização |
| `SALKV` | DECIMAL | Valor de estoque (avaliação comercial) |
| `VKSAL` | DECIMAL | Estoque avaliado a preço de venda |

⚠️ **`PEINH` tem o mesmo achado de bug já documentado pra `MBEW`** (`CONTEXTO_VENDAS_SAP.md`
§6.9: preço é por `PEINH` unidades, não por unidade — dividir sem isso infla o custo
unitário, achado real de 10.000x num material). Qualquer model que use `VERPR`/`STPRS` de
`MBEWH` **precisa** da mesma correção: `(CASE WHEN VERPR>0 THEN VERPR ELSE STPRS END) /
NULLIF(COALESCE(PEINH,1),0)`.

### A.2 Bronze — novo grupo de ingestão

Nenhuma das duas está em nenhum grupo de `data_pipelines/ingestion/bronze/pipelines/
dataspherev3/config.py` hoje. Proposta — novo grupo mensal (só mudam no fechamento de
período, não faz sentido rodar mais frequente que isso; `load_type="replace"` porque não
há uma coluna de data nativa pra filtro incremental — `LFGJA`+`LFMON` são campos separados,
não uma data — e o volume (798k/1,8M linhas) é pequeno o bastante pra full reload mensal
sair barato, mesmo padrão de `MCHB`/`MBEW` no grupo `mm_daily_X`, que também usam
`"replace"`):

```python
"datasphere_mm_monthly": {
    "schedule": "0 6 1 * *",  # dia 1 de cada mês, 06h — período anterior já fechou
    "dataset_suffix": "mm_monthly",
    "schema": "IB_SAPECC",
    "tables": [
        create_table(
            "MCHBH", ["MANDT", "MATNR", "WERKS", "LGORT", "CHARG", "LFGJA", "LFMON"], "replace"
        ),
        create_table(
            "MBEWH", ["MANDT", "MATNR", "BWKEY", "BWTAR", "LFGJA", "LFMON"], "replace"
        ),
    ],
},
```

Adicionar em `PIPELINE_ORCHESTRATION_CONFIG` (mesmo dicionário de `mm_daily_X`/
`mm_daily_h7`/`mm_master_daily_h12`, ver `CONTEXTO_VENDAS_SAP.md` §5).

### A.3 Silver — 2 models novos

Mesmo padrão exato de `airflow/dags/dbt/models/silver/dataspherev2/mchb/mchb.sql`
(materialized incremental, `delete+insert`, `unique_key='hash_pk'`).

`airflow/dags/dbt/models/silver/dataspherev2/mchbh/mchbh.sql`:

```sql
{{
   config(
        tags=['dataspherev2', 'silver'],
        alias='mchbh',
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key='hash_pk'
    )
}}

SELECT
    {{ nullif_empty('MANDT') }} AS mandt,
    {{ nullif_empty('MATNR') }} AS matnr,
    {{ nullif_empty('WERKS') }} AS werks,
    {{ nullif_empty('LGORT') }} AS lgort,
    {{ nullif_empty('CHARG') }} AS charg,
    {{ nullif_empty('LFGJA') }} AS lfgja,
    {{ nullif_empty('LFMON') }} AS lfmon,
    {{ to_decimal('CLABS') }} AS clabs,
    {{ to_decimal('CUMLM') }} AS cumlm,
    {{ to_decimal('CINSM') }} AS cinsm,
    {{ to_decimal('CEINM') }} AS ceinm,
    {{ to_decimal('CSPEM') }} AS cspem,
    {{ to_decimal('CRETM') }} AS cretm,

    {{ to_timestamp('dt_ingestao') }} AS dt_ingestao,
    hash_pk,
    source

FROM {{ source('dataspherev2', 'mchbh') }}
```

`airflow/dags/dbt/models/silver/dataspherev2/mbewh/mbewh.sql`:

```sql
{{
   config(
        tags=['dataspherev2', 'silver'],
        alias='mbewh',
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key='hash_pk'
    )
}}

SELECT
    {{ nullif_empty('MANDT') }} AS mandt,
    {{ nullif_empty('MATNR') }} AS matnr,
    {{ nullif_empty('BWKEY') }} AS bwkey,
    {{ nullif_empty('BWTAR') }} AS bwtar,
    {{ nullif_empty('LFGJA') }} AS lfgja,
    {{ nullif_empty('LFMON') }} AS lfmon,
    {{ to_decimal('LBKUM') }} AS lbkum,
    {{ to_decimal('SALK3') }} AS salk3,
    {{ nullif_empty('VPRSV') }} AS vprsv,
    {{ to_decimal('VERPR') }} AS verpr,
    {{ to_decimal('STPRS') }} AS stprs,
    {{ to_decimal('PEINH') }} AS peinh,
    {{ nullif_empty('BKLAS') }} AS bklas,
    {{ to_decimal('SALKV') }} AS salkv,
    {{ to_decimal('VKSAL') }} AS vksal,

    {{ to_timestamp('dt_ingestao') }} AS dt_ingestao,
    hash_pk,
    source

FROM {{ source('dataspherev2', 'mbewh') }}
```

Cada 1 precisa também do `.yml` companheiro (`sources: dataspherev2.mchbh`/`mbewh`,
`database: BRONZE`) — copiar a estrutura de `mchb.yml`, ajustando nomes/descrições de campo
pela tabela acima.

### A.4 Depois da ingestão (fora de escopo desta proposta, mas o motivo de existir)

Com `SILVER.dataspherev2.mchbh` existindo, `scripts/trace_lote.py::
estoque_historico_material_centro()` do dashboard `invest_sap` troca a consulta HANA ao vivo
(`read_hana_sql`) por uma consulta `SILVER` normal (`read_sql`) — mais rápido, sem depender
de VPN/HANA disponível na hora, com todo o benefício de estar num Data Warehouse de verdade
(histórico de carga, `dbt test`, acessível a outros projetos).

## Parte B — Model Gold `fct_movimento_lote_sap` (histórico de movimento por lote)

Não depende da Parte A — fonte é `mseg`/`mkpf`, **já ingeridos** (`SILVER.dataspherev2.mseg`/
`mkpf`, grupo `mm_daily_h7`). Nenhuma tabela GOLD hoje guarda a timeline de eventos de um
lote (produção → transferência → qualidade → liberação → venda) — só o estado atual
(`fct_estoque_lote_sap`) ou, com a Parte A, o fechamento mensal agregado. O rastreio de lote
do dashboard hoje resolve isso lendo `SILVER.dataspherev2.mseg`/`mkpf` direto via
`scripts/trace_lote.py::trace_lote()` — funciona, mas é uma consulta de app, não um model
reutilizável.

### B.1 Design proposto

- **Fonte**: `{{ ref('mseg') }}` (item) + `{{ ref('mkpf') }}` (cabeçalho), join por
  `mandt+mblnr+mjahr`.
- **Grão**: 1 linha por movimento de material com lote preenchido (`charg <> ''`) — ou seja,
  `Mandante+Numero_Documento(mblnr+mjahr)+Item(zeile)`.
- **Filtro de escopo**: só linhas com `charg` preenchido (descarta consumo de matéria-prima
  sem lote, centro de custo genérico, etc. — reduz volume e mantém o foco em
  rastreabilidade de produto).
- **Materialização sugerida**: incremental por `budat` (só linhas novas desde a última carga
  — `mseg` já tem ~3M linhas mesmo sem histórico gigante, ver nota de performance abaixo).

### B.2 Campos propostos

| Campo Gold | Fonte | Observação |
|---|---|---|
| `Mandante` | `mseg.mandt` | |
| `Numero_Documento_Material` | `mseg.mblnr` | |
| `Ano_Documento` | `mseg.mjahr` | |
| `Item_Documento` | `mseg.zeile` | |
| `Data_Lancamento` | `mkpf.budat` | Data de lançamento (posting date) |
| `Data_Documento` | `mkpf.bldat` | Data do documento |
| `Data_Criacao` | `mkpf.cpudt` | Data de criação no SAP |
| `Hora_Criacao` | `mkpf.cputm` | **Hora de criação (HHMMSS)** — não usado hoje em nenhum model, dá timestamp exato do lançamento |
| `Usuario` | `mkpf.usnam` | |
| `Codigo_Material` | `mseg.matnr` | |
| `Codigo_Centro` | `mseg.werks` | |
| `Codigo_Deposito` | `mseg.lgort` | |
| `Numero_Lote` | `mseg.charg` | |
| `Bwart` | `mseg.bwart` | Tipo de movimento — texto via mapa fixo (ver `scripts/trace_lote.py::DESCRICAO_BWART` no `invest_sap`; `T156T` não está replicado — considerar ingerir se o catálogo de códigos crescer) |
| `Indicador_Estoque` | `mseg.insmk` | Vazio=livre, X=qualidade, S=bloqueado |
| `Debito_Credito` | `mseg.shkzg` | S=entrada, H=saída |
| `Quantidade` | `mseg.menge` | |
| `Unidade` | `mseg.meins` | |
| `Centro_Destino` | `mseg.umwrk` | Só preenchido em transferência |
| `Deposito_Destino` | `mseg.umlgo` | |
| `Lote_Destino` | `mseg.umcha` | |
| `Pedido_Venda` | `mseg.kdauf` | Liga com `fct_pendencia_sap.Numero_Pedido` |
| `Item_Pedido_Venda` | `mseg.kdpos` | |
| `Valor_Movimento` | `mseg.dmbtr` | Opcional — valor contábil do movimento, se algum consumidor precisar |

### B.3 Casos de uso que passariam de query ad hoc a model reutilizável

1. Rastreio de lote (dashboard `invest_sap`, `scripts/trace_lote.py::trace_lote()` — hoje
   query direta, viraria `SELECT * FROM fct_movimento_lote_sap WHERE Codigo_Material=... AND
   Numero_Lote=...`).
2. `Causa_Estoque_Material` da página Pendência x Estoque (hoje recalcula
   `movimento_estoque_resumo_material_centro()` toda vez, ~90s sem cache — um model
   incremental resolveria isso de vez).
3. "Dias em qualidade" por lote (tempo entre entrada em qualidade e liberação) — hoje não
   calculado em lugar nenhum; com `Hora_Criacao` dá pra fazer isso com precisão de minuto,
   não só dia.
4. Enriquecer o "Radar de pedido zumbi" (página Pedidos do `invest_sap`) com a última
   movimentação real do material, não só a idade do pedido.

### B.4 Nota de performance (testado ao vivo, 2026-09-03)

`mseg` é columnstore (~3M linhas) **sem índice que acelere filtro seletivo por material** —
testado: filtrar por lista de 30 materiais não foi mais rápido que trazer tudo (o custo é do
`JOIN`+agregação, não da seletividade do filtro), e uma consulta sem filtro nenhum (agregando
tudo) levou ~90s numa janela de 24 meses. Um model incremental dbt (carrega só linha nova por
`budat`, não recalcula o historico inteiro a cada run) é bem mais barato que as consultas ad
hoc de hoje, que recalculam tudo toda vez.

## Quem mexe em quê

- **Parte A** (Bronze config + 2 models Silver): time de dados, repo `data-platform`
  (`data_pipelines/ingestion/bronze/pipelines/dataspherev3/config.py` +
  `airflow/dags/dbt/models/silver/dataspherev2/{mchbh,mbewh}/`).
- **Parte B** (model Gold novo): time de dados, repo `data-platform`
  (`airflow/dags/dbt/models/gold/vendas_sap/fct_movimento_lote_sap/`) — depois de existir,
  atualizar `docs/CONTEXTO_VENDAS_SAP.md` §3 (tabela de models) e trocar o consumo em
  `invest_sap` (`scripts/trace_lote.py`, `scripts/query_vendas_sap.py::
  movimento_estoque_resumo_material_centro`) de SQL direto pra `SELECT` na tabela Gold nova.
