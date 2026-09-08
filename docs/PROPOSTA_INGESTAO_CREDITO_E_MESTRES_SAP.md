# Proposta — Ingestão de `VBUK` (crédito nativo), `T001K` (moeda por centro) e `T003T` (texto de tipo de documento) no `data-platform`

> Spec pra levar ao time de dados propor no repo `data-platform`
> (`/home/swordpower/Documentos/REPO/TRABALHO/data-platform`). Documento separado de
> `docs/REGRAS_E_MELHORIAS_DW.md` (que registra os achados, §4.1/§4.9/§3.8) porque este aqui é
> a proposta de implementação — schema real, config de pipeline, modelo dbt — pronta pra
> copiar/colar e ajustar, mesmo formato de `PROPOSTA_CONVERSAO_CAMBIO_TCURR.md` e
> `PROPOSTA_INGESTAO_MOVIMENTO_ESTOQUE.md`.
>
> 3 peças independentes, pode implementar uma sem as outras: **(A)** `VBUK` (status de
> crédito nativo por pedido), **(B)** `T001K` (de-para oficial centro→empresa→moeda), **(C)**
> `T003T` (texto de tipo de documento contábil). Todas confirmadas com dado real ao vivo no
> HANA/Datasphere (`IB_SAPECC`) em 2026-09-05.
>
> **Status (2026-09-08): código implementado (Bronze+Silver+Gold), mas SEM validação de
> dado real** — as 3 tabelas são novas na ingestão; ficam sem nenhuma linha até a extração
> Bronze→Silver rodar em produção pelo menos 1 vez (fora do alcance de uma sessão sem acesso
> pra disparar Airflow). Ver `docs/REGRAS_E_MELHORIAS_DW.md` §5.11. Branch
> `feature/prioridade-pedido-carimbado-sap` (`data-platform`, commit `ac897d2f`), ainda sem
> push/PR. Reconferir com dado real assim que a extração rodar, antes de confiar nos campos
> Gold que dependem delas.

## Contexto (resumo — ver `docs/REGRAS_E_MELHORIAS_DW.md` pros achados completos)

Investigação de 2026-09-05 no dashboard `invest_sap` testou uma bateria de tabelas/campos SAP
ainda não usados em nenhum model, procurando substitutos/complementos mais precisos pra
problemas já conhecidos (crédito, moeda, texto de crédito/devolução). 3 tabelas confirmaram
achado forte e real, mas nenhuma está no pipeline de ingestão hoje (`data_pipelines/ingestion/
bronze/pipelines/dataspherev3/config.py`) — só acessíveis via consulta direta ao HANA
(`read_hana_sql`, mesma conexão de `scripts/ddic_lookup.py`), sem histórico de carga, sem
`dbt test`, inacessíveis a quem só tem acesso ao SQL Server (BRONZE/SILVER/GOLD).

## Parte A — Ingerir `VBUK` (status de crédito nativo por pedido)

### A.1 O que é

Confirmado ao vivo em 2026-09-05 via `DD02T`/`DD03L` no HANA/Datasphere: **`VBUK`**
("Documento SD: status de cabeçalho e dados administrativos", **1.048.896 linhas**) — grão
`MANDT+VBELN`, 1 linha por documento SD (cobre pedido, remessa e fatura no mesmo número de
documento — não é exclusivo de pedido de venda).

Achado que motiva a ingestão (ver `docs/REGRAS_E_MELHORIAS_DW.md` §4.1): campo `CMGST`
("Status global das verificações de crédito") — veredito de crédito que o próprio SAP já
calcula por documento, com textos oficiais confirmados (`DD07T`, domínio `CMGST`):

| `CMGST` | Texto oficial (PT) | Contagem ao vivo (2026-09-05) |
|---|---|---|
| *(vazio)* | Verif.crédito não foi efetuada/status não foi definido | 831.838 |
| `A` | Verif.crédito foi efetuada, operação oK | 85.881 |
| `B` | Verif.crédito foi efetuada, operação **não oK** | **2.978** |
| `C` | Verif.crédito efetuada, operação não oK, liberação parcial | (dentro do total acima) |
| `D` | Operação liberada pelo responsável crédito | 128.195 |

`B` é bloqueio de crédito ativo confirmado pelo próprio SAP — hoje `fct_limite_credito_sap`
só permite inferir isso pelo sinal de `Valor_Credito_Disponivel` (regra 1.2.1 do DW doc), sem
o veredito explícito por documento. `D` é achado colateral: 128 mil documentos que passaram
por bloqueio e foram liberados manualmente — sinal de fricção de processo de crédito.

### A.2 Campos propostos pra Silver (subconjunto útil, não a tabela inteira)

`VBUK` tem 83 campos no total — a maioria é status técnico interno de baixíssimo uso
(reservas de cliente, flags de "incompleto" por módulo, campos marcados `UNUSED` no próprio
DDIC). Proposta: replicar em Silver só os campos abaixo, que têm valor de negócio direto pra
`vendas_sap` (fácil de ampliar depois se aparecer necessidade — é o mesmo padrão incremental
já usado no projeto):

| Campo | Tipo (DDIC) | Descrição oficial |
|---|---|---|
| `MANDT` | CLNT(3) | Mandante |
| `VBELN` | CHAR(10) | Número do documento de vendas e distribuição (chave, liga com `VBAK.VBELN`/`LIKP.VBELN`/`VBRK.VBELN` dependendo do `VBTYP`) |
| `VBTYP` | CHAR(1) | Categoria de documento de vendas e distribuição (`C`=pedido, `J`=remessa, `M`=fatura, etc. — decide contra qual fato dar join) |
| `GBSTK` | CHAR(1) | Status global de processamento do documento (visão agregada SAP do "andamento" do documento) |
| `LFSTK` | CHAR(1) | Status de remessa |
| `FKSTK` | CHAR(1) | Status do faturamento |
| `WBSTK` | CHAR(1) | Status de movimento de mercadoria global |
| `BESTK` | CHAR(1) | Status de confirmação |
| `CMGST` | CHAR(1) | **Status global das verificações de crédito** — o campo-alvo desta proposta |
| `AEDAT` | DATS(8) | Data da última modificação do status |

### A.3 Bronze — novo grupo de ingestão

Nenhuma tabela `VBUK` está em nenhum grupo hoje. Proposta — encaixar no mesmo grupo horário
de `VBAK`/`VBAP` (`sd_hourly_h10`, `CONTEXTO_VENDAS_SAP.md` §5), já que `CMGST`/status mudam
na mesma cadência que o pedido em si (bloqueio/liberação de crédito acontece minutos depois
da criação do pedido, não faz sentido rodar mais devagar):

```python
"sd_hourly_h10": {
    # ... tabelas já existentes: VBAK, VBAP, VBUP, VBBE, VBEP ...
    "tables": [
        # ... entradas já existentes ...
        create_table("VBUK", ["MANDT", "VBELN"], "upsert"),  # incremental por AEDAT
    ],
},
```

Carga incremental por `AEDAT` (data da última modificação de status) — mesmo `range_columns`
padrão de `VBAK` nesse grupo (`AEDAT`, janela `LAST_14_DAYS` até `TODAY`, ver
`CONTEXTO_VENDAS_SAP.md` §5).

### A.4 Silver — model novo

Mesmo padrão de `airflow/dags/dbt/models/silver/dataspherev2/mchb/mchb.sql`.

`airflow/dags/dbt/models/silver/dataspherev2/vbuk/vbuk.sql`:

```sql
{{
   config(
        tags=['dataspherev2', 'silver'],
        alias='vbuk',
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key='hash_pk'
    )
}}

SELECT
    {{ nullif_empty('MANDT') }} AS mandt,
    {{ nullif_empty('VBELN') }} AS vbeln,
    {{ nullif_empty('VBTYP') }} AS vbtyp,
    {{ nullif_empty('GBSTK') }} AS gbstk,
    {{ nullif_empty('LFSTK') }} AS lfstk,
    {{ nullif_empty('FKSTK') }} AS fkstk,
    {{ nullif_empty('WBSTK') }} AS wbstk,
    {{ nullif_empty('BESTK') }} AS bestk,
    {{ nullif_empty('CMGST') }} AS cmgst,
    {{ to_timestamp('AEDAT') }} AS aedat,

    {{ to_timestamp('dt_ingestao') }} AS dt_ingestao,
    hash_pk,
    source

FROM {{ source('dataspherev2', 'vbuk') }}
```

Companheiro `.yml` (`sources: dataspherev2.vbuk`, `database: BRONZE`) — copiar estrutura de
`mchb.yml`, ajustando pelos campos da tabela A.2.

### A.5 Gold — onde expor

Adicionar `Status_Credito_Documento_SAP` (de `cmgst`, com texto via `CASE`/tabela de domínio
— `DD07T` não é replicada, então o texto vira um `CASE` fixo no model, igual já se faz com
outros domínios fixos do projeto) em `fct_pendencia_sap` (join por `Mandante+Numero_Pedido =
vbuk.mandt+vbeln`, filtrando `vbtyp` do tipo pedido) — ou, se fizer mais sentido pro time de
dados, como campo novo em `fct_limite_credito_sap`. Ganho: sinal de crédito **por pedido**,
mais granular que o limite por cliente+área de crédito que já existe lá.

## Parte B — Ingerir `T001K` (de-para oficial centro→empresa→moeda)

### B.1 O que é

Confirmado ao vivo: **`T001K`** ("Área de avaliação", **26 linhas** — tabela mestre pequena,
praticamente estática) — grão `MANDT+BWKEY`. É o de-para formal que
`PROPOSTA_CONVERSAO_CAMBIO_TCURR.md` (§A.4) e `CONTEXTO_VENDAS_SAP.md` §6.9(2) registraram
como **"não replicado nesta base"**, obrigando o app a usar uma heurística
(`Codigo_Centro→Pais_Centro` de `dim_centro_sap`) pra saber a moeda de cada centro.

Amostra real (join `T001K→T001.WAERS`, `WAERS` = moeda do código de empresa):

| `BWKEY` (Centro) | `BUKRS` (Empresa) | `WAERS` (Moeda) | Empresa |
|---|---|---|---|
| 1000-1900, 2200, 2300, 2350, R100 | 1000 | BRL | Blau Farmacêutica |
| 1700 | BR01 | BRL | Blau Farmacêutica Goiás |
| 2000, 2100, 2400, 2500, 2600, 2700 | UR01 | UYU | Blaufarma Uruguay S.A. |
| CO10 | CO10 | COP | Blau Farmacéutica Colômbia |
| BG01, BG02 | BG01 | BRL | **Bergamo Farmacêutica** (empresa do grupo não documentada antes desta investigação) |
| 3000 | IICT | BRL | **ICT - Ins.Cie.Tec. e Inov.** (idem — tem funcionários próprios confirmados via `PA0001`) |

Bate com a heurística `Pais_Centro` atual nos centros já conhecidos — a ingestão não corrige
um erro existente, **formaliza** o mapeamento (permite `dbt test` de integridade, elimina a
dependência de um campo textual de nome de país) e revela as 2 empresas acima que nunca
tinham aparecido em nenhum documento do projeto.

### B.2 Campos propostos

Tabela pequena — replicar por completo é barato. Campos com valor de negócio (o resto —
`BWMOD`, `XBKNG`, `MLBWA` etc. — é configuração de Ledger de Materiais, fora de escopo):

| Campo | Tipo (DDIC) | Descrição oficial |
|---|---|---|
| `MANDT` | CLNT(3) | Mandante |
| `BWKEY` | CHAR(4) | Área de avaliação (= `Codigo_Centro` nesta configuração SAP, ver `CONTEXTO_VENDAS_SAP.md` §6.9) |
| `BUKRS` | CHAR(4) | Empresa |

`T001` (empresa→moeda, `WAERS`) **já teria que ser confirmada/ingerida também** — não estava
no escopo desta investigação verificar se já existe replicada; se não estiver, é o mesmo
padrão de ingestão pequena (tabela mestre, poucas dezenas de linhas) a acrescentar junto.

### B.3 Bronze — novo grupo de ingestão

Tabela mestre estática — mesmo padrão de `T001W` (já ingerida no grupo `master_daily_h9`,
`CONTEXTO_VENDAS_SAP.md` §5). Proposta — encaixar no mesmo grupo, sem criar grupo novo:

```python
"master_daily_h9": {
    # ... tabelas já existentes: KNA1, KNVV, MARA, MAKT, LFA1, T001W, ... ...
    "tables": [
        # ... entradas já existentes ...
        create_table("T001K", ["MANDT", "BWKEY"], "replace"),  # tabela mestre pequena (26 linhas), full reload diário sai barato
    ],
},
```

### B.4 Silver — model novo

`airflow/dags/dbt/models/silver/dataspherev2/t001k/t001k.sql`:

```sql
{{
   config(
        tags=['dataspherev2', 'silver'],
        alias='t001k',
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key='hash_pk'
    )
}}

SELECT
    {{ nullif_empty('MANDT') }} AS mandt,
    {{ nullif_empty('BWKEY') }} AS bwkey,
    {{ nullif_empty('BUKRS') }} AS bukrs,

    {{ to_timestamp('dt_ingestao') }} AS dt_ingestao,
    hash_pk,
    source

FROM {{ source('dataspherev2', 't001k') }}
```

Companheiro `.yml` — copiar estrutura de `t001w.yml` (mesmo grupo), ajustando source/campos.

### B.5 Gold — onde expor

Substituir (ou validar contra) `Codigo_Centro→Pais_Centro` de `dim_centro_sap` por um join
`Codigo_Centro (BWKEY) → t001k.bukrs → t001.waers` em qualquer model que precise de moeda por
centro — hoje isso é `fct_estoque_lote_sap.Valor_Financeiro_Estoque` (§6.9) e, se
`PROPOSTA_CONVERSAO_CAMBIO_TCURR.md` for implementada, também os models multi-moeda
(`fct_faturamento_itens_sap`, `fct_vendas_itens_sap`, `fct_pendencia_sap`) — usar `T001K→T001`
como fonte oficial de moeda em vez do texto `Pais_Centro`.

## Parte C — Ingerir `T003T` (texto de tipo de documento contábil)

### C.1 O que é

Confirmado ao vivo: **`T003T`** ("Textos de tipos de documento", **274 linhas**) — grão
`MANDT+SPRAS+BLART`. Resolve parte da regra 1.2.3 de `docs/REGRAS_E_MELHORIAS_DW.md`:
`fct_credito_devolucoes_sap.Tipo_Documento_Contabil` hoje só tem o código (RV/AB/DR/DG/DZ/
LM/DA/EX/SA), sem tradução do **tipo de documento** em si. **Correção (2026-09-05, validação
de docs)**: o texto livre **do lançamento** (diferente da tradução do tipo de documento) não
é exclusividade do schema legado como se pensava antes — `fct_credito_devolucoes_sap` já tem
a coluna `Texto_Motivo`, 93,7% populada, com o mesmo conteúdo de
`vendas.dim_credito_devolucoes.Texto` (ver regra 1.2.3 corrigida em `REGRAS_E_MELHORIAS_DW.md`).
`T003T` continua valendo a pena só pra dar nome oficial ao *código* de `Tipo_Documento_Contabil`
(ex. "RV"→o que `T003T` traduzir), não pro texto do lançamento em si, que já existe.
`scripts/query_vendas_sap.py::devolucoes_credito_motivo` (`invest_sap`) já foi migrada pra usar
`fct_credito_devolucoes_sap.Texto_Motivo` diretamente (2026-09-05) — essa parte não depende
mais desta proposta acontecer no `data-platform`.

Amostra real (`SPRAS='P'`, português):

| `BLART` | `LTEXT` |
|---|---|
| `AB` | Documento contábil |
| `DA` | Documento do cliente |
| `DG` | Crédito de cliente |
| `DR` | Fatura cliente |
| `DV` | Juros de Cliente |
| `DZ` | Pagamento cliente |
| `KA` | Documento fornecedor |
| `KG` | Crédito fornecedor |

### C.2 Campos propostos

Tabela pequena — replicar por completo:

| Campo | Tipo (DDIC) | Descrição oficial |
|---|---|---|
| `MANDT` | CLNT(3) | Mandante |
| `SPRAS` | LANG(1) | Código de idioma (filtrar `SPRAS='P'` no Gold, mesmo padrão de `DDIC_LANGUAGE` do projeto) |
| `BLART` | CHAR(2) | Tipo de documento (chave de join com `Tipo_Documento_Contabil`) |
| `LTEXT` | CHAR(20) | Denominação do tipo de documento |

### C.3 Bronze — novo grupo de ingestão

Tabela mestre estática de FI — mesmo padrão de `T052U` (já ingerida no grupo `fi_daily_h8`,
`CONTEXTO_VENDAS_SAP.md` §5):

```python
"fi_daily_h8": {
    # ... tabelas já existentes: T052U, BSAK, BSIK ...
    "tables": [
        # ... entradas já existentes ...
        create_table("T003T", ["MANDT", "SPRAS", "BLART"], "replace"),  # tabela mestre pequena (274 linhas)
    ],
},
```

### C.4 Silver — model novo

`airflow/dags/dbt/models/silver/dataspherev2/t003t/t003t.sql`:

```sql
{{
   config(
        tags=['dataspherev2', 'silver'],
        alias='t003t',
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key='hash_pk'
    )
}}

SELECT
    {{ nullif_empty('MANDT') }} AS mandt,
    {{ nullif_empty('SPRAS') }} AS spras,
    {{ nullif_empty('BLART') }} AS blart,
    {{ nullif_empty('LTEXT') }} AS ltext,

    {{ to_timestamp('dt_ingestao') }} AS dt_ingestao,
    hash_pk,
    source

FROM {{ source('dataspherev2', 't003t') }}
```

Companheiro `.yml` — copiar estrutura de `t052u.yml` (mesmo grupo), ajustando source/campos.

### C.5 Gold — onde expor

Adicionar `Descricao_Tipo_Documento_Contabil` (join `Tipo_Documento_Contabil = t003t.blart`,
`spras='P'`) em `fct_credito_devolucoes_sap` — dá nome legível ao código sem precisar do
schema `vendas` legado pra esse propósito específico (a regra 1.2.2 sobre excluir `Tp_doc=
'RV'` continua valendo do mesmo jeito, só ganha um texto pra exibir).

## Quem mexe em quê

- **Parte A** (`VBUK`): time de dados, repo `data-platform`
  (`data_pipelines/ingestion/bronze/pipelines/dataspherev3/config.py`, grupo `sd_hourly_h10`
  + `airflow/dags/dbt/models/silver/dataspherev2/vbuk/`) — depois de existir, expor
  `Status_Credito_Documento_SAP` em `fct_pendencia_sap`/`fct_limite_credito_sap`.
- **Parte B** (`T001K`, e `T001` se ainda não estiver replicada): time de dados, grupo
  `master_daily_h9` + `airflow/dags/dbt/models/silver/dataspherev2/t001k/` — depois de
  existir, usar como fonte oficial de moeda por centro (substitui/valida `Pais_Centro`) e
  destrava a Parte B de `PROPOSTA_CONVERSAO_CAMBIO_TCURR.md` com dado real em vez de
  heurística.
- **Parte C** (`T003T`): time de dados, grupo `fi_daily_h8` +
  `airflow/dags/dbt/models/silver/dataspherev2/t003t/` — depois de existir, expor
  `Descricao_Tipo_Documento_Contabil` em `fct_credito_devolucoes_sap`.

Em todos os 3 casos, depois do deploy: atualizar `docs/CONTEXTO_VENDAS_SAP.md` §3 (tabela de
models) e `docs/REGRAS_E_MELHORIAS_DW.md` (marcar os achados correspondentes como
implementados, não mais "achado ao vivo via HANA direto").
