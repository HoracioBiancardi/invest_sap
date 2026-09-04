# Contexto — Vendas SAP (`GOLD.vendas_sap`)

> Documento de referência sobre a arquitetura e os modelos de vendas/pendência SAP no
> `data-platform` — o "o que é isso e como funciona". Para o registro de uma investigação
> específica (achados, bugs, correções aplicadas), veja **`INVESTIGACAO_PENDENCIA_SAP.md`**.
> Para como rodar os scripts deste projeto (conexões, exemplos de uso), veja
> **`COMO_RODAR.md`**.
>
> Construído lendo o repositório `data-platform`
> (`/home/swordpower/Documentos/REPO/TRABALHO/data-platform`) em 2026-08-24. Os caminhos de
> arquivo abaixo são relativos à raiz desse repositório, não deste projeto.

## 1. Visão geral

`vendas_sap` é o schema **GOLD** (banco `GOLD` no SQL Server de produção) que consolida o
ciclo de vida completo de uma venda no SAP ECC: **pedido → remessa → faturamento →
pendência (backlog) → alocação de estoque**, mais dimensões de apoio (cliente, material,
centro, vendedor) e fatos financeiros (crédito, devoluções).

É um pipeline **separado e mais novo** do que o antigo schema `GOLD.vendas` (baseado em
Salesforce/`fat_faturamento`, documentado em `mkdocs/docs/vendas/vendas.md`). Os dois
convivem hoje; `vendas_sap` é a fonte "verdade SAP" granular, `vendas` é a visão
comercial/Salesforce. Não existe (neste checkout) um `mkdocs/docs/vendas_sap/*.md`
equivalente — este documento tenta preencher essa lacuna.

Comentários nos `.yml` do dbt referenciam um `HISTORICO_TECNICO_VENDAS_SAP.md` e uma pasta
`spec_vendas/` (`PROPOSTAS_SQL_dbt.md`, `dim-vendedor-sap.md`, `dim-vendedor-sf.md`,
`fct-pendencia-status-sap.md`) como fonte de verdade de decisões de design e achados de
auditoria. **Esses arquivos não existem neste checkout local** — provavelmente vivem em
outro branch, num Confluence/Notion, ou foram removidos. Vale perguntar ao time de dados
onde estão antes de investigar algo que pareça "decisão de design não óbvia": boa parte já
foi resumida nos comentários dos `.yml` (reproduzidos na seção 6 abaixo).

## 2. Arquitetura (Bronze → Silver → Gold)

Uma única instância SQL Server hospeda três bancos separados (`BRONZE`, `SILVER`, `GOLD`),
mais o SAP HANA/Datasphere como origem direta e MinIO como "landing zone" intermediária.

```
SAP ECC (HANA/Datasphere, schema IB_SAPECC)
        │  hdbcli, extração incremental por range de data
        ▼
MinIO (bucket "landing", CSV)  ──▶  BRONZE.IB_SAPECC.<TABELA> (SQL Server, maiúsculas)
        │  dbt (models/silver/dataspherev2/*.sql — 1 model por tabela)
        ▼
SILVER.dataspherev2.<tabela>  (minúsculas, tipada/normalizada)
        │  dbt (models/gold/vendas_sap/*.sql)
        ▼
GOLD.vendas_sap.<Modelo>  (colunas em PascalCase com underscore, ex.: Codigo_Cliente)
```

- **Ingestão (Bronze):** `data_pipelines/ingestion/bronze/pipelines/dataspherev3/`
  (`connector_sap.py` conecta via `hdbcli`; `config.py` define, por grupo de agendamento,
  quais tabelas SAP são extraídas, chaves primárias e tipo de carga — ver seção 5).
- **Transformação (Silver):** `airflow/dags/dbt/models/silver/dataspherev2/*` — 1 pasta por
  tabela SAP (`vbak/`, `vbap/`, `vbrp/`, `mchb/`, ...), cast de tipos e `TRIM`.
- **Agregação (Gold):** `airflow/dags/dbt/models/gold/vendas_sap/*` — as 13 models
  descritas na seção 3.
- **Orquestração:** Airflow, orientado a `Dataset` (URIs `sap://silver/...`,
  `sap://gold/...`), config central em `airflow/dags/fabric_dags/gold_config.py`.
- **dbt:** `dbt_project.yml` define `gold.vendas_sap.+schema: "vendas_sap"` e
  `gold.+database: GOLD` (var `gold_database`, default `GOLD`). `profiles.yml` usa driver
  `ODBC Driver 18 for SQL Server`.

## 3. Modelos GOLD.vendas_sap

Fonte: `airflow/dags/dbt/models/gold/vendas_sap/*/*.yml` (documentação dbt oficial,
extraída integralmente — é a referência mais confiável de campos e regras que existe hoje).

### 3.1 Dimensões

| Model | Grão | Descrição | Fonte Silver |
|---|---|---|---|
| `dim_centro_sap` | Mandante+Centro+OrgVendas | Centros logísticos/comerciais (fábricas, filiais, CDs) | `t001w`, `tvkwz` (dedup!), `tvkot` |
| `dim_cliente_sap` | Mandante+Cliente+OrgVendas+Canal+Setor | Clientes + geografia (região macro derivada de UF) + condições comerciais | `kna1`, `knvv` |
| `dim_material_sap` | Mandante+Produto | Materiais/produtos, pesos, status de ciclo de vida | `mara`, `makt` |
| `dim_material_centro_sap` | Mandante+Produto+Centro | Material por centro (MRP, comprador, status local) | `marc`, `makt`, `t001w` |
| `dim_vendedor_sap` | Mandante+Vendedor | Vendedor via `VBPA` (`PARVW='VE'`) — **hoje sempre vazio em produção**, ver §6.1 | `vbpa` |
| `dim_vendedor_sf` | Codigo_Vendedor_SF | Vendedor via Salesforce `User` — **fallback real usado hoje**, ver §6.1 | `salesforce.User`, `OpportunityLineItem` |

### 3.2 Fatos — ciclo de vida da venda

| Model | Grão | Descrição | Fonte Silver |
|---|---|---|---|
| `fct_vendas_itens_sap` | Mandante+Pedido+Item | Itens de pedido de venda (ordem original) | `vbap`, `vbak`, `vbpa`, `vbep`, Salesforce `OpportunityLineItem` |
| `fct_vendas_canceladas_sap` | Mandante+Pedido+Item | Itens rejeitados/cancelados (demanda reprimida), com motivo de recusa | `vbap`, `vbak` |
| `fct_remessa_itens_sap` | Mandante+Entrega+Item | Itens efetivamente expedidos (remessa/delivery) | `lips`, `likp`, `vbfa`, `vbup` |
| `fct_faturamento_itens_sap` | Mandante+Fatura+Item | Itens de nota fiscal emitida (receita, CMV, margem) | `vbrp`, `vbrk` |
| **`fct_pendencia_sap`** | Mandante+Pedido+Item | **Fato "combinada" de 2º nível**: junta pedido+remessa+faturamento+estoque para calcular backlog | `fct_vendas_itens_sap`, `fct_remessa_itens_sap`, `fct_faturamento_itens_sap`, `fct_estoque_lote_sap`, `dim_*` |
| `fct_pendencia_status_sap` | Mandante+Pedido+Item | Fato de 3º nível: simulação FIFO de alocação virtual de estoque contra o backlog | `fct_pendencia_sap` |

### 3.3 Fatos — estoque, crédito, financeiro

| Model | Grão | Descrição | Fonte Silver |
|---|---|---|---|
| `fct_estoque_lote_sap` | Mandante+Material+Centro+Depósito+Lote | Posição de estoque por lote (livre/qualidade/bloqueado/trânsito), com custo e valorização; inclui reconciliação MARD vs MCHB | `mchb`, `mch1`, `mara`, `makt`, `mbew`, `mard`, `vbbe` |
| `fct_limite_credito_sap` | Mandante+Cliente+Área de Crédito | Limite de crédito, exposição, saldo a vencer/vencido | `knkk`, `bsid` |
| `fct_credito_devolucoes_sap` | Mandante+Empresa+Cliente+Documento+Item | Notas de crédito/devoluções de clientes (contábil) | `bsid`, `dim_cliente_sap` |

**Fluxo de dependências do backlog** (o mais usado em investigações de pendência):

```
fct_vendas_itens_sap ─┐
fct_remessa_itens_sap ─┼─▶ fct_pendencia_sap ─▶ fct_pendencia_status_sap (FIFO)
fct_faturamento_itens_sap ─┘        ▲
fct_estoque_lote_sap ───────────────┘ (só lê via ref(), sem esperar sua cadência)
```

## 4. Campos-chave de `fct_pendencia_sap` (o mais consultado)

- `Qtd_Pendida`, `Qtd_Remetida`, `Qtd_Faturada` — quantidades acumuladas nas 3 etapas.
- `Qtd_Pendente_Remessa` = `MAX(Pedida - Remetida, 0)`; `Qtd_Pendente_Faturamento` =
  `MAX(Pedida - Faturada, 0)`; `Qtd_Pendente_Operacional` = maior dos dois.
- `Status_Faturamento` ∈ {Nao Faturado, Faturado Parcial, Totalmente Faturado}.
- `Status_Pendencia` ∈ {Concluido, Pendente Logistico e Fiscal, Pendente Logistico
  (Remessa), Pendente Fiscal (Faturamento)}.
- `Status_Pendencia_Estoque` ∈ {Sem Pendencia, Pendente com Estoque, Pendente com Estoque
  Parcial, Pendente sem Estoque} — cruza backlog com `fct_estoque_lote_sap`.
- `Prioridade_Pedido`: 1 se `Tipo_Ordem_Venda='ZVCO'`, senão 9 — **nunca é 2 ou 3** (gap de
  regra de negócio conhecido, ver §6.3).
- `Dias_Desde_Inclusao_Pedido`: aging em BRT explícito (`AT TIME ZONE`), não UTC puro.

## 5. Pipeline de ingestão SAP (grupos de agendamento)

Config central: `data_pipelines/ingestion/bronze/pipelines/dataspherev3/config.py`
(`PIPELINE_ORCHESTRATION_CONFIG`). Schema Bronze/HANA de origem: **`IB_SAPECC`** (mesmo
valor da env var `DDIC_SCHEMA`). Grupos relevantes para vendas:

| Grupo (dataset) | Schedule | Tabelas SAP | Observação |
|---|---|---|---|
| `sd_hourly_h10` | `10,40 * * * *` | VBAK, VBAP, VBUP, VBBE, VBEP | Pedido de venda + status + cronograma. `VBBE` é `replace` total (sem range). |
| `sd_hourly_h15` | `15,45 * * * *` | VBFA, LIKP, LIPS, VBRK, VBRP, VBPA (3x, headers diferentes), J_1BNFDOC/LIN, VTTK/VTTP | Vínculo de docs, remessa, fatura, NF-e, transporte. Range incremental de 14 dias. |
| `master_daily_h9` | `0 9 * * *` | KNA1, KNVV, MARA, MAKT, LFA1, T001W, ... | Mestres (cliente, material, fornecedor, centro) |
| `sd_master_daily_h11` | `0 11 * * *` | TVKOT, TVTWT, TSPAT, T151T, TVKWZ, TVZBT, TVAGT | Textos/domínios de vendas (org. vendas, canal, setor, motivo de recusa) |
| `mm_master_daily_h12` | `0 12 * * *` | T161T, T023T, T134T, T141T | Textos de materiais (tipo, grupo, status) |
| `mm_daily_h7` | `0 7 * * *` | MKPF, MSEG, RBKP, RSEG | Movimentos de mercadoria / entrada de fatura de compra |
| `mm_daily_X` | `15 */4 * * *` | MCHB, MARD, MCH1, MBEW | **Estoque** (a cada 4h, `replace` total) |
| `fi_daily_h8` | `0 8 * * *` | T052U, BSAK, BSIK | Condição de pagamento, títulos a pagar |
| `fi_hourly_h25`/`h45` | hora em hora | BKPF/BSEG, BSAD/BSID | Contábil e títulos a receber (crédito/devolução usa `BSID`) |
| `hr_daily_h10` | `0 10 * * *` | PA0000..., HRP1000/1001, ... | RH (fora do escopo vendas, mas mesma infra) |

Regras de extração incremental usam `range_columns` como `AEDAT`/`ERDAT` (alteração/criação)
com janela `LAST_14_DAYS`/`LAST_30_DAYS`/`LAST_60_DAYS` até `TODAY`. Tabelas de log
append-only (ex. `VBFA`, `BKPF`/`BSEG`) usam só a data de criação — comentário no código
explica que SAP raramente faz UPDATE nesses casos (correção = estorno + novo doc).

## 6. Achados de auditoria e gotchas conhecidos

Extraído literalmente dos comentários em `.yml`/`.sql` — **leia antes de confiar num
número**, especialmente em análises de vendedor/comissão ou de priorização de backlog.
(Para o achado de `KWMENG=0`/pendência escondida, o mais recente e o que motivou a
correção aplicada, veja `INVESTIGACAO_PENDENCIA_SAP.md`.)

### 6.1 `Codigo_Vendedor` quase sempre vem do Salesforce, não do SAP
Achado de auditoria (2026-08-13): `VBPA.PARVW='VE'` **nunca ocorre** nesta base (0 de ~3,5M
linhas de VBPA). `dim_vendedor_sap` está sempre NULL/vazia em produção. O fallback real
usado em `fct_vendas_itens_sap`, `fct_remessa_itens_sap`, `fct_faturamento_itens_sap` e
`fct_vendas_canceladas_sap` é:
```
COALESCE(VBPA.KUNNR onde PARVW='VE', Salesforce.OpportunityLineItem.Vendedor__c)
```
cobertura medida via Salesforce: **72,97%**. Cada fato expõe `Origem_Vendedor` (`SAP_VBPA`
ou `SALESFORCE`, pode ser NULL) para saber contra qual dimensão de vendedor dar join —
`Codigo_Vendedor` SAP (KUNNR) e Salesforce (Id) são domínios de chave **incompatíveis**,
nunca comparar diretamente.

### 6.2 `dim_centro_sap`: fanout corrigido em 2026-08-20
`TVKWZ` tem grão `MANDT+WERKS+VKORG+VTWEG` (um centro atende vários canais). Sem dedup, o
join por `WERKS` multiplicava cada centro por N canais (achado real: um centro com 9 canais
gerava 9 linhas idênticas, propagando fanout para `fct_pendencia_sap` via join
`Mandante+Codigo_Centro+Org_Vendas_Responsavel`). Corrigido deduplicando `TVKWZ` por
`MANDT+WERKS+VKORG` antes do join (CTE `tvkwz_dedup` em `dim_centro_sap.sql`).

### 6.3 `Prioridade_Pedido` nunca é 2 ou 3 — "CARIMBAGEM" nunca ocorre
`Prioridade_Pedido` só assume 1 (`Tipo_Ordem_Venda='ZVCO'`) ou 9 (demais) — a regra de
negócio original previa uma escala mais fina (VKORG específico, AUART em listas), mas a
implementação atual colapsou tudo fora de `ZVCO` em "9". Consequência direta em
`fct_pendencia_status_sap`: `Status_Alocacao_Virtual='CARIMBAGEM'` depende de
`Prioridade_Pedido IN (2,3)`, que **nunca acontece hoje** — é um status morto no código.
Gap de regra de negócio conhecido, não um bug de SQL.

### 6.4 Estoque: `MCH1`/`MCHB` com data `'00000000'`
`Data_Producao`/`Data_Validade` em `fct_estoque_lote_sap` usam `TRY_CAST` (não `CAST`)
porque o SAP grava literalmente `'00000000'` quando a data não se aplica ao lote — um
`CAST` normal quebraria a query inteira nesses casos.

### 6.5 `Delta_Estoque_Deposito_Vs_Lotes`
Campo de reconciliação: `Estoque_Total_Deposito` (via `MARD`, nível depósito) menos
`Estoque_Total_Lotes_Deposito` (via `MCHB`, soma por lote). Diferente de zero indica
inconsistência de estoque entre as duas visões SAP — útil como sanity check antes de
confiar em `fct_estoque_lote_sap` para uma investigação de ruptura.

### 6.6 Timezone: BRT explícito desde 2026-08-13
Campos de "processado em" e cálculos de aging (`Dias_Desde_Inclusao_Pedido`,
`Dias_Aging_Credito`, `Data_Processamento_DW`) passaram a usar
`SYSUTCDATETIME() AT TIME ZONE 'UTC' AT TIME ZONE 'E. South America Standard Time'`
(T-SQL) em vez de horário de servidor implícito — se um número de aging parecer "off by
3h", confirme se o model já foi migrado para esse padrão.

### 6.7 Chain consolidada `sap_pendencia_chain`
`fct_vendas_itens_sap`, `fct_remessa_itens_sap` e `fct_faturamento_itens_sap` **não têm
`ref()` entre si** (só leem Silver + `dim_cliente_sap`) — rodam em paralelo na mesma
`DbtTaskGroup` via Cosmos, e só `fct_pendencia_sap` espera os três. Antes eram 4 DAGs
encadeadas por `Dataset`, e cada salto custava minutos de scheduler do Airflow — motivo da
consolidação (`airflow/dags/fabric_dags/gold_config.py`, comentário linha ~142).
`fct_estoque_lote_sap` fica fora de propósito da chain (cadência mais lenta, só `ref()`).

### 6.8 Campos adicionados em 2026-08-19 (mudança recente)
Em `fct_faturamento_itens_sap`: `Data_Hora_Criacao_Fatura` (distinto de `Data_Faturamento`,
que é a data fiscal — pode divergir de quando o documento foi criado de fato),
`Status_Transferencia_Contabil`, `Documento_Contabil`, `Exercicio_Documento_Contabil`,
`Data_Transporte` (via `LIKP.WADAT_IST` da remessa mais recente — **não** é o documento de
transporte/romaneio `VTTK`, que segue bloqueado até terminar carga histórica), `Regional`.
Se uma análise histórica parecer não bater para trás dessa data, é esperado.

### 6.9 `fct_estoque_lote_sap.Valor_Financeiro_Estoque` — 2 achados reais (2026-08-25)

Investigando por que o valor de estoque parecia grande demais mesmo depois de corrigido:

1. **Custo unitário sem dividir por `PEINH`** — `MBEW.VERPR`/`STPRS` é o preço para
   `MBEW.PEINH` unidades, não por unidade (convenção SAP padrão). O model original não
   dividia por `PEINH`, inflando `Valor_Custo_Unitario` (e por consequência
   `Valor_Financeiro_Estoque`) pelo fator de `PEINH` sempre que ≠ 1 — comum em item
   barato/granel, precificado "por 1000"/"por 10000". Achado real: material
   `000000000009600491` (Acetato de Abiraterona) tinha `PEINH=10000`, dando custo unitário
   de R$428.037,74 em vez do real R$42,80. **Corrigido** em
   `fct_estoque_lote_sap.sql` (commit `ad60d108`, branch `feature/restruct-sap-vendas`) —
   verificar se já rodou em produção antes de confiar no valor.
2. **Moeda não convertida pra centros fora do Brasil** — `Codigo_Centro` em
   `fct_estoque_lote_sap` é o mesmo código de `MBEW.BWKEY` (valuation area = plant nesta
   configuração SAP). A maioria dos centros é Brasil/BRL, mas Montevidéu/Canelones (Uruguai,
   centros 2000/2100/2400/2500/2600/2700) valoram em **UYU** e o centro `CO10` (Colômbia) em
   **COP** (confirmado via `SILVER.dataspherev2.t001`, tabela de empresa/moeda — `T001K`,
   que faria o de-para formal `BWKEY→BUKRS`, não está replicada nesta base, então o mapeamento
   usado é `Codigo_Centro→Pais_Centro` de `dim_centro_sap`). Sem tabela de câmbio (`TCURR`)
   disponível pra converter de verdade, `scripts/query_vendas_sap.py` (`invest_sap`) hoje
   **filtra** (parâmetro `pais_centro` em `estoque_restrito_disponivel`/`estoque_validade*`)
   — soma valor em R$ só de centro `Pais_Centro='BR'` por padrão nos totais/KPIs, e expõe
   uma coluna `Moeda` (`MOEDA_POR_PAIS_CENTRO`) nas consultas de detalhe.

   **Extração de `TCURR` adicionada em 2026-08-25** (commit `d364cacf`, branch
   `feature/restruct-sap-vendas`, grupo Bronze `fi_daily_h8` + Silver
   `dataspherev2.tcurr`) — mas a conversão **não está ligada no Gold ainda**: `TCURR` nunca
   teve linha extraída nesta base antes dessa mudança, então não dava pra validar a fórmula
   (direção/sinal de `UKURS`, fatores `FFACT`/`TFACT`) contra dado real. Próximo passo, só
   depois que essa extração rodar em produção pelo menos uma vez: conferir os valores reais
   de `KURST`/`UKURS` pra BRL↔UYU/COP contra uma taxa de referência conhecida, aí sim ligar a
   conversão em `fct_estoque_lote_sap` (ou onde fizer mais sentido). `T001K` (de-para formal
   `BWKEY→BUKRS`) continua não replicada — o mapeamento usado pra saber o país/moeda de cada
   centro continua sendo `Codigo_Centro→Pais_Centro` de `dim_centro_sap`.

Ambos achados são específicos de `fct_estoque_lote_sap` — não afetam `Qtd_*`/datas, só os
campos de R$/custo unitário.

### 6.10 SQL Server: plano de execução cacheado dá **resultado errado** (não só lento) pra query parametrizada com CTE + predicado calculado

Achado grave (2026-08-25), construindo `scripts/query_faturamento_comercial.py`: a mesma
consulta parametrizada (`WITH` de crosswalk cliente→setor, filtro
`WHERE (CASE/COALESCE...) = :param` sobre uma coluna calculada, agregação por outra dimensão)
dava **dois resultados diferentes e ambos deterministicamente reprodutíveis** dependendo só
de espaço em branco *fora* de qualquer cláusula (uma linha em branco a mais no fim do texto
da query) — confirmado isolando em `read_sql()` direto, sem nenhuma lógica Python no meio.
`SELECT` agregando por "Setor" com filtro `Divisional = 'Emerson Alves'` somava
R$ 31.872.135,42 (23 grupos) numa versão do texto e R$ 37.640.872,15 (22 grupos, valores
errados por grupo) na outra — a diferença bateu exatamente com o efeito de reusar um plano de
execução compilado pra uma combinação diferente de parâmetros (parameter sniffing), só que
aqui **mudando o resultado, não só a performance** — comportamento fora do esperado até pra
esse tipo de bug (normalmente parameter sniffing só piora plano/tempo, não muda a soma).

**Confirmado com `OPTION (RECOMPILE)`**: adicionar essa hint no fim da query (força o SQL
Server a recompilar o plano do zero a cada execução, ignorando o cache) fez as duas versões
do texto darem o resultado certo (R$ 31.872.135,42) de forma consistente, repetido em
múltiplas execuções. `scripts/query_faturamento_comercial.py` usa `OPTION (RECOMPILE)` em
toda consulta que combina a CTE de crosswalk com um predicado parametrizado sobre expressão
calculada — não em consultas sem parâmetro (`valores_dimensao`, que não tem `WHERE`
parametrizado, não precisa e ficaria mais lenta à toa).

**Contrapartida de performance descoberta ao aplicar a correção**: em pelo menos 1 formato de
consulta (`TOP N ... ORDER BY ... ` com várias tabelas juntadas, usado em
`relatorio_analitico`), `OPTION (RECOMPILE)` teve o efeito oposto — o otimizador, forçado a
recompilar do zero toda vez, escolheu um plano ruim e levou de ~3s pra 30-50s pro mesmo
resultado. Não achamos uma regra simples pra prever qual dos dois lados (`RECOMPILE` mais
rápido vs. mais lento) uma query nova vai cair — **testar os dois** (com e sem a hint,
medindo tempo E conferindo o resultado contra uma soma calculada de outro jeito) antes de
decidir, em vez de assumir.

**Implicação pro resto do projeto**: `scripts/query_vendas_sap.py` tem várias funções com o
mesmo formato de risco (CTE + filtro parametrizado sobre expressão calculada) —
`faturamento_por_org_vendas_linha_negocio`, `meta_vs_realizado_mensal`,
`correlacao_oportunidade_pedido_pendencia_fatura`, e os helpers `_filtro_periodo_tipo_cliente`/
`_condicao_tipo_cliente_por_codigo` reusados por boa parte do módulo. **Nenhuma delas foi
auditada por esse bug ainda** — não foi escopo desta investigação, só a descoberta em cima do
módulo novo. Vale considerar uma auditoria dedicada (comparar o resultado de cada uma contra
uma soma calculada de outro jeito, tipo fiz aqui) antes de confiar cegamente num número dessas
funções específicas, principalmente as que já são usadas em decisão (Meta x Realizado,
Linha de Negócio).

## 7. Como conectar (produção) — conceitos

Duas origens de dados, credenciais no `.env` (nunca commitar valores, nunca colar em
chat/PR). Detalhes de setup e como usar via os scripts deste projeto estão em
`COMO_RODAR.md`.

- **SQL Server (BRONZE/SILVER/GOLD)** — mesma instância, banco muda no `DATABASE=` da
  connection string. Driver: `ODBC Driver 18 for SQL Server`. Tabelas de vendas:
  `GOLD.vendas_sap.<Model>`.
- **SAP HANA/Datasphere** — acesso direto via `hdbcli`, útil para consultar o **DDIC**
  (dicionário de dados: `DD02T` descrição de tabela, `DD03L`/`DD04T` descrição de campo) ou
  amostrar uma tabela SAP crua antes dela existir na Silver. Schema padrão: `IB_SAPECC`
  (= env var `DDIC_SCHEMA`); idioma: `P` (português, = `DDIC_LANGUAGE`).

## 8. Rastreamento Opportunity (Salesforce) → Pedido (SAP)

O fluxo comercial começa no Salesforce: a **Opportunity** (oportunidade) vira um ou mais
**OpportunityLineItem** (um por produto) e, quando aprovada, é transmitida ao SAP, que cria
o pedido de venda (`VBAK`/`VBAP`). O elo de volta dessa integração é gravado nas próprias
tabelas do Salesforce — ambas disponíveis em `SILVER.salesforce.*` no SQL Server:

- **`SILVER.salesforce.OpportunityLineItem`** — grão item. Campo `Ordem_de_Venda_Sap__c`
  = `VBELN` do pedido, `ItemNumero__c` = `POSNR` do item. Também traz `Quantity`,
  `Qtde_Ordem__c`, `Qtde_Pendente__c`, `Pendencia__c`, `Status_Faturamento__c` e
  `Vendedor__c` — **calculados independentemente do pipeline SAP**, direto no Salesforce.
- **`SILVER.salesforce.Opportunity`** — grão cabeçalho. Campos de negócio (`name`,
  `stage_name`, `amount`, `is_won`) e o **retorno da integração com o SAP**:
  `numero_pedido`/`retorno_numero_pedido` (VBELN retornado), `retorno_motivo_status`
  (mensagem textual, ex.: "O pedido foi criado com sucesso!"), `situacao_pedido_del`
  (HTML com indicador visual de status), `status_faturamento`, `pendencia_pedido`.

Atenção: colunas de `OpportunityLineItem` estão em `CamelCase`/`Nome_Campo__c` (herdado do
Salesforce), enquanto `Opportunity` está em `snake_case` — schemas com convenções diferentes
na mesma origem, não é engano.

Um caso real de uso disso (rastreando o pedido 137490 pelas 3 camadas — SAP cru, Gold,
Salesforce — e usando o Salesforce pra cross-validar um bug) está em
`INVESTIGACAO_PENDENCIA_SAP.md`. O script `scripts/trace_pedido.py` (ver `COMO_RODAR.md`)
automatiza esse rastreamento pra qualquer pedido.

## 8.1 Schema `GOLD.vendas` (legado/comercial) — Linha de Negócio e motivo de crédito/devolução

Descoberto investigando o pedido do painel de correlação (2026-08-25): o schema `vendas`
(mais antigo, baseado em Salesforce/`fat_faturamento`, ver §1) tem tabelas que `vendas_sap`
não tem equivalente, úteis pra duas coisas específicas:

- **Linha de Negócio comercial** (AESTHETICS, AESTHETICS BLAU/BRG, FARMA, ONCO / HEMATO,
  NÃO ALOCADO) — não existe como campo em `vendas_sap`. Vem do crosswalk:
  `Codigo_Cliente` → `vendas.dim_cliente_setor` (pega o `periodo` mais recente por
  cliente — é um painel mensal, `cod_cliente` varchar sem zero-padding, `cod_setor`
  bigint) → `vendas.dim_estrutura.org_vendas` (join por `cod_setor`). Cobertura medida
  ~52% dos clientes do backlog aberto em 2026-08-25 — cliente sem match cai em
  "NÃO ALOCADO" (categoria que já existe nativamente na tabela).
  **Importante**: essa Linha de Negócio é **independente** da Organização de Vendas SAP
  (`Codigo_Org_Vendas`/VKORG) — não é uma hierarquia 1:1. Confirmado nos dados: Org Vendas
  1000 (BLAU HOSPITALAR) aparece faturando pra clientes de ONCO/HEMATO, AESTHETICS, FARMA
  e NÃO ALOCADO ao mesmo tempo. Ver `scripts/query_vendas_sap.py::faturamento_por_org_vendas_linha_negocio`.
- **Motivo de crédito/devolução em texto livre** — `vendas.dim_credito_devolucoes.Texto`
  (~93% de cobertura) tem o texto que o financeiro registrou no lançamento (ex.: "SALDO NF
  000064142-2", "ACORDO CONFISSÃO DE DIVIDA - 24 PARCELAS", "DESCONTOS CONC..."). A tabela
  equivalente em `vendas_sap` (`fct_credito_devolucoes_sap`) **não tem** esse campo — só
  código de tipo de documento (`Tipo_Documento_Contabil`: RV/AB/DR/DG/DZ/LM/DA/EX/SA) e
  conta contábil, sem texto. `Tp_doc = 'RV'` é o mais comum (>95% das linhas) e é só
  transferência de documento de faturamento de rotina (texto sempre "Transf.docs.faturam.
  ..."), não é devolução/abatimento de negócio de fato — vale excluir por padrão.
  Os códigos de `Tp_doc` não têm tradução pra texto disponível nesta base (a tabela SAP
  `T003T`, de descrição de tipo de documento, existe no DDIC mas não está replicada como
  dado no HANA/`IB_SAPECC` — só a definição de estrutura, sem linhas). Ver
  `scripts/query_vendas_sap.py::devolucoes_credito_motivo`.

## 8.2 Investigação: existe um `dim_estrutura` nativo (SAP ou Salesforce) pra substituir o crosswalk manual da Linha de Negócio?

Motivada pelo incômodo (justo) de que a Linha de Negócio comercial (§8.1) depende de uma
planilha com só ~52% de cobertura. Testado em produção (2026-08-25), 3 hipóteses:

1. **Campos de cadastro de cliente no SAP** (`KNA1.BRSCH`, `CNAE`, `KATR1-10`,
   `KNVV.KVGR1-5`, `KDGRP`, `VKBUR`, `VKGRP`) — cruzados contra a Linha de Negócio conhecida
   via `vendas.dim_cliente_setor`. **Nenhum discrimina**: ex. `BRSCH='0009'` é o valor mais
   frequente simultaneamente em AESTHETICS (348 linhas), FARMA (276) e ONCO/HEMATO (522) —
   é ruído de cadastro, não segmentação de negócio. `CNAE`/`KATR*`/`KVGR*`/`VKBUR`/`VKGRP`
   nem estão preenchidos nesta configuração SAP (100% NULL).
2. **Cadastro do vendedor no Salesforce** (`salesforce.User` → `dim_vendedor_sf`) — tem
   campos reais `Unidade_Negocio` (ex. `'2000 BLAU FARMA'`), `Divisao` (ex. `'Onco'`,
   `'Aesthetics'`), `Setor` (código numérico no mesmo formato de `cod_setor`). Mas só
   ~15-25% dos 327 vendedores têm esses campos preenchidos — não dá pra usar como fonte
   primária hoje.
3. **`vendas.dim_estrutura` é um organograma por nome, não um cadastro de sistema** — as
   colunas `divisional`/`regional`/`distrital` guardam **nomes de gerentes**, não só
   códigos. Testado casar `dim_vendedor_sf.Nome_Gerente_SF` contra esses nomes (hipótese:
   vendedor → gerente [Salesforce, sempre atual] → Linha de Negócio [`dim_estrutura`],
   evitando o crosswalk por cliente): deu **11,6%** de cobertura de vendedores — pior que os
   52% atuais por cliente, e enviesado pra ONCO/HEMATO, o único braço do organograma
   preenchido de ponta a ponta em `dim_estrutura` (AESTHETICS/FARMA têm nós como
   `"Setor Vago"`, `"MS"`, `"Publico"` — organograma raso).

**Conclusão**: o teto não é a chave de join usada (cliente vs. vendedor/gerente) — é que
`dim_estrutura` em si é **incompleta e desigual entre linhas de negócio**. Nem SAP nem
Salesforce guardam essa segmentação comercial como dado estruturado de sistema hoje; ela só
existe, parcialmente, dentro da própria tabela auxiliar SharePoint (`dCLIENTE_SETOR`/
`dESTRUTURA`, ver §1 do histórico de ingestão em
`data_pipelines/ingestion/bronze/pipelines/sharepoint/config.py`). Trocar o caminho técnico
do join não resolve — qualquer caminho herda a mesma lacuna de preenchimento.

**Heurística por produto — testada e implementada (2026-08-25)**: como o universo de
materiais (`vendas.dim_produto`, campo `unidade_de_negocio` — ainda SharePoint, mas
pequeno e estável, ~316 materiais mapeados) é muito menor que o de clientes, testei inferir
a Linha de Negócio de um cliente pela categoria de produto dominante (maior
`Valor_Liquido_Faturamento` histórico) entre o que ele comprou, via
`fct_faturamento_itens_sap.Codigo_Produto → dim_produto.material`. Validado contra os
clientes com rótulo manual conhecido:

| Categoria (heurística) | Precisão | Observação |
|---|---|---|
| BLAU AESTHETICS → AESTHETICS | ~96% (726/753) | Confiável |
| FARMA → FARMA | ~100% (58/58) | Confiável, mas raro (baixo *recall* — poucos clientes têm produto FARMA como dominante) |
| ESPECIALIDADES-ONCO HEMATO → ONCO/HEMATO | ~64% (616/967) | **Não confiável isoladamente** — ~30% dos casos são clientes de fato FARMA que compram muito produto de especialidade/onco por valor |

Cobertura de clientes faturados sobe de ~52% (só manual) pra **~87%** com o fallback.
Implementado em `scripts/query_vendas_sap.py::faturamento_por_org_vendas_linha_negocio`
como camada de fallback (só preenche quando não há match manual), expondo
`Origem_Linha_Negocio` (`MANUAL`/`HEURISTICA_PRODUTO`/`NAO_ALOCADO`) pra quem consumir o
dado poder filtrar só o confirmado quando precisar de precisão em vez de cobertura —
exibido também em `pages/8_Faturamento_Org_Vendas.py`.

## 8.3 `GOLD.vendas.fat_meta_equipe` — meta comercial (planejamento, não transação)

Também SharePoint (mesma família de `dCLIENTE_SETOR`/`dESTRUTURA`, ver §8.1/§8.2), grão
mês (`data_meta`, sempre dia 1) x `cod_setor` x `material`, campos `meta` (R$) e `unidades`.
`bu` aqui tem nomenclatura própria (`ONCO-HEMATO`, `FARMA`, `BLAU AESTHETICS`, `MS`,
`Botulift`) — **não** é 1:1 com a Linha de Negócio de §8.1 (grafias diferentes, e
`Botulift` é uma BU própria sem equivalente lá). Cobertura medida (2026-08-25): 80 setores,
224 materiais, mês de 2025-01 até 2026-08.

Diferente da Linha de Negócio, aqui **não existe heurística possível** — meta é uma decisão
de orçamento/planejamento, não um fato observável em nenhuma transação SAP/Salesforce. Não
tem "sistema de origem" pra integrar; a única forma de melhorar isso é o processo de
captura em si (ferramenta de planejamento formal com dono/SLA), não uma solução técnica de
dado.

Consumida em `scripts/query_vendas_sap.py::meta_vs_realizado_mensal` (Meta x Realizado por
mês x BU, `pages/11_Metas.py`) — o Realizado é atribuído ao `cod_setor` da meta via o mesmo
crosswalk `vendas.dim_cliente_setor` de §8.1/§8.2, então herda a cobertura ~52%: faturamento
de cliente sem setor mapeado não desaparece, cai em BU `'NAO ALOCADO'` à parte do
atingimento das BUs com meta.

## 9. Perguntas em aberto para uma próxima investigação

- Onde estão `HISTORICO_TECNICO_VENDAS_SAP.md` e a pasta `spec_vendas/` referenciados nos
  comentários dbt? (seção 1) — provavelmente têm mais decisões de design não capturadas
  aqui.
- `GOLD.vendas` (Salesforce) vs `GOLD.vendas_sap` (SAP): existe hoje algum model que
  concilie os dois (ex.: comparar faturamento Salesforce vs faturamento SAP para o mesmo
  pedido)? Não encontrado neste checkout — pode ser uma lacuna real ou só não ter sido
  necessário ainda. **Parcialmente coberto** por
  `scripts/query_vendas_sap.py::correlacao_oportunidade_pedido_pendencia_fatura` (página
  Streamlit `pages/5_Jornada_Pedido.py`) — mas é uma junção em pandas ad hoc, não um model dbt;
  se o uso crescer, vale considerar promovê-la a model Gold de verdade.
- Cobertura de vendedor via Salesforce é 72,97% (medida em 2026-08-13) — vale checar se
  subiu/caiu, e investigar os ~27% sem vendedor identificado (pedidos antigos? canais sem
  integração Salesforce?).
- `Status_Alocacao_Virtual='CARIMBAGEM'` nunca ocorre (§6.3) — perguntar ao time de negócio
  se a intenção original (prioridades 2/3) ainda é necessária ou se o status pode ser
  removido do código.

## 10. `scripts/query_faturamento_comercial.py` — Faturamento por dimensão comercial (Canal/Divisional/Regional/Distrital/Setor)

Construído em 2026-08-25 a partir de um PDF do Power BI "Painel Vendas" (Blaú) enviado pelo
usuário, pra montar páginas equivalentes no `invest_sap`. Passou por 2 versões — a segunda é
a que está em produção hoje; a primeira fica registrada abaixo porque a decisão de trocar (e
o porquê) importa pra quem mexer nisso depois.

### 10.0 Histórico: por que a 1ª versão (`vendas.fat_faturamento`) foi descartada

Os totais do PDF (ex.: Faturamento 2026 YTD = R$ 1.044.368.922) não batem com
`SUM(Valor_Liquido_Faturamento)` de `vendas_sap.fct_faturamento_itens_sap` sem filtro (dá
R$ 2,43 bi no mesmo período — mistura filial estrangeira em moeda local + documento
intercompany, mesma classe de problema do §6.9 pro estoque). Investigando, a fonte que batia
com precisão (~2%, explicado por defasagem de horário) era `GOLD.vendas.fat_faturamento`
(schema `vendas`, legado/Salesforce) — tem `cod_setor` nativo por linha, sem precisar de
crosswalk, e `familia_do_produto`/`uf_nome` prontos. Uma primeira versão deste módulo foi
implementada em cima dela.

**Essa versão foi descartada por decisão do usuário** (2026-08-25, na mesma conversa): o
schema `vendas` (legado) só deve seguir em uso pra `fat_meta_equipe` (Meta — não tem
alternativa, ver §8.3) e pras tabelas de crosswalk/dimensão já estabelecidas noutras páginas
(`dim_cliente_setor`, `dim_estrutura`, `dim_produto`, ver §8.1) — não mais como fonte de
**medida** de faturamento. Motivo: `fat_faturamento` competia diretamente com
`vendas_sap.fct_faturamento_itens_sap` como "o" número de faturamento do app, e as duas
tabelas não reconciliam entre si — ter 2 fontes de faturamento independentes no mesmo app é
pior do que ter 1 só que não bate com um painel externo. **Consequência aceita**: as páginas
de Faturamento (Painel Vendas) **não batem mais** com os números exatos do PDF de
referência — servem pra estrutura/navegação equivalente, não reconciliação numérica.

### 10.1 Versão atual: `vendas_sap.fct_faturamento_itens_sap` + crosswalk

A medida é `SUM(Valor_Liquido_Faturamento)` de `fct_faturamento_itens_sap`, sem filtro de Org
Vendas/moeda/tipo de documento — a mesma medida que `scripts/query_vendas_sap.py::
faturamento_mensal`/`faturamento_por_org_vendas_linha_negocio` já usam, de propósito (só uma
noção de "Faturamento Total" circulando no app). A quebra por Canal/Linha de Negócio/
Divisional/Regional/Distrital/Setor vem do **mesmo crosswalk** dessas duas funções:
`Codigo_Cliente` → `vendas.dim_cliente_setor` (`periodo` mais recente por cliente) →
`vendas.dim_estrutura` (por `cod_setor`) — cobertura ~52% dos clientes faturados (§8.1/§8.2);
cliente sem match cai em `'NAO ALOCADO'`.

**Canal Venda (Privado/MS/Publico/NAO ALOCADO)** reaproveita a mesma regra descoberta na
investigação do PDF (ainda válida estruturalmente, só que agora atrás do crosswalk):

- **MS**: cliente cujo `Nome_Cliente` (via `vendas_sap.dim_cliente_sap`) contém "MINISTERIO
  DA SAUDE" (`LIKE`, não igualdade — esse cliente está pulverizado em várias `Codigo_Cliente`
  por unidade hospitalar, ex. "MINISTERIO DA SAUDE HOSP. GERAL...", "...-HOSPITAL DA LAG").
- **Publico**: `cod_setor` (via crosswalk) é um dos nós "`<Org Vendas comercial> -
  Publico`" de `vendas.dim_estrutura` (`601000000`/`602000000`/`603000000`) — excluindo quem
  já caiu em MS.
- **Privado**: cliente com `cod_setor` mapeado, resto.
- **NAO ALOCADO**: cliente sem `cod_setor` no crosswalk (~48%) — balde grande e esperado, não
  bug. Diferente da 1ª versão (onde `cod_setor` vinha nativo por linha, cobertura maior), aqui
  esse balde é proporcional ao teto do crosswalk documentado em §8.1/§8.2.

Mesma REPLACE "`- Publico`" → "`- MS`" aplicada em Divisional/Regional/Distrital/Setor quando
o cliente é Ministério da Saúde (`_expr_dimensao_hierarquia` em
`scripts/query_faturamento_comercial.py`) — nó "`- MS`" de `dim_estrutura` continua sem
faturamento real associado (achado original da investigação, ainda válido).

### 10.2 Diferenças a saber

- **Não compare com um Painel Vendas externo** — os números aqui não batem mais 1:1 com o
  PDF de referência (ver §10.0). Servem pra navegação/estrutura equivalente e pra métrica
  internamente consistente com o resto do app, não pra reconciliar com aquele painel.
- **Meta** (`vendas.fat_meta_equipe`) não tem o ajuste de MS: seu `cod_setor` de "Publico"
  não separa o cliente Ministério da Saúde do resto — Meta de Canal='MS' aparece sempre
  0/NULL nas páginas de Meta x Realizado. Não é ausência de dado, é que a meta orçamentária
  nunca segmentou esse cliente à parte.
- **"Produto"** (`valores_dimensao`) é restrito a materiais com pelo menos 1 fatura no
  histórico (~1.700), não o cadastro completo de `dim_material_sap` (~80 mil, na maioria
  matéria-prima/embalagem nunca faturada a cliente — inviável num filtro).
- **"Nome Produto"** no Relatório Analítico (`pages/17_Relatorio_Analitico.py`) é resolvido
  em 2 passos (TOP N primeiro, `dim_material_sap` só nas linhas já selecionadas depois) —
  ver §6.10 pro motivo (juntar essa tabela na mesma consulta que já tem a CTE de crosswalk +
  `TOP N ORDER BY` faz o otimizador escolher um plano catastrófico, >100s). Por isso não vem
  marcado por padrão no seletor de colunas.
- **`OPTION (RECOMPILE)`** aparece na maioria das consultas deste módulo — não é estilo, é
  correção de um bug real de plano cacheado do SQL Server (ver §6.10). Não remover sem
  reconferir o resultado contra uma soma calculada de outro jeito.

## 11. Estoque histórico: `MCHBH`/`MBEWH` (real, via HANA) e proposta de `fct_movimento_lote_sap`

Investigação ao vivo em 2026-09-03 (dashboard `invest_sap`, página **Pendência x Estoque**
→ aba **Rastreio de Lote**/drill-down "estoque na data do pedido"), motivada por um usuário
questionando por que um pedido aparecia "sem estoque" quando ele lembrava do material ter
estoque na época. Duas descobertas ficaram maduras o bastante pra virar candidatas a modelo
GOLD — registradas aqui pra quando alguém for propor isso no `data-platform`.

### 11.1 `IB_SAPECC.MCHBH`/`MBEWH` já existem no HANA, com dado real — não estão na ingestão

`MCHBH` ("Estoques de lotes - histórico") e `MBEWH` ("Avaliação de material - histórico")
são views no schema `IB_SAPECC` do HANA/Datasphere, com dado real (798.364 e 1.816.404
linhas respectivamente, contadas ao vivo) — **mas não fazem parte de nenhum grupo de
ingestão** da tabela da seção 5 (`sd_hourly_h10`, `mm_daily_X`, etc.). Ou seja: o dado
existe e é acessível hoje via `read_hana_sql()` direto (mesma conexão de
`scripts/ddic_lookup.py`), mas nunca chega em `BRONZE`/`SILVER`/`GOLD`.

**O que são**: snapshot de FECHAMENTO DE PERÍODO (`LFGJA`+`LFMON`, ano+mês), calculado pelo
próprio SAP — não uma reconstrução externa. `MCHBH` tem grão Mandante+Material+Centro+
Depósito+Lote+Período, com as mesmas colunas de quantidade de `MCHB` (`CLABS`=livre,
`CINSM`=qualidade, `CSPEM`=bloqueado, `CUMLM`=em trânsito, `CEINM`=uso restrito,
`CRETM`=devolução bloqueada). `MBEWH` é o equivalente pra valorização/custo (`MBEW`
histórico).

**Por que importa pra mais de 1 visão**:
- **Estoque numa data passada** (o caso que motivou a descoberta): consulta real, granularidade
  de mês fechado, ~5-10s por Material+Centro. Já implementado em
  `scripts/trace_lote.py::estoque_historico_material_centro()` — substituiu uma estimativa via
  soma de `MSEG`/`MKPF` que dava número errado (calibração quebrava quando o material já tinha
  estoque antes do início da réplica de `MSEG`, ~2024).
- **Auditoria/reconciliação**: comparar `fct_estoque_lote_sap` (foto de hoje) contra o
  fechamento do mês anterior em `MCHBH` pra ver se a variação bate com o que `MSEG` registrou
  no meio do caminho — detectaria lacuna/erro de replicação sem precisar confiar cegamente
  numa única fonte.
- **Valorização histórica** (`MBEWH`, ainda não explorado): margem/custo num pedido antigo
  específico, sem depender do custo *atual* de `fct_estoque_lote_sap.Valor_Custo_Unitario`
  (que já teve achado de bug de `PEINH`, ver §6.9) — útil pra reconstruir margem "como era
  na época" em vez de com o custo de hoje.
- **Séries temporais de estoque** (mês a mês) pra material/família, sem precisar reconstruir
  nada — é só agrupar por período.

**Se o uso crescer**: candidato a ingestão formal (`data-platform`) — deixaria essas
consultas em GOLD normal em vez de HANA ao vivo (mais rápido, sem depender de VPN). Spec
completa (config de Bronze, SQL dos 2 models Silver, schema real de `MCHBH`/`MBEWH`
verificado ao vivo) em **`docs/PROPOSTA_INGESTAO_MOVIMENTO_ESTOQUE.md`** (Parte A).

### 11.2 Proposta (não construída): `fct_movimento_lote_sap` — histórico de movimento por lote

Nenhuma tabela GOLD guarda hoje a timeline de eventos de um lote (produção → transferência →
qualidade → liberação → venda) — só o estado atual (`fct_estoque_lote_sap`, sem histórico) ou
o fechamento mensal agregado (`MCHBH`, sem o evento individual). O rastreio de lote do
dashboard (`scripts/trace_lote.py::trace_lote()`) hoje resolve isso lendo
`SILVER.dataspherev2.mseg`/`mkpf` direto, sem passar por GOLD — funciona, mas é uma consulta
ad hoc de app, não um model reutilizável por outros dashboards/relatórios.

Design completo (grão, campos, casos de uso, nota de performance/volume) em
**`docs/PROPOSTA_INGESTAO_MOVIMENTO_ESTOQUE.md`** (Parte B) — não depende da Parte A, a
fonte (`mseg`/`mkpf`) já está ingerida.
