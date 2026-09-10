# Regras de Negócio e Melhorias Propostas — Data Warehouse (`vendas_sap`)

> Documento consolidado pra levar ao time de dados (`data-platform`). Reúne (1) as regras de
> negócio/gotchas que **já existem hoje** e qualquer consumidor do DW precisa conhecer antes
> de confiar num número, e (2) melhorias propostas — as 3 specs formalizadas (câmbio TCURR,
> ingestão de movimento de estoque, ingestão de crédito e mestres SAP) já foram implementadas
> e mergeadas em `origin/main` do `data-platform` (2026-09-08/09, ver §5.11); os arquivos
> `PROPOSTA_*.md` correspondentes foram removidos deste repo em 2026-09-09 por não serem mais
> necessários — mais um conjunto novo de propostas que ainda não tinham virado documento,
> levantadas ao construir e usar o dashboard `invest_sap`.
>
> Fonte: `docs/CONTEXTO_VENDAS_SAP.md` (arquitetura completa), `docs/INVESTIGACAO_PENDENCIA_SAP.md`
> (achado `KWMENG=0`) e achados registrados durante o uso do app entre 2026-08-13 e
> 2026-09-05. Este documento não repete a arquitetura Bronze→Silver→Gold nem o catálogo de
> models — isso já está em `CONTEXTO_VENDAS_SAP.md` §2/§3; aqui o foco é **regra de negócio
> obrigatória** + **lacuna a corrigir**.

## Como usar este documento

- **Seção 1** — regras que **já valem hoje**: se você (humano ou IA) for escrever uma query
  ou um model novo em cima de `vendas_sap`/`vendas`, leia antes de confiar no número. Cada
  regra tem link pro achado completo (`CONTEXTO_VENDAS_SAP.md` ou memória da investigação).
- **Seção 2** — as 3 propostas de ingestão já com spec pronta (schema, config de pipeline,
  SQL de model) — só resumidas aqui, o documento fonte é o link.
- **Seção 3** — propostas **novas**, ainda sem spec de implementação, priorizadas por
  esforço/impacto. É a parte que falta em relação às propostas já formalizadas.
- **Seção 4** — investigação ao vivo no SAP/HANA e, mais tarde no mesmo dia, SQL Server
  (2026-09-05): campos nativos ainda não usados em nenhum model, testados um a um
  (preenchidos ou não, valem ou não como sinal) pra responder "dá pra pegar isso direto do
  SAP/Salesforce com mais precisão?" pra várias lacunas da seção 1/3. Inclui achados
  positivos (`VBUK.CMGST`, `VBAP.PRODH`/`LPRIO`, `T001K`→moeda oficial,
  `salesforce.User.regional` medido contra transação real, §4.11) e negativos (`PRCTR` não é
  segmentação de negócio, bloqueio nativo pouco usado, nenhuma outra função de parceiro serve
  pra vendedor, `MVKE`/`TVLP` descartados, trilha do SAP HR superada pela 4.11).
- **Seção 5** — tabela de priorização e dono sugerido (time de dados vs. time de
  negócio/funcional SAP — nem toda lacuna aqui é técnica).

---

## 1. Regras de negócio obrigatórias (não são bugs de query, são do dado)

### 1.1 Pendência / backlog (`fct_pendencia_sap`, `fct_pendencia_status_sap`)

| # | Regra | Por quê | Onde mitigado hoje |
|---|---|---|---|
| 1.1.1 | **Não confiar em `Flag_Pendencia=1` sem checar `Flag_Totalmente_Faturado`.** 52% dos itens "pendentes" (84,5% da quantidade pendente da base) já estão `Flag_Totalmente_Faturado=1` — contradição lógica. Mecanismo: `Qtd_Pendente_Operacional`/`Status_Pendencia_Estoque` usam `Qtd_Remetida`, que **nunca é populada** para pedidos de devolução (`ZREB`/`ZROB`/`ZRSG`/`ZRES`/`ZRET`/`ZDV1`/`ZBON`, e alguns "normais": `ZVCO`/`ZIND`/`ZDES`/`UVCO`/`UNCR`/`UDEV`), mesmo depois de 100% faturados/creditados. | Achado 2026-09-03, `pendencia_falso_positivo_faturado`. `Valor_Pendente_Faturamento=0` nesses casos — só quantidade é afetada, por isso não pegou em KPI de R$. | App: categoria `"Falso Positivo (já faturado)"` em `pages/27_Pendencia_x_Estoque.py`, checada antes de qualquer outro motivo, excluída por padrão das métricas. **Não corrigido no dbt.** |
| 1.1.2 | **`VBAP.KWMENG=0` esconde quantidade real em `ZMENG`** para 7 tipos de ordem (`ZVCO` 100% dos itens = R$ 2,45 bi, `UNCR`, `ZN01`, `ZDES`, `ZDRB`, `ZPEC`, `ZD01`). Sem correção, esses itens viravam `Qtd_Pedida=0` → `Flag_Pendencia=0` → `Status_Pendencia='Concluido'`, e **sumiam** de qualquer filtro de backlog. `ZVCO` é justamente o tipo marcado como prioridade máxima (`Prioridade_Pedido=1`, regra 1.1.4) — o bug atingia a categoria que o negócio trata como mais urgente. | `docs/INVESTIGACAO_PENDENCIA_SAP.md` (2026-08-24), caso real pedido 137490, cross-validado via Salesforce. | Fix `COALESCE(NULLIF(kwmeng,0), zmeng, 0)` (`33d0cf49`, branch `feature/restruct-sap-vendas`, repo `data-platform`) em `fct_vendas_itens_sap.sql`/`fct_vendas_canceladas_sap.sql`/`dim_pendencia.sql` (legado) — **confirmado em produção em 2026-09-05** (reconferido ao vivo em `GOLD.vendas_sap`: 0 itens restantes com o padrão `Qtd_Pedida_Original=0 AND Valor>0`). Proposta 3.2 concluída. |
| 1.1.3 | **67,9% do backlog aberto (R$ 230,5mi de R$ 339,6mi) tem >365 dias, e R$ 168mi tem >3 anos** — boa parte sem nenhuma reserva viva no SAP (`Qtd_Estoque_Reservada`/`VBBE` = 0). Provável "lixo de dado" (pedido nunca baixado/cancelado formalmente), não demanda real — mas não deve ser tratado automaticamente como cancelamento sem validar com vendas/SAP. | Achado 2026-09-03, `backlog_zumbi_achado`, via "Radar de pedido zumbi" (`pages/20_Pedidos.py`). | Só visualização/filtro no app — nenhum campo estruturado no Gold marca isso. Ver proposta 3.4. |
| 1.1.4 | **`Prioridade_Pedido` nunca é 2 ou 3** — só assume 1 (`ZVCO`) ou 9 (resto). `Status_Alocacao_Virtual='CARIMBAGEM'` depende de prioridade 2/3, que nunca ocorre: é **status morto** em produção, não um bug de SQL. | `CONTEXTO_VENDAS_SAP.md` §6.3. | Nenhuma — gap de regra de negócio conhecido, aberto desde antes desta investigação. Ver proposta 3.6. |
| 1.1.5 | **1.621 itens (0,39%) de `fct_pendencia_sap` sem `Descricao_Produto`** (join com `dim_material_sap` falhou); menor escala sem `Nome_Cliente` (66) ou `Nome_Centro` (24). Não investigado a fundo. | `docs/INVESTIGACAO_PENDENCIA_SAP.md` §7 (`integridade_dimensoes`), 2026-08-24. | Nenhuma — item aberto. Ver proposta 3.9. |

### 1.2 Crédito e devoluções (`fct_limite_credito_sap`, `fct_credito_devolucoes_sap`)

| # | Regra | Por quê |
|---|---|---|
| 1.2.1 | **`Cliente_Bloqueado=0` não garante ausência de bloqueio de crédito real.** Caso real: cliente 0001004873, `Cliente_Bloqueado=0` mas `Valor_Credito_Disponivel = -R$ 4.060.748,77`. Ao diagnosticar pedido travado, checar sinal/magnitude de `Valor_Credito_Disponivel`, não só a flag — e confirmar no SAP (VKM3/VKM4) antes de descartar crédito como causa. | Achado 2026-09-03, `credito_flag_nao_confiavel`. |
| 1.2.2 | **`Tp_doc='RV'` em `fct_credito_devolucoes_sap` (~91,8% das linhas, medido ao vivo em 2026-09-05 — não ">95%" como versões anteriores deste documento diziam) não é devolução de negócio** — é transferência de rotina de documento de faturamento (texto sempre "Transf.docs.faturam..."). Excluir por padrão em qualquer análise de "motivo de devolução/crédito real". | `CONTEXTO_VENDAS_SAP.md` §8.1. |
| 1.2.3 | **Correção + migração concluída (2026-09-05): `fct_credito_devolucoes_sap` TEM texto livre do lançamento** — coluna `Texto_Motivo`, 93,7% de cobertura (23.325/24.890 linhas), mesmo conteúdo do schema legado (ex. "Transf.docs.faturam. ..."). A afirmação anterior aqui ("não tem texto livre") estava errada, não só desatualizada. `scripts/query_vendas_sap.py::devolucoes_credito_motivo` foi migrada pra ler `fct_credito_devolucoes_sap` diretamente (não usa mais `vendas.dim_credito_devolucoes`), e `Montante` passou a vir com o **sinal contábil real** (`Indicador_Debito_Credito`: `S`=positivo, `H`=negativo) em vez de valor absoluto — mudança deliberada, ver docstring da função. `Tipo_Documento_Contabil` (RV/AB/DR/DG/DZ/LM/DA/EX/SA) segue sem tradução de código pra texto oficial no Gold (isso é outra coisa, resolvida pela ingestão de `T003T`, já com dado real confirmado — ver §4.9). | `CONTEXTO_VENDAS_SAP.md` §8.1 (corrigido). |
| 1.2.4 | **BUG confirmado em produção (2026-09-08): `fct_limite_credito_sap.Valor_Saldo_Vencido`/`Valor_Saldo_A_Vencer` são SEMPRE ZERO** — `TRY_CONVERT(DATE, CAST(zfbdt AS VARCHAR(8)), 112)` falha porque `zfbdt` já é `DATE` na Silver (o `CAST` corta a string, `112` não bate). Consumido em `pages/7_Credito_Devolucoes.py`/`pages/27_Pendencia_x_Estoque.py` — hoje mostra R$0,00 de "vencido" mesmo com R$1,05bi de exposição real (`Valor_Exposicao_Total_SAP`). **Não usar esses 2 campos até a correção subir** (`data-platform`). | §5.7/5.6 abaixo (achado + correção testada + filtro `blart='ZP'` que também falta). |

### 1.3 Estoque (`fct_estoque_lote_sap`)

| # | Regra | Por quê |
|---|---|---|
| 1.3.1 | **Custo unitário: sempre dividir por `PEINH`** (`MBEW.VERPR`/`STPRS` é preço para `PEINH` unidades, não por unidade). Achado real: material com `PEINH=10000` tinha custo unitário de R$428.037,74 em vez de R$42,80. **Corrigido** em `fct_estoque_lote_sap.sql` (commit `ad60d108`) — mas qualquer novo model que leia `MBEW`/`MBEWH` precisa repetir a correção (`... / NULLIF(COALESCE(PEINH,1),0)`). | `CONTEXTO_VENDAS_SAP.md` §6.9(1). |
| 1.3.2 | **Valor financeiro de estoque em moeda não-BRL não é convertido.** Centros Uruguai (2000/2100/2400/2500/2600/2700) valoram em UYU, `CO10` (Colômbia) em COP. Sem `TCURR` ligado no Gold, o app filtra `Pais_Centro='BR'` nos totais e expõe `Moeda` no detalhe. | `CONTEXTO_VENDAS_SAP.md` §6.9(2). Correção formal: proposta já existente, ver §2.1 deste doc. |
| 1.3.3 | **`Data_Producao`/`Data_Validade` usam `TRY_CAST`**, nunca `CAST` — SAP grava `'00000000'` quando a data não se aplica ao lote. | `CONTEXTO_VENDAS_SAP.md` §6.4. |
| 1.3.4 | **`Delta_Estoque_Deposito_Vs_Lotes` ≠ 0 é sinal de inconsistência** entre a visão por depósito (`MARD`) e por lote (`MCHB`) — checar antes de confiar em `fct_estoque_lote_sap` numa investigação de ruptura. | `CONTEXTO_VENDAS_SAP.md` §6.5. |
| 1.3.5 | **Foto do dia, sem histórico.** Para "estoque numa data passada", a fonte real é `MCHBH`/`MBEWH` (fechamento de período no HANA, ainda fora do DW) — reconstruir via soma de `MSEG`/`MKPF` dá número errado quando o material já tinha estoque antes do início da réplica (~2024). | `CONTEXTO_VENDAS_SAP.md` §11.1, memória `mchbh_estoque_historico_real`. Proposta de ingestão já existe, ver §2.2. |

### 1.4 Vendedor (`dim_vendedor_sap`, `dim_vendedor_sf`)

| # | Regra | Por quê |
|---|---|---|
| 1.4.1 | **`dim_vendedor_sap` está sempre vazia em produção** (`VBPA.PARVW='VE'` nunca ocorre, 0 de ~3,5M linhas). O vendedor real usado é `COALESCE(VBPA..., Salesforce.OpportunityLineItem.Vendedor__c)`, cobertura via Salesforce ~72,97% (medida 2026-08-13, não reconferida desde então). | `CONTEXTO_VENDAS_SAP.md` §6.1. |
| 1.4.2 | **Domínios de chave incompatíveis**: `Codigo_Vendedor` SAP (`KUNNR`) e Salesforce (`Id`) nunca devem ser comparados diretamente — usar `Origem_Vendedor` pra saber contra qual dimensão dar join. | `CONTEXTO_VENDAS_SAP.md` §6.1. |

### 1.5 Faturamento multi-moeda e Linha de Negócio

| # | Regra | Por quê |
|---|---|---|
| 1.5.1 | **Nunca somar `Valor_Liquido_Faturamento`/`Valor_Liquido_Pedido` sem separar por `Moeda`.** 5 moedas convivem (BRL/UYU/COP/USD/CLP) — soma direta gerou R$2,43bi vs. real R$1,04bi no mesmo período. | `CONTEXTO_VENDAS_SAP.md` §10.0. Correção formal (`TCURR`): proposta já existente, §2.1. |
| 1.5.2 | **Linha de Negócio (AESTHETICS/FARMA/ONCO-HEMATO) não existe como campo de sistema** em SAP nem Salesforce — vem de crosswalk manual (SharePoint `dCLIENTE_SETOR`/`dESTRUTURA`), cobertura ~52% (~87% com heurística de produto, precisão desigual: AESTHETICS ~96%, FARMA ~100% mas raro, ONCO/HEMATO só ~64%). Testado e descartado usar campos de cadastro SAP/Salesforce como substituto — o teto é a completude do dado manual, não a estratégia de join. | Memória `linha_negocio_sem_fonte_sistema`, `CONTEXTO_VENDAS_SAP.md` §8.1/§8.2. |
| 1.5.3 | **`GOLD.vendas.fat_faturamento` (legado) não deve ser reaberto como fonte de medida de faturamento**, mesmo que bata melhor com um Painel Vendas externo — decisão explícita do usuário (2026-08-25) pra não ter 2 fontes de faturamento concorrentes no app. Schema `vendas` segue permitido só para `fat_meta_equipe` (Meta, sem alternativa) e tabelas de crosswalk/dimensão (`dim_cliente_setor`, `dim_estrutura`, `dim_produto`). | Memória `painel_vendas_fonte_dados`. |
| 1.5.4 | **Correção (2026-09-05): `vendas.fat_meta_equipe` JÁ segmenta o canal MS, ao menos parcialmente, e a página já mostra isso.** `dim_estrutura` tem nós `MS` nativos (`701/702/703000000`, espelhando os nós `Publico` `601/602/603000000`), e `fat_meta_equipe` tem 87 linhas com `bu='MS'` sob `cod_setor=701000000` (ONCO/HEMATO), somando R$85,3mi de meta em 2025. A afirmação anterior ("Meta de Canal='MS' sempre 0/NULL") estava errada — confirmado ao vivo que `scripts/query_faturamento_comercial.py::meta_vs_realizado_por_dimensao(dimensao='Canal')` já reconhece `e.descricao LIKE '% - MS'` e retorna Meta real/não-zero pra MS na maioria dos meses de 2025 (a lógica já existia no código, só o docstring da função e a documentação estavam errados). Ressalva que continua válida: `702000000`/`703000000` (FARMA-MS/AESTHETICS-MS) não têm nenhuma linha de meta — só ONCO/HEMATO segmenta MS hoje; e alguns meses esparsos (mai/out/nov de 2025) não têm meta cadastrada. Nenhuma mudança de código necessária, só de documentação (já corrigida no docstring da função). | `CONTEXTO_VENDAS_SAP.md` §10.2 (corrigido). |

### 1.6 Engenharia de consulta (SQL Server) — regra de processo, não só de dado

| # | Regra | Por quê |
|---|---|---|
| 1.6.1 | **Query parametrizada com CTE + filtro sobre expressão calculada pode dar resultado ERRADO com plano cacheado, não só lento.** Confirmado: mesma query, dois resultados diferentes e reprodutíveis, variando só por espaço em branco fora de qualquer cláusula. `OPTION (RECOMPILE)` corrigiu de forma consistente em todo teste — mas não é grátis: em `TOP N ... ORDER BY` com múltiplos `JOIN`, teve o efeito oposto (3s → 30-50s). Testar os dois lados (tempo + resultado conferido contra soma calculada de outro jeito) antes de decidir numa query nova desse formato. | Memória `sql_server_plan_cache_bug`, `CONTEXTO_VENDAS_SAP.md` §6.10. |
| 1.6.2 | **Funções de `scripts/query_vendas_sap.py` com o mesmo formato de risco ainda não auditadas**: `faturamento_por_org_vendas_linha_negocio`, `meta_vs_realizado_mensal`, `correlacao_oportunidade_pedido_pendencia_fatura`, helpers `_filtro_periodo_tipo_cliente`/`_condicao_tipo_cliente_por_codigo`. Não confiar cegamente num número dessas até auditar. | `CONTEXTO_VENDAS_SAP.md` §6.10, ver proposta 3.7. |

### 1.7 Timezone e datas

| # | Regra | Por quê |
|---|---|---|
| 1.7.1 | Campos de aging (`Dias_Desde_Inclusao_Pedido`, `Dias_Aging_Credito`, `Data_Processamento_DW`) usam **BRT explícito** (`AT TIME ZONE`) desde 2026-08-13, não horário de servidor implícito — se um número parecer "off by 3h", checar se o model já foi migrado. | `CONTEXTO_VENDAS_SAP.md` §6.6. |
| 1.7.2 | **`TCURR.GDATU` vem invertido** (`GDATU = 99999999 - AAAAMMDD`) — corrigido na decodificação da Silver junto com a implementação da §2.1 (ver §5.11); qualquer consumo direto fora da Silver já decodificada ainda precisa decodificar isso antes de usar como data. | §2.1/§5.11. |

---

## 2. Melhorias já propostas — **implementadas, spec removida** (ver §5.11)

As 3 specs abaixo foram implementadas e mergeadas em `origin/main` do `data-platform`
(commits `64509e2e`/`ac897d2f`, 2026-09-08, merge pra `main` confirmado em 2026-09-09) —
resumo mantido aqui por contexto histórico, mas os documentos `PROPOSTA_*.md` originais
(config de pipeline, SQL de model, schema de campo) foram apagados deste repo em 2026-09-09
por já não serem mais necessários. Status de validação com dado real por peça em §5.11.

### 2.1 Conversão de câmbio (`TCURR`) — implementado e validado
Ingeriu `IB_SAPECC.TCURR` (Bronze+Silver) e ligou conversão pra BRL nos 3 models multi-moeda
(`fct_faturamento_itens_sap`, `fct_vendas_itens_sap`, `fct_pendencia_sap`), resolvendo a regra
1.5.1/1.3.2 dentro do DW em vez de round-trip ao HANA no app a cada carregamento de página.

### 2.2 Movimento de estoque (`MCHBH`/`MBEWH` + `fct_movimento_lote_sap`) — implementado
- **Parte A**: ingeriu `MCHBH`/`MBEWH` (Bronze+Silver) — resolve a regra 1.3.5 (estoque
  histórico real) dentro do DW. Implementada, ainda sem validação de dado real.
- **Parte B**: novo model `fct_movimento_lote_sap` (timeline de movimento por lote, via
  `mseg`/`mkpf`, já ingeridos) — antes era consulta ad hoc de app (`scripts/trace_lote.py`).
  Implementada e validada com dado real (2,52 milhões de linhas).

### 2.3 Crédito e mestres SAP (`VBUK`/`T001K`/`T003T`) — implementado
Nasceu direto da investigação da seção 4 abaixo (não é achado anterior, é a formalização dos
3 achados que precisam de ingestão nova, vs. os que só precisam de SELECT em tabela já
ingerida — ver a árvore de decisão no início da seção 4). 3 peças independentes, todas
implementadas, nenhuma validada com dado real ainda:
- **Parte A**: ingeriu `VBUK` (Bronze+Silver) — expõe `Status_Credito_Documento_SAP`
  (`CMGST`, achado §4.1) por pedido, mais preciso que a regra 1.2.1 atual.
- **Parte B**: ingeriu `T001K` (Bronze+Silver) — de-para oficial centro→empresa→moeda
  (achado §4.9), substitui a heurística `Pais_Centro` e destrava a conversão de câmbio
  (§2.1) com dado real.
- **Parte C**: ingeriu `T003T` (Bronze+Silver) — texto de tipo de documento contábil (regra
  1.2.3/proposta 3.8), resolve o bloqueio que a proposta 3.8 tinha registrado.

---

## 3. Melhorias novas propostas (sem spec de implementação ainda)

Levantadas ao longo do uso do dashboard, sem overlap com as propostas da seção 2. Ordenadas
por tema, não por prioridade (ver tabela de priorização §5).

### 3.1 Corrigir `Flag_Pendencia`/`Status_Pendencia_Estoque` para considerar `Flag_Totalmente_Faturado`
**Problema**: regra 1.1.1 — `fct_pendencia_sap.sql` calcula pendência de quantidade só a
partir de `Qtd_Remetida`, nunca checando se o item já foi 100% faturado/creditado. Afeta
52% dos itens "pendentes" (84,5% da quantidade).
**Proposta**: no cálculo de `Qtd_Pendente_Operacional`/`Status_Pendencia_Estoque`, tratar
`Flag_Totalmente_Faturado=1` como sinal de conclusão mesmo quando `Qtd_Remetida` não foi
populada (típico de devolução) — equivalente ao que a mitigação no app já faz
(`pages/27_Pendencia_x_Estoque.py::Motivo_Principal="Falso Positivo (já faturado)"`), só que
corrigindo a fonte em vez do consumo.
**Esforço**: médio (mudança de lógica em model já complexo, precisa de teste de regressão
comparando volume antes/depois). **Dono**: time de dados (`data-platform`).

### 3.2 ~~Deploy do fix `KWMENG=0`/`ZMENG`~~ (regra 1.1.2) — CONCLUÍDO, confirmado em produção (2026-09-05)
**Problema (histórico)**: commit `33d0cf49` (branch `feature/restruct-sap-vendas`) corrigia
R$ 2,45bi+ de backlog escondido (`ZVCO` e mais 6 tipos de ordem), mas quando este documento
foi escrito ainda não tinha passado por `dbt build`/deploy em produção.
**Status atual**: reconferido ao vivo em `GOLD.vendas_sap` em 2026-09-05 — `fct_vendas_itens_sap`
tem 0 itens com o padrão `Qtd_Pedida_Original=0 AND Valor_Liquido_Pedido>0` (era ~7.000 antes),
o pedido 137490 já reflete a quantidade corrigida, e
`scripts/audit_pendencia_flow.py::pendencia_escondida` zerou (era 15 itens) — exatamente o
critério de sucesso que este item propunha. Ver `docs/INVESTIGACAO_PENDENCIA_SAP.md` §6/§7.1.
Nenhuma ação pendente aqui.

### 3.3 Cobertura de testes dbt nos models críticos de `vendas_sap`
**Problema**: pelo menos 2 achados reais só foram descobertos por auditoria manual/uso do
app, não por teste automatizado: o fanout de `dim_centro_sap` (§`CONTEXTO_VENDAS_SAP.md`
§6.2, corrigido em 2026-08-20) e os 1.621 itens sem `Descricao_Produto` (regra 1.1.5, ainda
aberto). Nenhum dos dois teria passado despercebido com `dbt test` básico.
**Proposta**: adicionar (a) teste `unique`/`not_null` na chave de grão de cada dimensão
(`dim_centro_sap`, `dim_cliente_sap`, `dim_material_sap`) pra pegar fanout de join cedo; (b)
teste `relationships` de `fct_pendencia_sap` contra `dim_material_sap`/`dim_cliente_sap`/
`dim_centro_sap` com um limiar de tolerância (não travar o pipeline, mas alertar se a
% de linhas sem match subir); (c) teste customizado equivalente a `valor_sem_quantidade`
(`Qtd_Pedida=0 AND Valor_Liquido_Pedido>0`) pra pegar recorrência do padrão do achado
`KWMENG` em outros campos/tipos de ordem no futuro.
**Esforço**: médio (não é 1 mudança, é processo contínuo — mas cada teste individual é
barato). **Dono**: time de dados.

### 3.4 Campo estruturado de "backlog sem reserva"/idade no Gold
**Problema**: regra 1.1.3 — hoje o "Radar de pedido zumbi" recalcula tudo no app
(`pages/20_Pedidos.py`) toda vez que a página carrega; qualquer outro consumidor do backlog
(relatório externo, outro dashboard) reproduziria o cálculo do zero ou, pior, nem saberia do
problema.
**Proposta**: adicionar em `fct_pendencia_sap` (ou `fct_pendencia_status_sap`) um campo
`Flag_Backlog_Sem_Reserva` (`Qtd_Estoque_Reservada=0 AND Dias_Desde_Inclusao_Pedido > limiar`,
limiar sugerido 365 dias por já estar validado no app) — vira filtro nativo em SQL em vez de
lógica reimplementada por cada consumidor.
**Esforço**: baixo (campo derivado simples, sem novo join). **Dono**: time de dados, com
validação do limiar junto ao time de vendas/negócio (não tratar como "cancelar
automaticamente" — pode haver contrato de fornecimento longo legítimo).

### 3.5 Formalizar `Cliente_Bloqueado_Efetivo` em `fct_limite_credito_sap`
**Problema**: regra 1.2.1 — a flag SAP nativa (`Cliente_Bloqueado`) pode ser 0 com o cliente
milhões negativo no limite; hoje cada análise precisa lembrar de checar os dois campos.
**Proposta**: campo derivado `Cliente_Bloqueado_Efetivo = CASE WHEN Cliente_Bloqueado=1 OR
Valor_Credito_Disponivel < 0 THEN 1 ELSE 0 END` (ajustar limiar/regra exata com o time
financeiro — pode haver tolerância pequena negativa que não deveria contar), documentado
como "sinal ampliado", sem substituir a flag original (manter as duas colunas).
**Esforço**: baixo. **Dono**: time de dados + validação com financeiro (a regra exata do
que conta como "bloqueio efetivo" é decisão de negócio, não só técnica).

### 3.6 Resolver o status morto `CARIMBAGEM`/`Prioridade_Pedido` (regra 1.1.4)
**Problema**: gap de regra de negócio conhecido desde antes desta investigação — a escala
original previa prioridades 2/3 que nunca são atribuídas.
**Proposta**: não é uma correção técnica — é uma pergunta pro time de negócio: (a) a
priorização fina (VKORG específico, lista de AUART) ainda é necessária? Se sim, implementar
de fato em `fct_pendencia_status_sap`; se não, remover o status morto do código pra não
confundir quem for debugar alocação virtual no futuro.
**Esforço**: baixo técnico, mas depende de decisão de negócio primeiro. **Dono**: time de
negócio (decisão) → time de dados (implementação, se confirmado que ainda é necessário).

### 3.7 Auditoria dedicada de plan-cache bug nas funções restantes (regra 1.6.2)
**Problema**: só `scripts/query_faturamento_comercial.py` foi auditado/corrigido com
`OPTION (RECOMPILE)`; `faturamento_por_org_vendas_linha_negocio`, `meta_vs_realizado_mensal`,
`correlacao_oportunidade_pedido_pendencia_fatura` e os helpers de filtro em
`scripts/query_vendas_sap.py` têm o mesmo formato de risco mas nunca foram testados.
**Proposta**: para cada função, comparar o resultado atual contra uma soma calculada de
outro jeito (ex.: agregação em pandas depois de trazer o detalhe cru) e testar
`OPTION (RECOMPILE)` medindo tempo + resultado, igual foi feito no módulo já corrigido —
aplicar a hint só onde o resultado muda ou o ganho de correção supera o custo de
performance.
**Esforço**: médio (é auditoria função por função, não uma mudança única). **Dono**: quem
mantém `invest_sap` (não é uma mudança de DW, é validação de consumo — citado aqui porque
afeta diretamente a confiabilidade das "visões" pedidas).

### 3.8 Ingerir `T003T` (descrição de tipo de documento contábil) — parte (b) CONCLUÍDA, era suposição errada
**Problema (histórico)**: regra 1.2.3 — pensava-se que `fct_credito_devolucoes_sap` só tinha
código de `Tipo_Documento_Contabil`, sem tradução (`T003T` existe no DDIC mas não estava
replicada como dado), e que o texto livre só existia no schema legado
`vendas.dim_credito_devolucoes.Texto`.
**Proposta original**: (a) adicionar `T003T` a um grupo de ingestão diário/mestre existente
(tabela pequena, texto estático) pra pelo menos dar nome legível ao código; (b) avaliar se o
texto de lançamento (`BSEG.SGTXT`) pode alimentar um campo `Texto_Lancamento` em
`fct_credito_devolucoes_sap`, aproximando a paridade com o legado sem precisar do schema
`vendas` pra esse propósito.
**Correção + migração concluída (2026-09-05, validação de docs)**: a parte (b) não precisava
de investigação — **já existia**. `fct_credito_devolucoes_sap` tem a coluna `Texto_Motivo`,
93,7% populada (23.325/24.890 linhas), com o mesmo conteúdo do legado (ver regra 1.2.3).
`scripts/query_vendas_sap.py::devolucoes_credito_motivo` foi migrada nesta mesma sessão pra
ler `fct_credito_devolucoes_sap` diretamente (não usa mais `vendas.dim_credito_devolucoes`),
testada e validada (`pages/7_Credito_Devolucoes.py` atualizada junto). Decisão adicional
tomada na migração: `Montante` passou a expor o **sinal contábil real** (`Indicador_Debito_Credito`:
`S`=débito positivo, `H`=crédito negativo) em vez do valor absoluto que o legado usava —
muda o significado de somas agregadas (posição líquida, não total bruto), documentado na
página e no docstring da função. Falta só (a) — a ingestão formal de `T003T` no `data-platform`
pra dar nome oficial ao *código* de `Tipo_Documento_Contabil` (diferente do texto do
lançamento, que já existe).
**Esforço**: baixo (a) `T003T` já liberada e confirmada com dado real (§4.9), pode ir direto
pra ingestão. **Dono**: time de dados (a) — (b) já foi feito por quem mantém `invest_sap`.

### 3.9 Investigar as dimensões sem match em `fct_pendencia_sap` (regra 1.1.5)
**Problema**: 1.621 itens (0,39%) sem `Descricao_Produto`, 66 sem `Nome_Cliente`, 24 sem
`Nome_Centro` — não investigado desde o achado em 2026-08-24.
**Proposta**: isolar as chaves (`Codigo_Produto`/`Codigo_Cliente`/`Codigo_Centro`) desses
itens e checar se existem em `dim_material_sap`/`dim_cliente_sap`/`dim_centro_sap` com chave
composta diferente (ex.: mandante ou organização de vendas divergente) — decidir se é
dimensão desatualizada (rodar de novo o master data) ou lacuna real de cadastro.
**Esforço**: baixo (investigação pontual, não muda pipeline até saber a causa). **Dono**:
time de dados.

### 3.10 Formalizar Linha de Negócio como model Gold
**Problema**: a heurística de fallback por produto vive só dentro de
`scripts/query_vendas_sap.py::faturamento_por_org_vendas_linha_negocio`, em Python/pandas —
qualquer outro consumidor (outro dashboard, um relatório em outra ferramenta) reimplementaria
a mesma lógica do zero, arriscando divergir.
**Proposta**: promover a lógica a um model Gold — ex. `dim_cliente_linha_negocio_sap` — pra
virar 1 fonte só, testável com `dbt test`, em vez de query ad hoc.
**Atualização (2026-09-05)**: quando esta proposta foi escrita, o teto de cobertura (~87% com
heurística por cliente) parecia um problema de dado manual incompleto, fora de alcance
técnico. A investigação da §4.3 mudou isso: `MARA.PRDHA`, medido no recorte certo (produto
realmente faturado), cobre **92,4% do valor** — maior que o teto atual. Ainda falta validar
se os *rótulos* de `PRDHA` batem com AESTHETICS/FARMA/ONCO-HEMATO (§4.3), mas se baterem, o
model Gold desta proposta deveria nascer usando `PRDHA` como fonte primária (por item) em vez
de só formalizar a heurística por cliente já existente — ver §4.3 antes de implementar.
**Esforço**: médio (portar lógica existente e validada pro dbt — mas com a lógica em si
possivelmente mudando por causa da §4.3, avaliar as duas juntas antes de codar).
**Dono**: time de dados (model) + time comercial (validar se os rótulos de `PRDHA` fazem
sentido de negócio, e completar cadastro nos materiais de alto valor sem `PRDHA`, §4.3).

### 3.11 Investigar por que `VBPA.PARVW='VE'` nunca ocorre (regra 1.4.1)
**Problema**: `dim_vendedor_sap` é 100% vazia em produção há pelo menos desde 2026-08-13 —
não se sabe se é configuração SAP ausente (parceiro "vendedor" nunca é gravado no pedido
nesta implementação) ou lacuna de extração.
**Proposta**: perguntar ao time funcional SAP se a partner function `VE` é usada nesta
configuração; se a resposta for "não, nunca foi", considerar depreciar/documentar
`dim_vendedor_sap` como intencionalmente vazia (evita alguém tentar "consertar" um join que
não tem conserto técnico) e formalizar `dim_vendedor_sf` como fonte primária de fato, não só
fallback.
**Esforço**: baixo (é uma pergunta, não uma mudança de pipeline) — mas decide a direção de
qualquer trabalho futuro em vendedor/comissão. **Dono**: time de negócio/funcional SAP
(responde) → time de dados (documenta/formaliza depois).

### 3.12 Model formal de reconciliação `GOLD.vendas` × `GOLD.vendas_sap`
**Problema**: pergunta em aberto desde `CONTEXTO_VENDAS_SAP.md` §9 — não existe hoje um
model que concilie os dois schemas de faturamento pro mesmo período/pedido. A única coisa
parecida é `correlacao_oportunidade_pedido_pendencia_fatura`, uma junção ad hoc em pandas no
app (`pages/5_Jornada_Pedido.py`), não um model dbt.
**Proposta**: se o uso desse tipo de conciliação crescer, promover a query pandas a model
Gold — grão pedido/período, com as duas medidas lado a lado e um `Delta_Percentual`
explícito, reaproveitando os aprendizados de moeda (§10.0/§1.5.1) pra não repetir o erro de
somar moedas misturadas na comparação.
**Esforço**: médio-alto (só vale a pena se o uso justificar — não propor só por completude).
**Dono**: time de dados, sob demanda.

---

## 4. Investigação ao vivo no SAP (2026-09-05) — campos nativos não usados hoje

Investigação feita direto no HANA/Datasphere (`IB_SAPECC`, mesma conexão de
`scripts/ddic_lookup.py` — **acessível sem VPN** neste ambiente) pra responder uma pergunta
concreta: existe campo nativo do SAP, ainda não usado em nenhum model, que resolva ou
melhore algum dos gaps das seções 1/3 com mais precisão do que a solução atual (crosswalk
manual, heurística, inferência por saldo)? Metodologia: `DD02T`/`DD03L`/`DD04T`/`DD07T`
(DDIC) pra confirmar campo e texto oficial, depois `GROUP BY`/`COUNT` ao vivo pra medir se o
campo está realmente preenchido nesta configuração SAP — o mesmo cuidado que já invalidou
`BRSCH`/`CNAE`/`VKBUR`/`VKGRP` na investigação anterior (§8.2 de `CONTEXTO_VENDAS_SAP.md`)
também se aplica aqui: campo existir no DDIC não quer dizer que tem dado. O SQL Server
(BRONZE/SILVER/GOLD) exige rede/VPN da empresa e ficou indisponível na primeira parte desta
investigação (por isso §4.1-4.9 usam só HANA) — liberado mais tarde no mesmo dia, permitindo
cruzar achado do SAP contra dado real de `GOLD`/`SILVER` nas seções 4.10/4.11.

**Importante sobre esforço de implementação**: `VBAK`/`VBAP`/`MARA`/`VBEP` **já são
ingeridos** hoje (grupos `sd_hourly_h10`/`master_daily_h9`, `CONTEXTO_VENDAS_SAP.md` §5) — os
campos novos abaixo que vêm dessas tabelas são só **adicionar coluna no SELECT dos models
Silver/Gold já existentes**, não uma ingestão nova. Só `VBUK` (§4.1) exige Bronze+Silver
novos, igual às duas propostas já formalizadas (§2).

### 4.1 `VBUK.CMGST` — status de crédito nativo do pedido (melhora a regra 1.2.1 / proposta 3.5)

Achado forte: `VBUK` (status agregado do documento de vendas, **não ingerido hoje**) tem o
campo `CMGST` com o **veredito de crédito que o próprio SAP já calculou por pedido**, textos
oficiais confirmados via `DD07T`:

| `CMGST` | Texto oficial (DD07T, PT) | Contagem ao vivo (2026-09-05) |
|---|---|---|
| *(vazio)* | Verif.crédito não foi efetuada/status não foi definido | 831.838 |
| `A` | Verif.crédito foi efetuada, operação oK | 85.881 |
| `B` | Verif.crédito foi efetuada, operação **não oK** | **2.978** |
| `C` | Verif.crédito efetuada, operação não oK, liberação parcial | (incluído no grupo D abaixo na contagem por N, ver nota) |
| `D` | Operação liberada pelo responsável crédito | 128.195 |

`B` é precisamente o "bloqueio de crédito ativo" que a regra 1.2.1 hoje só infere pelo sinal
de `Valor_Credito_Disponivel` — **2.978 pedidos com o próprio SAP dizendo "não OK"**, sem
precisar de heurística. `D` (128 mil) é um achado colateral interessante: volume grande de
pedidos que passaram por bloqueio e foram liberados manualmente pelo responsável de crédito —
sinal de fricção de processo, não é bug.

**Proposta (substitui/reforça a 3.5) — implementada, ver §2.3/§5.11**: ingerir `VBUK`
(Bronze+Silver) e expor `CMGST`/texto em `fct_pendencia_sap` ou `fct_limite_credito_sap` como
`Status_Credito_Documento_SAP` — sinal por **pedido**, mais granular que o limite de crédito
por cliente (`fct_limite_credito_sap`, que é por cliente+área de crédito, não por pedido
individual). Dono: time de dados.

### 4.2 `VBAP.LPRIO` — prioridade de entrega nativa por item (resolve a decisão da 3.6)

Existe e está preenchida — resolve de vez a pergunta de negócio da proposta 3.6 sem precisar
esperar decisão: o SAP **já tem** um campo de prioridade granular por item, diferente do
`Prioridade_Pedido` derivado hoje só de `AUART='ZVCO'`:

| `LPRIO` | Contagem ao vivo |
|---|---|
| `02` | 392.547 (maioria) |
| `00` | 78.985 |
| `01` | 1.038 |

Não consegui confirmar o texto oficial de cada código — a tabela `TVLP`, liberada e testada
depois (§4.9), **não é a tabela de descrição de `LPRIO`** (são campos de determinação de
lote, suposição errada minha) — mas o campo **existe e tem 3 valores reais distintos**,
diferente do esquema atual que só distingue 1/9. Antes de implementar, confirmar com o time
funcional SAP o texto de `00`/`01`/`02` (provável ordem "Alta/Média/Normal", convenção comum
de `LPRIO`, mas não assumir sem confirmar) — a tabela de customizing certa ainda não foi
identificada.

**Proposta**: se confirmado que `00`/`01`/`02` mapeia numa escala de prioridade de negócio
coerente, usar `VBAP.LPRIO` (ou o equivalente de `LIPS.LPRIO` na remessa) em vez da regra
`AUART='ZVCO'→1, resto→9` — resolveria o status morto `CARIMBAGEM` (regra 1.1.4) com dado
real em vez de decisão de negócio pendente. Esforço: baixo (campo já ingerido via `VBAP`, só
adicionar ao SELECT) + confirmação funcional. Dono: time de dados + time funcional SAP
(confirmar os textos).

### 4.3 `VBAP.PRODH`/`MARA.PRDHA` — hierarquia de produto nativa (candidato pra Linha de Negócio, reforça 1.5.2/3.10)

Achado mais relevante desta investigação: a investigação anterior (§8.2 de
`CONTEXTO_VENDAS_SAP.md`) testou `BRSCH`/`CNAE`/`KATR1-10`/`KVGR1-5`/`KDGRP`/`VKBUR`/`VKGRP`
como possível fonte nativa de Linha de Negócio e descartou todos — mas **nunca testou a
Hierarquia de Produto (`PRODH`/`PRDHA`)**, o campo que o SAP usa nativamente pra esse tipo de
rollup. Testado ao vivo agora, em `VBAP.PRODH` (nível 1 da hierarquia, primeiros 4
caracteres), por quantidade de itens e valor:

| Nível 1 (`PRODH`) | Itens | Valor (`NETWR`) |
|---|---|---|
| *(vazio)* | 208.669 (44%) | R$ 23,2 bilhões (**68% do valor**) |
| `ESPE` (Especialidades) | 110.740 | R$ 3,38 bi |
| `ONCO` (Onco) | 57.807 | R$ 1,33 bi |
| `BIOL` (Biológicos) | 53.804 | R$ 6,37 bi |
| `OUTR` (Outros) | 41.530 | R$ 1,01 bi |
| `AEST` (Aesthetics) | 12 | R$ 24 mil |
| `HEMO`/`HEMA`/`ANTI` | 2-4 cada | irrelevante |

`MARA.PRDHA` (mestre do material, mesma hierarquia) mostra os mesmos segmentos com mais
granularidade nos poucos materiais preenchidos (~600 de 80 mil): `ONCO.ONCI.*`,
`ESPE.ANTI/ANTT/GAST.*`, `HEMO.HEMO/ANTA.*`, `AEST.AGEP.TOXINA_B` (toxina botulínica — bate
com Aesthetics), `BIOL.*` — os nomes dos segmentos batem conceitualmente com as categorias já
conhecidas (AESTHETICS/FARMA/ONCO-HEMATO) de forma muito mais clara que qualquer campo
testado antes.

**Ressalva original (2026-09-05, manhã) — corrigida depois no mesmo dia, ver abaixo**: a
medida de 68% de valor vazio acima foi tirada em cima de **todo `VBAP` histórico** (469 mil
itens de pedido, qualquer tipo de ordem, incluindo cotação/amostra/intercompany/matéria-prima
nunca vendida a cliente) — não é o recorte certo pra decidir se o campo serve pra Linha de
Negócio, porque essa pergunta só importa pro que **é de fato faturado**.

**Correção (2026-09-05, tarde, com SQL Server liberado)**: medi de novo restringindo ao
universo que o próprio app já usa pra "Produto" (`~1.700-1.839` materiais com pelo menos 1
fatura no histórico, `CONTEXTO_VENDAS_SAP.md` §10.2) — juntando `fct_faturamento_itens_sap`
(GOLD) com `MARA.PRDHA` (HANA) por `Codigo_Produto`/`MATNR`. O resultado muda muito:

| Métrica | Todo `VBAP` (recorte errado) | Só produtos faturados (recorte certo) |
|---|---|---|
| Cobertura por item | 55% | **87,7%** |
| Cobertura por valor | 32% | **92,4%** |

Por segmento (nível 1 de `PRDHA`, só produtos faturados, por valor): `HEMO` domina com
**64,6%** do valor faturado (R$ 20,7 bi — hemoderivados, alto valor unitário; não aparecia
nos números antigos porque são poucos materiais distintos, mas de giro altíssimo), `ESPE`
11,6%, `ANTI` 7,1%, `ONCO` 5,5%, `AEST` 2,4%, `BIOL`/`PRES`/`HEMA` pequenos, e só **7,6%**
sem `PRDHA` (R$ 2,42 bi) — concentrado em poucos materiais de alto valor (`PA8792`=R$226mi,
`PA9210`=R$120mi, `PA9212`=R$108mi, `PA6402`=R$94mi, entre outros): **cadastrar `PRDHA` só
nesses ~15-20 materiais** fecharia a maior parte do gap restante — um trabalho pontual de
master data, não um problema estrutural.

**Nota técnica**: `VBAP.PRODH` (foto no momento do pedido) e `MARA.PRDHA` (mestre atual) às
vezes divergem pro mesmo material (ex.: materiais hoje em `HEMO.*` apareciam como `BIOL.*` na
consulta antiga sobre `VBAP`) — usar `MARA.PRDHA` (mestre atual) como referência, não
`VBAP.PRODH` histórico, se o objetivo é a classificação de negócio vigente.

**Precisão dos rótulos — testada e medida (2026-09-05, com SQL Server liberado)**: cruzando
o segmento `PRDHA` dominante por cliente (mesma metodologia da heurística de produto do §8.2)
contra os ~1.789 clientes com rótulo manual conhecido:

| Linha de Negócio manual | Segmento `PRDHA` dominante mais comum | % nesse segmento |
|---|---|---|
| AESTHETICS | `AEST` | **92,6%** |
| FARMA | `ESPE` | 45,6% (resto espalhado: `ONCO` 26,8%, `PRES` 16,6%, `HEMO` 5,9%) |
| ONCO/HEMATO | `ESPE` | 33,1% (resto espalhado: `HEMO` 28,7%, `ANTI` 24,3%, `ONCO` só 11,2%) |

**AESTHETICS discrimina muito bem** (92,6%, no mesmo patamar da heurística antiga ~96%).
**FARMA e ONCO/HEMATO não discriminam** — as duas se espalham pelos mesmos segmentos
(`ESPE`/`HEMO`/`ANTI`/`ONCO`) sem uma categoria dominante clara, pior até que a heurística
antiga (que já tinha esse mesmo problema, ~64% pra ONCO/HEMATO, mas pelo menos tinha *uma*
categoria majoritária). Cobertura alta (92,4% do valor) não implica rótulo confiável — são
perguntas diferentes, e ficou provado que só a 1ª tem resposta boa aqui.

**Tentativa de implementação (2026-09-05) — chegou a ser codada e depois revertida**: dado
que só AESTHETICS discrimina bem, implementei uma 3ª camada em
`scripts/query_vendas_sap.py::faturamento_por_org_vendas_linha_negocio` (cliente cuja família
de materiais `PRDHA='AEST%'` responde por >50% do valor histórico faturado → AESTHETICS,
mesmo formato de fallback das camadas 1/2). Rodei contra o `GOLD` de verdade: **763 clientes
bateram no critério, mas 756 já tinham rótulo manual, e todos os 7 restantes já eram
resolvidos pela heurística de produto (camada 2)** — **0 clientes ganhariam classificação
nova**. A população que compra majoritariamente produtos de toxina botulínica/preenchedor já
é inteiramente capturada pelas 2 camadas existentes; a alta precisão medida acima é real, mas
não fecha nenhuma lacuna de cobertura que já não estivesse fechada. **Revertido** — não faz
sentido manter uma 3ª camada que nunca dispara. Fica só a documentação do achado.

**Conclusão final**: `PRODH`/`PRDHA` tem cobertura alta (92,4% do valor) mas isso não se
traduziu em ganho prático pra Linha de Negócio **no recorte testado** (fallback por
cliente-dominante, só AESTHETICS).

**As 2 linhas em aberto foram testadas e fechadas (2026-09-05, sessão de investigação sobre
"como tirar a dependência de `dim_estrutura`")** — ambas com resultado negativo, o que reforça
(não enfraquece) a conclusão acima:

- **(a) Nível de item, não cliente-dominante**: testado juntando `fct_faturamento_itens_sap`
  (cliente+produto) direto com `MARA.PRDHA`, restrito aos mesmos ~2.041 clientes com rótulo
  manual conhecido (97,3% de cobertura de valor nesse recorte). Resultado pior que o
  cliente-dominante, não melhor: o segmento `HEMO` (hemoderivados, poucos materiais mas de
  valor unitário altíssimo) domina o valor faturado **nas 3 categorias ao mesmo tempo**
  (32,7% em AESTHETICS, 76,5% em FARMA, 41,9% em ONCO/HEMATO) — um cliente de qualquer linha
  de negócio que compra hemoderivados puxa o item-dominante pra `HEMO` independente do seu
  perfil real, poluindo a classificação por item mais do que a por cliente-dominante (que já
  neutralizava isso ao olhar o perfil agregado do cliente).
- **(b) Nível 2/3 da hierarquia** (`ESPE.ANTI`, `ESPE.ANTT`, etc.): mesmo problema —
  `HEMO.HEMO`/`HEMO.ANTA` aparecem no topo das 3 categorias. **Refeito excluindo `HEMO`
  inteiramente** pra isolar o efeito: `AESTHETICS` melhora pra 31,2% `AEST` mas ainda não
  domina (`ANTI` 29,1%, `ESPE` 27,8% quase empatados); `FARMA` e `ONCO/HEMATO` ficam **ainda
  mais parecidos** entre si (`ESPE` 47,7% vs. 43,6%, respectivamente) — confirma que a
  hierarquia de produto simplesmente não capta a fronteira FARMA×ONCO/HEMATO, em nenhum nível,
  com ou sem o ruído de `HEMO`.

**Candidato adicional testado nesta mesma sessão**: `salesforce.User`/`dim_vendedor_sf`
tem campos `Divisao` (valores como "Onco"/"Aesthetics") e `Unidade_Negocio` (valores como
"1000 BLAU ESPECIALIDADES"/"2000 BLAU FARMA") que soam mais promissores que `regional`
(§4.11) por serem literalmente rotulados como negócio, não geografia — mas medidos ao vivo:
`Divisao` só está preenchido em **12 de 328 vendedores (3,7%)**, ainda mais esparso que a
faixa "~15-25%" já registrada em `CONTEXTO_VENDAS_SAP.md` §8.2 pra esse grupo de campos, e com
valores inconsistentes (duplicidade "Colombia"/"COlômbia", só 1 vendedor com "Aesthetics", só
1 com "BU Farma"). `Unidade_Negocio` está em 81/328 (24,7%) — na faixa já conhecida, não é
achado novo. Nenhum dos dois é viável como fonte primária.

**`dim_estrutura` continua sendo a fonte usada, sem substituto técnico encontrado** — agora
com bem mais tentativas esgotadas (cadastro SAP, `PRCTR`, `PRODH`/`PRDHA` em 4 recortes
diferentes, SAP HR, Salesforce em 3 campos diferentes). O teto é de dado/processo (nenhum
sistema captura essa segmentação estruturadamente), não de estratégia técnica de join —
reforça a recomendação de tratar isso como formalização de cadastro (proposta 3.10) em vez de
continuar procurando substituto.

### 4.4 `VBAP.PRCTR` (centro de lucro) — **revisado em 2026-09-05**: não é segmentação de negócio, é por planta/produto individual

Hipótese original (quando `CEPC`/`CEPCT` ainda não estavam expostas): os códigos `CLONCO`/
`CLESP`/`CLBIO`/`CLUR` pareciam autoexplicativos de segmento comercial. **Com `CEPC`/`CEPCT`
liberadas e consultadas ao vivo, a hipótese caiu por terra**:

- `CLUR` = **"Uruguay"** — é um centro de lucro por **país/filial** (bate com o
  `Codigo_Centro→Pais_Centro` já usado em `dim_centro_sap`, não é segmento de negócio).
  `CLONCO`/`CLESP`/`CLBIO` (os códigos com nome de segmento que motivaram a hipótese) **não
  têm registro em `CEPC`/`CEPCT`** — parecem centros de lucro órfãos/legados usados em
  transação sem cadastro mestre atual; não dá pra confiar neles sem confirmação funcional.
- A faixa `0000001xxx` é centro de lucro **por planta física** (`0000001000`="Matriz Predio
  100", `0000001001`="Fabrica Caucaia", `0000001003`="Fabrica Sao Paulo", `0000001006`=
  "Fábrica Goiás", `0000001900`="Deposito Etiopia", `0000002000`="Pharma Limirio",
  `0000002300`="FÁBRICA - PERNAMBUCO") — geografia/planta, não linha de negócio.
- A faixa `0000005xxx` é centro de lucro **por produto individual** (`0000005314`="ACETATO
  ABIRATERONA" — o mesmo material do achado de bug `PEINH` em §6.9!, `0000005306`=
  "IMATINIBE", `0000005316`="CARFILZOMIBE", `0000005346`="**BOTULIFT**" — bate com a BU
  "Botulift" citada em `vendas.fat_meta_equipe`, §8.3 de `CONTEXTO_VENDAS_SAP.md`, `0000005010`
  ="Comercial & Marketing", `0000005020`="P&D") — é granularidade de **margem por produto/
  função**, mais fina que Linha de Negócio, não um rollup dela.

**Conclusão corrigida**: `PRCTR` **não serve** como candidato de Linha de Negócio (motivo
diferente do suposto antes — não é "mais concentrado que `PRODH`", é **outra dimensão
inteiramente**: geografia de planta + rastreio de margem por produto específico). Achado que
sobrevive e é útil: **`PRCTR='0000005346'` identifica precisamente transações de "Botulift"**
sem depender do crosswalk de `fat_meta_equipe` — se alguém for reconciliar a BU "Botulift"
(que hoje só existe na meta, sem equivalente de faturamento real, §8.3) contra faturamento
real, este é o caminho técnico. Fora esse caso pontual, não seguir com `PRCTR` pra
segmentação de negócio.

### 4.5 `VBAP.ZZNUM_LICITACAO`/`ZZNUM_PREGAO`/`ZZDT_EMPENHO` — campos nativos de licitação pública (refina Canal MS/Público, §10 de `CONTEXTO_VENDAS_SAP.md`)

Achado direto: `VBAP` tem 3 campos customizados (`Z*`) específicos de venda pra governo —
número de licitação, número de pregão e data de empenho — preenchidos em **~39-40 mil itens
(~8,3% de 472.570)**. Hoje o Canal "MS"/"Publico" do Painel Vendas (§10.1 de
`CONTEXTO_VENDAS_SAP.md`) é inferido por `LIKE '%MINISTERIO DA SAUDE%'` no nome do cliente
(frágil a variação de grafia) e por nó de `dim_estrutura`. Esses 3 campos são um sinal
**direto do processo de venda** (é ou não é uma venda por licitação/pregão), não dependem de
nome de cliente nem de crosswalk manual.

**Proposta**: expor `Flag_Venda_Licitacao` (`ZZNUM_LICITACAO IS NOT NULL OR ZZNUM_PREGAO IS
NOT NULL`) em `fct_vendas_itens_sap`/`fct_pendencia_sap` como sinal complementar ao Canal
Público/MS já existente — não substitui (cobertura de ~8% é menor que o universo real de
"Publico", que inclui venda direta a estado/município sem licitação formal registrada nesses
campos), mas dá um segundo sinal, mais preciso onde existe, pra validar/cruzar contra a
classificação por nome de cliente. Esforço: baixo (campo já ingerido via `VBAP`). Dono: time
de dados.

### 4.6 Bloqueio de entrega/faturamento nativo (`VBAK.LIFSK`/`FAKSK`, `VBEP.LIFSP`) — achado negativo, mas reforça o achado do backlog zumbi

Investigado como possível substituto mais preciso pra "pedido travado"/backlog zumbi (regra
1.1.3, proposta 3.4): o SAP tem um mecanismo formal de bloqueio com motivo (`TVLST`, textos
reais confirmados em português: `Z1`="Pedido Cancelado", `Z2`="Cliente S/Limite Cred",
`Z6`="Pedido Antigo-Cancel", entre outros) — **mas é quase não usado na prática**:

- `VBAK.LIFSK` (bloqueio de entrega, header): 196.847 pedidos sem bloqueio, só 4 bloqueados.
- `VBAK.FAKSK` (bloqueio de faturamento, header): 196.772 sem bloqueio, 79 bloqueados.
- `VBEP.LIFSP` (bloqueio por linha de cronograma, mais granular): 482.926 linhas sem
  bloqueio, só **23 linhas bloqueadas** no total (4 delas com `Z6`="Pedido Antigo-Cancel" —
  vale inspecionar manualmente essas 4 como amostra).

**Conclusão**: não dá pra usar bloqueio nativo como sinal amplo do backlog zumbi — o time
comercial/logístico simplesmente não usa esse mecanismo formal do SAP pra marcar pedido morto
(reforça a leitura já registrada na proposta 3.4: é "lixo de dado" por abandono informal, não
por bloqueio formal não sincronizado). Não requer ação técnica; é argumento a mais, se
alguém propuser treinar o time a usar bloqueio formal em vez de deixar pedido velho parado.

### 4.7 `VBPA` — nenhuma outra função de parceiro serve de proxy pra vendedor (fecha a pergunta da 1.4.1/3.11)

Testado: as funções de parceiro (`PARVW`) realmente usadas em `VBPA` são `WE` (local de
entrega, 858.121), `AG` (solicitante, 761.120), `SP` (741.901), `RE` (pagador?, 490.376),
`RG` (490.314), `LF` (fornecedor, 190.914), `ZL` (51.987), `SB` (170) — **nenhuma é `VE`
(vendedor) nem parece um substituto plausível** (todas são papéis logísticos/comerciais do
documento, não pessoa responsável pela venda). Achado negativo, mas fecha em definitivo a
pergunta da proposta 3.11 do lado técnico: não há dado de vendedor escondido em outra
função de parceiro nesta base — a resposta só pode vir do time funcional SAP confirmando se
`VE` foi mesmo abandonado/nunca configurado.

### 4.9 As 5 tabelas antes não expostas — liberadas e testadas ao vivo em 2026-09-05

As 5 tabelas listadas na primeira versão desta seção (`MVKE`, `T001K`, `T003T`, `TVLP`,
`CEPC`/`CEPCT`) foram adicionadas à view do Datasphere/HANA a pedido do usuário e testadas de
novo no mesmo dia. Confirma que era mesmo um problema de escopo de exposição, não de
permissão/nome — 4 das 5 vieram com dado real e útil:

**`T001K` — achado forte, fecha o gap de moeda por centro do §6.9(2)/regra 1.3.2.** O
de-para formal `BWKEY→BUKRS` que a investigação de câmbio (§2.1) registrou como "não
replicado, mapeamento usa heurística `Pais_Centro`" **agora existe e bate
exatamente com a heurística nos centros já conhecidos** (BR: 1000-1900/2200/2300/2350/R100;
Uruguai (`UR01`, UYU): 2000/2100/2400/2500/2600/2700; Colômbia (`CO10`, COP): `CO10`) — mas
revela **2 empresas do grupo não documentadas antes**: `BG01`/`BG02` ("Bergamo
Farmacêutica", BRL) e `3000`/`IICT` ("ICT - Ins.Cie.Tec. e Inov.", BRL), ambas em real
(confirmado via join `T001K→T001.WAERS`). **Proposta**: usar `T001K→T001` como fonte oficial
de moeda por centro na implementação de `TCURR` (§2.1), em vez de (ou como validação de)
`Codigo_Centro→Pais_Centro`; e confirmar com o financeiro se `Bergamo`/`ICT` já aparecem
corretamente nos totais "BR" hoje (são BRL, então o filtro por moeda não quebra, mas o
`Pais_Centro` desses centros em `dim_centro_sap` nunca foi conferido nominalmente). Esforço:
baixo (join novo, tabela já com dado). Dono: time de dados.

**`T003T` — confirmado com dado real, resolve a proposta 3.8(a) sem bloqueio.** Textos
oficiais de tipo de documento contábil em português (`AB`="Documento contábil",
`DG`="Crédito de cliente", `DR`="Fatura cliente", `DZ`="Pagamento cliente", etc.) — pode
entrar em `fct_credito_devolucoes_sap.Tipo_Documento_Contabil` já na próxima rodada, sem
depender de decisão de exposição. Ver 3.8 atualizado.

**`CEPC`/`CEPCT` — confirmado com dado real**, mas **muda a conclusão da §4.4** (ver acima):
não é segmentação de negócio, é planta física + produto individual. Achado colateral útil:
confirma 2 empresas (`BG01`, `3000`) que também apareceram em `T001K`.

**`MVKE` — tabela existe (80.326 linhas, bate com o total de materiais), mas as colunas de
classificação não servem**: `SPART` não existe nesta réplica de `MVKE` (é campo de `MARA`/
`VBAK`, não de `MVKE`, correção de uma suposição errada minha) e `MVGR1` existe mas está
**100% vazia** nas 80.326 linhas — mesmo padrão de campo-cadastrado-mas-não-preenchido já
visto em `KVGR1-5`/`VKBUR`/`VKGRP` (§8.2 de `CONTEXTO_VENDAS_SAP.md`). **Descarta `MVKE` como
candidato** pra qualquer segmentação — achado negativo, mas fecha a pergunta.

**`TVLP` — tabela existe, mas não é a tabela certa pra texto de `LPRIO`.** Os campos reais de
`TVLP` (`PSTYV`, `MATN0`, `SPEMT`, `MATPR`, `BWART`...) são de **estratégia de determinação de
lote** (batch management), sem nenhuma relação com prioridade de entrega — suposição errada
minha ao nomear a tabela em §4.2. A tabela certa pro texto de `LPRIO` **ainda não foi
identificada**; como `DD04L` (que resolveria o domínio de qualquer campo) também não está
exposta nesta conexão, o caminho mais rápido agora é perguntar direto ao time funcional SAP
o texto de `00`/`01`/`02` de `LPRIO`, em vez de continuar tentando adivinhar o nome da tabela.

### 4.10 `HRP1000`/`HRP1001`/`PA0001` (Organizational Management de RH) — candidato forte pra substituir a hierarquia comercial manual (Divisional/Regional/Distrital/Setor)

Investigado a partir de uma pergunta direta do usuário: já que `dim_estrutura` (§1.5.2/§8.2
de `CONTEXTO_VENDAS_SAP.md`) é um organograma SharePoint mantido manualmente, por gerente,
incompleto e desigual entre linhas de negócio — não dava pra trazer isso das tabelas de RH do
próprio SAP? As tabelas de Organizational Management (`HRP1000`=objetos, `HRP1001`=
relacionamentos, `PA0001`=lotação do funcionário) **já são ingeridas** (grupo `hr_daily_h10`,
`CONTEXTO_VENDAS_SAP.md` §5), só nunca usadas fora do escopo de RH.

**Achado forte**: `HRP1000` (919 unidades organizacionais, tipo `O`) tem uma estrutura
**real e ativamente mantida** (histórico versionado por `BEGDA`/`ENDDA`, última mudança em
2025) com nomes que batem com a geografia comercial já conhecida — sob `Comercial` →
`Força De Vendas`:

| Unidade org (`HRP1000.STEXT`) |
|---|
| Regional Nordeste |
| Regional Rj/Mg/Es |
| Regional Rs |
| Regional Sp/Go/Df |
| Regional Spi |
| Vendas Regional Ne/No / Se/Co / Sp/Sul (braço "Selling Hospital", separado) |
| Administracao De Vendas |

O braço **"Selling Hospital"** é um achado à parte interessante: é uma estrutura comercial
paralela (provavelmente o canal hospitalar/público) que **não tem equivalente identificado
em `dim_estrutura`/Canal hoje** — vale investigar se explica parte do Canal "Publico"/"MS"
(§1.5.1) de um jeito mais estruturado que o `LIKE` em nome de cliente.

**`PA0001` confirma que a estrutura está viva**: linhas com `BEGDA` de agosto/setembro de
2026 (contratações recentes), ligando `PERNR` (funcionário) → `ORGEH` (unidade organizacional)
→ `KOSTL`/`BUKRS` — inclusive revela que `IICT` (a empresa "ICT" achada via `T001K`, §4.9) tem
funcionários próprios lotados nela, confirmando que é uma operação real, não só um código de
plano de contas.

**Ressalva que segue de pé**: não achei unidade com o nome exato "Distrital"/"Divisional"/
"Setor" nesta investigação (só "Regional") — pode existir em nível mais fundo da árvore
(abaixo de cada Regional) que não foi percorrido até o fim, ou a granularidade fina do RH
pode parar em "Regional" mesmo. **O elo Vendedor(Salesforce)→`PERNR` não precisou ser
testado** — achado da §4.11 abaixo (com VPN/SQL Server liberados na mesma sessão) mostrou que
existe um caminho mais direto, sem depender do RH.

### 4.11 `salesforce.User.regional` — o mesmo território já existe no cadastro do vendedor, sem precisar de elo com o SAP HR (**medido com dado real, 2026-09-05**)

Com o SQL Server liberado na mesma investigação, testei direto: `SILVER.salesforce."User"`
(cadastro do vendedor, já usado hoje como fallback de `Codigo_Vendedor`, §1.4.1) tem um campo
`regional` **preenchido nas 588 linhas** (100%), com valores que batem com a nomenclatura do
SAP HR (§4.10): `Matriz`, `Farma`, `RJ/MG`, `NO/NE`, `SPC`, `RS`, `SPI`. Isso elimina a
necessidade do elo com `PERNR` — a informação já está no próprio cadastro do vendedor,
sem precisar cruzar com RH.

**Mas a cobertura real sobre transação, medida por completo, é modesta** — join corrigido
contra `fct_faturamento_itens_sap` (o `Codigo_Vendedor` do Gold é o ID Salesforce em 15
caracteres, `salesforce.User.id` vem em 18 — comparar por `LEFT(id,15)`, achado técnico à
parte que vale documentar pra quem for reusar isso):

| `regional` do vendedor | Itens de fatura | % do total (449.618) |
|---|---|---|
| `Matriz` (não é território de campo) | 242.574 | 54,0% |
| *(sem vendedor/match, ~17% do total sem Salesforce)* | 77.546 | 17,3% |
| `Farma` (é linha de negócio, não geografia) | 50.860 | 11,3% |
| `NO/NE` | 23.925 | 5,3% |
| `SPC` | 18.975 | 4,2% |
| `RS` | 16.087 | 3,6% |
| `RJ/MG` | 14.463 | 3,2% |
| `SPI` | 5.188 | 1,2% |

**Leitura honesta**: só ~17,5% dos itens de faturamento têm um vendedor com território
geográfico real e útil (`NO/NE`+`SPC`+`RS`+`RJ/MG`+`SPI`) — a maioria (54%) cai em `Matriz`
(provavelmente conta nacional/e-commerce/distribuidor ou só o padrão do cadastro pra quem não
é vendedor de campo), e ~17% não tem vendedor identificado (mesmo teto de ~73-83% de
cobertura de vendedor já conhecido, §1.4.1). **Cobertura pior que o crosswalk manual atual
(52-87%)** — não é um substituto de `dim_estrutura`, mas é um sinal **estruturado, sem
manutenção manual, e independente** (vem do cadastro do vendedor, não de planilha) que vale
cruzar contra a hierarquia atual: onde os dois concordam, aumenta a confiança; onde só um
tem dado, complementa a cobertura. `Farma`, embora não seja geografia, é um sinal a mais pra
Linha de Negócio (§1.5.2/§4.3), na mesma linha de `PRODH`.

**Proposta**: (a) expor `regional`/`department`/`unidade_de_negocio` de `salesforce.User`
como colunas de `dim_vendedor_sf` (parte já existe conceitualmente, ver `CONTEXTO_VENDAS_SAP.md`
§8.2, que mediu ~15-25% de cobertura pra esses outros campos — `regional` é bem melhor que
isso quando o vendedor tem match, mas o teto real por transação, medido agora, é os ~17,5%
acima); (b) não seguir a linha de investigação do SAP HR (`HRP1000`/`PA0001`, §4.10) como
prioridade pra esse fim específico — o Salesforce já responde a mesma pergunta sem precisar
de um elo novo, mesmo com cobertura parecida/discretamente pior. Esforço: baixo (campo já
existe e é acessível, é só expor). Dono: time de dados.

### 4.12 Varredura do DDIC (2026-09-05) por mais um candidato nativo pra Linha de Negócio — achado negativo (`SPART`), e a varredura ampla por palavra-chave não é viável

Sessão adicional de investigação (motivada pela pergunta "dá pra tirar a dependência de
`dim_estrutura`?"): tentativa de busca ampla em `DD02T` (descrição de tabelas) por palavras-chave
em português (`segmento`, `divisão`, `ramo`, `negócio`, `categoria`, `classificação`, `grupo de
mercadoria`, `setor`, `marca`, `hierarquia`) — **inviável como método**: `DD02T` é o dicionário
inteiro do SAP standard (centenas de milhares de tabelas de todos os módulos e add-ons de
terceiros — `/BI0/`, `/FHG/`, `/BEV1/`, `/ISDFPS/`, IDoc, etc.), então qualquer palavra genérica
retorna milhares de resultados de módulos completamente irrelevantes (RH, BI, fiscal,
logística de bebidas) sem relação com esta configuração SD. Não vale repetir essa técnica —
funciona só quando já se sabe o nome exato da tabela (uso normal de `scripts/ddic_lookup.py`),
não pra descoberta exploratória.

**Achado concreto, via inspeção direta de campo já ingerido**: `VBAP.SPART`/`MARA.SPART`/
`KNVV.SPART` ("Divisão", campo clássico de SD) está bem preenchido, com poucos valores
distintos (`10`/`20`/`30`/`40` em `VBAP`, ~445k/27k/668 linhas respectivamente — fortemente
concentrado em `10`). Texto oficial confirmado via `TSPAT` (`SPRAS='P'`, tabela pequena, já
citada no grupo de ingestão `sd_master_daily_h11` de `CONTEXTO_VENDAS_SAP.md` §5):

| `SPART` | Texto oficial (`TSPAT`) |
|---|---|
| `10` | Medicamentos |
| `20` | Matéria-Prima |
| `30` | Outros |
| `40` | Dispositivos médicos |
| `50` | Venda Serviços |

**Descartado como candidato**: é uma classificação por **tipo de produto** (remédio vs.
matéria-prima vs. dispositivo vs. serviço), não por área terapêutica/comercial — não tem
relação com AESTHETICS/FARMA/ONCO-HEMATO, e além disso está extremamente concentrado num só
valor (94% dos itens de pedido são `SPART='10'`), o que já inviabilizaria discriminação mesmo
que o eixo fosse o certo. Mesmo padrão de achado negativo dos outros campos de cadastro SAP já
testados (§8.2 de `CONTEXTO_VENDAS_SAP.md`, §4.4, §4.9) — o campo existe e está preenchido,
mas mede outra coisa.

**Conclusão desta rodada**: nenhum caminho novo de descoberta (DDIC por palavra-chave) nem
candidato novo (`SPART`) mudou a conclusão já registrada em §4.3/§4.9/§4.11 — `dim_estrutura`
segue sem substituto técnico, e a lista de "coisas já tentadas e descartadas" cresceu mais uma
vez em vez de encontrar uma saída.

### 4.13 `GOLD.rh.fct_funcionario` — tabela de RH não documentada até aqui, com precisão alta pra Linha de Negócio mas SEM ganho de cobertura (achado misto, 2026-09-05)

Descoberta ao inspecionar schemas do `GOLD` direto no SQL Server (pedido do usuário): existe
uma tabela **`rh.fct_funcionario`** (6.249 funcionários, ~60 colunas) **nunca citada em
nenhum documento deste projeto** — não é a mesma coisa que `HRP1000`/`PA0001` (SAP HR bruto,
via HANA, §4.10): é um fato de RH já modelado, com organograma **em texto navegável**
(`arvore_organizacional`, ex.: `"CONSELHO DE ADMINISTRAÇÃO > Presidencia > Comercial >
Unidades De Negocios > Aesthetics"`) e níveis numerados (`nome_nivel_2`...`nome_nivel_8`),
cargo, gestor, e-mail, usuário SAP, status (ativo/saiu da empresa) — mais rico que qualquer
fonte de RH testada antes neste projeto.

**Validado no `data-platform`** (`airflow/dags/dbt/models/gold/rh/fct_funcionario.sql`/`.yml`,
dono documentado "Dados e Analytics"): é o **mesmo SAP HCM** já citado em §4.10
(`PA0000`/`PA0001`/`PA0002`/`PA0105`/`HRP1000`/`HRP1001`, via `silver.dataspherev2.*`, já
ingeridos), só que modelado de verdade em vez de consulta ad hoc — resolve o gestor via
posição (`HRP1001` relação `008`/`002`), sobe a cadeia organizacional por até 8 níveis via
`LEFT JOIN` sucessivo em `HRP1001`, e enriquece com usuário SAP/e-mail (`PA0105`,
`USRTY 0001`/`0010`). Materializado como `table` (full refresh), sempre com `WHERE hoje_sap
BETWEEN BEGDA AND ENDDA` — é um **snapshot do dia da execução**, não histórico; o `.yml`
avisa que `MANDT` não está no SELECT final (só um mandante nesta base, mas vale checar se
usar como chave de teste `dbt`). Confirma que o achado é sobre dado real e mantido, não um
artefato de consulta.

**Achado forte**: sob `Comercial`, existem **dois ramos organizacionais separados**:
`Força De Vendas` (regional — `Regional Nordeste`/`Rs`/`Sp/Go/Df`, o mesmo achado da §4.10) e
**`Unidades De Negocios`**, com filhos literais **`Aesthetics`**, **`Farma`** e
**`ESPECIALIDADES`** (nome usado internamente pro braço ONCO/HEMATO — confirmado pelos cargos:
"Gerente Distrital/Regional/Divisional/Produtos - Onco"). 89 funcionários ativos nesse ramo,
100% com `email`/`usuario_sap` preenchidos — deu pra casar 73 (82%) com
`SILVER.salesforce."User"` por e-mail, e daí com `Codigo_Vendedor` de
`fct_faturamento_itens_sap` (mesmo truque `LEFT(id,15)` da §4.11).

**Precisão medida (cruzando contra os mesmos ~2.041 clientes com rótulo manual conhecido)** —
a melhor de todos os candidatos testados até aqui, inclusive nos 2 eixos que nunca
discriminaram bem (FARMA e ONCO/HEMATO):

| `Unidade_Negocio` (RH, via vendedor) | Rótulo manual dominante | Precisão |
|---|---|---|
| `Aesthetics` | AESTHETICS | **93,8%** |
| `ESPECIALIDADES` | ONCO / HEMATO | **83,0%** |
| `Farma` | FARMA | **82,2%** |

Pra comparação: o melhor resultado anterior pra FARMA era ~45,6% e pra ONCO/HEMATO ~64%
(heurística por produto, §4.3/§8.2) — este sinal bate os dois por margem grande.

**Mas a cobertura não ajuda em nada**: dos R$1,51bi atribuíveis a esses 73 vendedores,
**99,6% (R$1,50bi) já pertence a clientes que já tinham rótulo manual** — só **R$5,5 milhões
(0,4%)** é cobertura genuinamente nova. Mesmo padrão de resultado nulo já visto com a 3ª
camada de `PRDHA` em §4.3 (implementada e revertida por não ganhar cliente novo): os
funcionários de `Unidades De Negocios` são papéis sênior/especializados (Gerente
Distrital/Regional/Divisional/Produtos, Representante Vendas Interno) que cuidam de contas
específicas **já conhecidas**, não o grosso da força de vendas de campo (`Propagandista
Vendedor Sr/Pl`, 36 pessoas ativas, sem nenhum sufixo de unidade no cargo) — que continua
organizada só geograficamente (`Força De Vendas > Regional X`), sem tag de unidade de negócio
em lugar nenhum do cadastro de RH.

**Achado colateral que sobrevive e tem valor próprio — divergência como sinal de QA**: nos
R$1,50bi onde as duas fontes (RH-via-vendedor e `dim_estrutura`-manual) coexistem, elas
**discordam em 16,5% do valor (R$247,9 milhões)** — maior bloco: **R$131,9 milhões** de
clientes rotulados `AESTHETICS` no manual, mas vendidos majoritariamente por vendedor tagueado
`ESPECIALIDADES` (Onco/Hemato) no RH. Não dá pra saber sem investigar mais se é
cross-sell legítimo (um vendedor de Onco vendendo pontualmente pra conta de Aesthetics) ou
sinal de que o rótulo manual desses clientes está desatualizado — mas é um sinal **novo e
independente** (vem de RH+Salesforce, não de SharePoint) que dá pra usar como checagem
cruzada em vez de fonte primária.

**Conclusão**: mais um candidato que teria alta precisão mas fecha zero de lacuna de
cobertura — reforça (pela 3ª vez, contando `PRDHA` em §4.3 e agora este) que o problema real
não é "achar o campo certo com boa correlação", é que nenhum sistema (SAP, Salesforce, RH)
organiza o *grosso* do time comercial por linha de negócio — só uma fatia pequena e sênior.

**Implementado (2026-09-05)**: `scripts/query_vendas_sap.py::auditoria_linha_negocio_rh_vs_estrutura`
formaliza esse cruzamento como checagem reutilizável (janela de 12 meses por padrão, não o
histórico completo usado na investigação — R$57,7mi em desacordo nos últimos 12 meses vs.
R$247,9mi no histórico todo) — exposta em `pages/2_Auditoria.py` como a 5ª checagem
(`linha_negocio_rh_vs_estrutura`), ao lado das 4 de `audit_pendencia_flow.py`. Retorna 1 linha
por cliente em desacordo (`Codigo_Cliente`, `Nome_Cliente`, `Linha_Negocio_Manual`,
`Unidade_Negocio_RH`, `Linha_Negocio_Esperada_RH`, `Valor_Faturado`, `Nome_Vendedor`,
`Nome_Cargo_Vendedor`), ordenado por valor — pronta pro time comercial revisar os casos de
maior impacto primeiro. **Dono**: time comercial (decidir se cada divergência é cross-sell
legítimo ou rótulo desatualizado em `dim_estrutura`).

## 5. Investigação (2026-09-08): regras de negócio do schema `vendas` (legado) que `vendas_sap` ainda não implementa

Motivada por uma pergunta direta: já que `vendas_sap` vai substituir `vendas` no médio prazo,
existe alguma regra de negócio real embutida nos models legados (`GOLD.vendas.*`) que não
tem equivalente em `vendas_sap` hoje? Metodologia: lidos os `.sql` de todos os 15 models de
`airflow/dags/dbt/models/gold/vendas/` no checkout local do `data-platform` e comparados
linha a linha com os `.sql` dos models `vendas_sap` equivalentes (não só os `.yml`/docs, que já
tinham sido lidos em investigações anteriores). Achados novos, do mais para o menos impactante:

### 5.1 Régua de `Prioridade_Pedido` completa existe — resolve o "gap de decisão de negócio" da regra 1.1.4/§6.3

> **Atualização (2026-09-08): implementado.** A decisão inicial foi "não priorizar por
> enquanto", revertida na mesma sessão depois de confirmar por que os depósitos "Carimbado"
> não são um Centro só, e sim uma convenção repetida por planta (ver §5.9 abaixo pra medição
> completa por Centro). Implementação em `feature/prioridade-pedido-carimbado-sap`
> (`data-platform`), ainda não mergeada/deployada — ver §5.9 pro detalhe técnico e status.

**O maior achado desta investigação.** As regras 1.1.4/3.6/4.2 registravam isso como uma
pergunta em aberto pro time de negócio ("a escala fina de prioridade ainda é necessária?").
Não é — ela já existe, funcionando, no model legado `dim_pendencia.sql`:

```sql
CASE
    WHEN VBAK.vkorg = '2000'             THEN 1  -- BLAU FARMA
    WHEN VBAK.auart = 'ZVCO'             THEN 2
    WHEN VBAK.auart IN ('ZGOV', 'ZVLC')  THEN 3
    WHEN VBAK.vkorg IN ('1000', '4000')  THEN 4  -- BLAU HOSPITALAR / BÉRGAMO
    WHEN VBAK.vkorg = '3000'             THEN 5  -- BLAU DERMO
    ELSE 99
END AS prioridade_pedido
```

`fct_pendencia_sap.sql` (vendas_sap), em contraste, só tem `CASE WHEN Tipo_Ordem_Venda='ZVCO'
THEN 1 ELSE 9 END` — ou seja, a migração manteve o nível 2 da régua (`ZVCO`) mas descartou os
níveis 1, 3, 4 e 5 inteiros, e ainda reclassificou o nível 2 original como "1" (mudando o
significado da prioridade mais alta: no legado é `VKORG='2000'`, não `ZVCO`).

**Medido ao vivo hoje (HANA, `IB_SAPECC`, 2026-09-08) — a régua não é coisa do passado**:

| Critério (nível da régua legada) | Valor real hoje |
|---|---|
| `VBAK.VKORG='2000'` (nível 1 — BLAU FARMA, texto via `TVKOT`) | **22.029** pedidos (cabeçalho) |
| `VBAK.AUART='ZVCO'` (nível 2) | 4.401 pedidos |
| `VBAK.AUART='ZGOV'` (nível 3) | **24.393** pedidos |
| `VBAK.AUART='ZVLC'` (nível 3) | 1.729 pedidos |
| `VBAK.VKORG IN ('1000','4000')` (nível 4 — BLAU HOSPITALAR/BÉRGAMO) | 105.696 + 16.705 pedidos |
| `VBAK.VKORG='3000'` (nível 5 — BLAU DERMO) | 8.046 pedidos |

Não é um código morto de um AUART/VKORG descontinuado — `ZGOV` sozinho (provavelmente "venda
Governo", coerente com os campos `ZZNUM_LICITACAO`/`ZZNUM_PREGAO` já achados na regra 4.5) é
maior em volume que `ZVCO`, e `VKORG='2000'` (BLAU FARMA) é ~11% de todos os pedidos.
Consequência prática: hoje, em `vendas_sap`, um pedido `ZGOV`/`ZVLC` ou de `VKORG='2000'`
concorre por estoque em pé de igualdade com qualquer pedido comum (prioridade "9") — a régua
de negócio que existia no legado para dar precedência a Farma/Governo/Licitação na alocação
de estoque simplesmente não é aplicada mais.

**Ligado ao estoque "Carimbado" (regra 5.2 abaixo)**: no legado, a régua de prioridade não é
só ordem de fila — pedidos de prioridade 2/3 (`ZVCO`/`ZGOV`/`ZVLC`) têm acesso a uma bolsa de
estoque adicional ("Carimbado") que pedidos de prioridade 1/4/5 não têm. Ou seja, o *status*
`CARIMBAGEM` em `fct_pendencia_status_sap` (que hoje nunca ocorre, regra 1.1.4) não é um
enum solto — no legado ele corresponde a um mecanismo real de reserva de estoque por tipo de
depósito, não só por ordenação de fila.

**Confirmado em produção com VPN (2026-09-08)**: `GOLD.vendas_sap.fct_pendencia_sap` tem
exatamente `Prioridade_Pedido` = 1 (5.226 linhas) ou 9 (419.022 linhas), nunca outro valor;
`fct_pendencia_status_sap.Status_Alocacao_Virtual` só assume `SEM ESTOQUE` (41.698) ou
`EM REMESSA` (4.503) — `CARIMBAGEM` e `EXC_COMERCIAL` têm **0 linhas** hoje. Confirma que a
régua realmente não está em uso, não é só uma leitura pontual do código.

**Proposta**: portar a régua completa (6 níveis, valores de `VKORG`/`AUART` acima) pra
`fct_pendencia_sap.sql`, substituindo o atual `CASE ZVCO→1 ELSE 9`. Reativa o status
`CARIMBAGEM` em `fct_pendencia_status_sap` com semântica real. **Esforço**: baixo (mudança
de uma expressão `CASE`, mesmos campos já ingeridos — `VBAK.VKORG`/`VBAK.AUART`). **Risco**:
médio — muda a ordem de alocação virtual de estoque pra ~34% dos pedidos (soma dos níveis
1/3 acima) e reativa um status hoje inexistente; qualquer dashboard/relatório que filtre
`Prioridade_Pedido=9` como "tudo" quebraria silenciosamente. Validar com o time comercial se
a régua de 2026 ainda reflete a prioridade real do negócio antes de portar (a régua no
legado pode já estar desatualizada — não foi possível confirmar a data de criação do model
neste checkout). **Dono**: time de dados (implementação) + time comercial (confirmar a régua
ainda vale).

### 5.2 Estoque "Carimbado" (depósitos reservados) não existe em `fct_estoque_lote_sap`

O legado classifica estoque por código de depósito (`LGORT`), não só por status SAP nativo:

```sql
CASE
    WHEN lgort_deposito IN ('1050', '4000') THEN 'Livre'
    WHEN lgort_deposito IN ('1080', '1190') THEN 'Carimbado'
    ELSE 'Outros'
END AS tipo_estoque
```

E a alocação em `dim_pendencia.sql`/`dim_pendencia_status.sql` trata os dois tipos de forma
desigual: pedidos de prioridade 1/4/5 só enxergam estoque "Livre"; pedidos de prioridade 2/3
(`ZVCO`/`ZGOV`/`ZVLC`, ver 7.1) enxergam "Livre" **+** "Carimbado" somados. Isto é, os
depósitos `1080`/`1190` funcionam como uma reserva exclusiva para os pedidos de maior
prioridade — não é estoque "a mais", é estoque **segregado por elegibilidade de pedido**.

`fct_estoque_lote_sap.sql` não faz nenhuma distinção por `LGORT` — todo `MCHB.CLABS` (estoque
avaliado de utilização livre) de qualquer depósito entra como `Qtd_Estoque_Livre`/
`Qtd_Disponivel_Venda` igualmente, disponível pra qualquer pedido na simulação FIFO de
`fct_pendencia_status_sap`.

**Medido ao vivo (HANA, 2026-09-08)**: os depósitos "Carimbado" têm estoque real hoje, não
residual — `LGORT='1080'`: 20.752 unidades livres (6.643 linhas de lote); `LGORT='1190'`: 265
unidades (3.262 linhas, quantidade pequena por lote mas com giro). Não dá pra confirmar sem o
time de operações/SAP se `1080`/`1190` são fisicamente depósitos "carimbados" (reservados/
alocados) em todos os centros ou só nos que a amostra pegou — os códigos de depósito podem
não ser universais entre centros (`WERKS` não foi cruzado nesta consulta).

**Proposta**: (a) confirmar com o time funcional SAP se `1080`/`1190` ainda representam
"depósito carimbado" reservado — os códigos podem ter mudado desde que o legado foi escrito;
(b) se confirmado, adicionar um campo `Flag_Estoque_Reservado`/`Tipo_Estoque_Deposito` em
`fct_estoque_lote_sap` (derivado de `MCHB.LGORT`) e usar isso em `fct_pendencia_status_sap`
pra replicar a elegibilidade diferenciada por prioridade — sem isso, a régua da 7.1, mesmo
que portada, ficaria incompleta (prioriza a *fila*, mas não segrega o *estoque*). **Esforço**:
médio (novo campo + mudança na CTE de FIFO de `fct_pendencia_status_sap` pra considerar 2
pools de estoque em vez de 1). **Dono**: time de dados + time de operações/SAP (confirmar
significado atual dos códigos de depósito).

### 5.3 Reclassificação manual de Organização de Vendas em `fat_faturamento` — sem equivalente em `fct_faturamento_itens_sap`

`fat_faturamento.sql` (legado) tem uma cadeia de `CASE` que **reatribui** a Organização de
Vendas de pedidos específicos antes de qualquer agregação — não é enriquecimento, é correção
de rateio comercial:

```sql
WHEN nr_pedido = '00108884' THEN '1000'                              -- Botulim p/ Easy Farma, alocado em Org 1000
WHEN nr_pedido = '00108885' THEN '1000'
WHEN nr_pedido = '00112511' THEN '3000'                               -- venda Org 4000/1000 realocada p/ Org 3000
WHEN nr_pedido IN ('00118322','00118321','00118327') THEN '2000'      -- venda Org 4000/1000 realocada p/ Org 2000
WHEN CONCAT(cod_cliente, cod_produto, organizacao_de_venda)
     = '3000103PA37261000' THEN '3000'                                -- Botulift p/ MS, jun/24
...
WHEN organizacao_de_venda_original = '4000' THEN '1000'                -- regra geral: toda venda originada em Org 4000 (BÉRGAMO) rateia para Org 1000 (BLAU HOSPITALAR)
```

A regra geral (`4000→1000`) é a que mais importa: significa que, comercialmente, **todo o
faturamento de BÉRGAMO (Org 4000) é reportado sob BLAU HOSPITALAR (Org 1000)** no painel
legado — não é erro, é decisão de rateio de negócio (provavelmente BÉRGAMO fatura mas o
resultado é atribuído à unidade comercial que "vendeu"). `fct_faturamento_itens_sap.sql` usa
`VBAK.VKORG` puro (`Codigo_Org_Vendas`), sem nenhuma realocação — o faturamento de Org 4000
aparece como Org 4000.

**Por que importa**: qualquer comparação de faturamento por Organização de Vendas entre os
dois schemas (ou entre `vendas_sap` e um relatório comercial que ainda espere a visão
realocada) vai divergir sistematicamente nos ~16.705 pedidos de Org 4000 (contagem de
cabeçalho VBAK medida em 7.1) — o valor total bate, mas a quebra por Org Vendas, não.
`scripts/query_vendas_sap.py::faturamento_por_org_vendas_linha_negocio` (`invest_sap`) herda
esse gap, pois usa `Codigo_Org_Vendas` de `vendas_sap` sem qualquer realocação.

**Proposta**: perguntar ao time comercial/financeiro se o rateio `4000→1000` (e os 4-5
pedidos individuais hardcoded) ainda é a visão correta de reporte hoje. Se sim, formalizar
como um `dim_org_vendas_ajustada` ou campo derivado em `dim_centro_sap`/model novo, em vez de
repetir `CASE nr_pedido IN (...)` hardcoded a cada consumidor — o formato atual (pedidos
individuais no código) não escala e fica invisível pra quem não leu o SQL legado. **Esforço**:
baixo pra portar a regra geral (`4000→1000`), mas os pedidos hardcoded individuais precisam de
decisão caso a caso (não generalizam). **Dono**: time comercial/financeiro (confirmar regra
vigente) + time de dados (implementação).

### 5.4 `ajuste_tributario` — correção de valor líquido de faturamento sem equivalente em `vendas_sap`

> **Atualização do usuário (2026-09-08): não priorizar.** A tabela `ajuste_tributario` é uma
> correção manual enviada pelo Financeiro pra compensar uma alíquota que estava incorreta na
> origem — **a alíquota já foi corrigida na fonte a partir de agora**, então o volume de novos
> ajustes tende a cair/zerar organicamente daqui pra frente (as 386 linhas/R$646,7mil medidas
> em 2026-09-08 são resíduo do período com alíquota errada, não um fluxo permanente). Não faz
> sentido portar esse join pra `fct_faturamento_itens_sap` como um mecanismo contínuo — manter
> como está (lido do schema `vendas` legado enquanto o resíduo existir) e não tratar como gap
> a corrigir no pipeline novo.

`fat_faturamento.sql` faz um `LEFT JOIN` final contra `GOLD.vendas.ajuste_tributario` e, quando
há match, **substitui** `faturamento_liquido` pelo valor corrigido:

```sql
-- Ajuste tributário: implementado em 2026-07-20, a pedido do time de Vendas e Financeiro,
-- corrige faturamento líquido que não reflete corretamente o SAP após ajuste tributário.
COALESCE(
    TRY_CAST(REPLACE(REPLACE(aju.soma_de_valor_liquido_correto, '.', ''), ',', '.') AS DECIMAL(18,2)),
    fat_final.faturamento_liquido
) AS valor_liquido_final
```

Não é um achado antigo/obsoleto — a implementação é de **2026-07-20**, recente, e existe uma
tabela `GOLD.vendas.ajuste_tributario` alimentada ativamente pelo time de Vendas/Financeiro.
`fct_faturamento_itens_sap.sql` não tem nenhum join equivalente — `Valor_Liquido_Faturamento`
é sempre `VBRP`/`VBRK` puro do SAP, sem qualquer correção tributária manual.

**Por que importa mais que os outros itens desta seção**: este é o único achado da lista que
altera diretamente **o número de receita reportado** (não uma dimensão/quebra, o valor em R$
em si) — se essa tabela de ajuste continua recebendo lançamentos, todo o faturamento
reportado via `vendas_sap` (inclusive `scripts/query_vendas_sap.py::faturamento_mensal` e as
páginas de Painel Vendas/Faturamento do `invest_sap`) está sistematicamente sem essas
correções, por menor que seja o volume.

**Não foi possível medir o volume/vigência atual** (SQL Server BRONZE/SILVER/GOLD
indisponível nesta sessão — precisa de VPN da empresa; só o HANA/Datasphere foi acessível
aqui). **Próximo passo, antes de priorizar**: com VPN disponível, checar
`SELECT COUNT(*), MAX(data_do_ajuste_ou_equivalente) FROM GOLD.vendas.ajuste_tributario`
(nome exato de coluna de data a confirmar no schema) — se a tabela parou de receber
lançamento há muito tempo, o gap é irrelevante; se está ativa, é candidato a prioridade alta
(replicar o mesmo padrão de join em `fct_faturamento_itens_sap`, ou expor os ajustes como
model próprio em `vendas_sap` pra não depender do schema legado). **Dono**: time de dados
(quantificar + decidir formato) + time Financeiro (confirmar se o processo de ajuste
tributário continua vivo).

### 5.5 Regra "produto contém 'MS'" para Setor Ministério da Saúde — mais ampla que a lógica de Canal MS atual

Além do de-para de cliente (`cod_cliente='3000103'` → nós `70{1,2,3}000000` conforme a Org
Vendas, já documentado na regra 1.5.1/§10.1), `fat_faturamento.sql` tem uma 2ª regra,
independente de cliente:

```sql
WHEN fat.descricao_produto LIKE '%MS%' THEN '701000000'
```

Ou seja: **qualquer produto cuja descrição contenha "MS"** (não só vendas pro cliente
Ministério da Saúde) é classificado no legado como Setor MS/ONCO-HEMATO. A lógica de Canal MS
hoje em `scripts/query_faturamento_comercial.py` (`invest_sap`, regra documentada em
`CONTEXTO_VENDAS_SAP.md` §10.1) só olha `Nome_Cliente LIKE '%MINISTERIO DA SAUDE%'` — não
reproduz esta 2ª via por produto.

**Ressalva importante, não é um "só portar"**: `LIKE '%MS%'` sobre descrição de produto é uma
regra frágil por natureza (substring de 2 letras — risco real de falso positivo em produtos
cuja descrição contenha "MS" por outro motivo, ex. miligramas abreviado, nome comercial,
etc.). Antes de portar, medir ao vivo (SQL Server, indisponível nesta sessão) **quantos e
quais produtos** batem nesse `LIKE` hoje, pra confirmar se a regra ainda faz sentido como
está ou se precisa de uma lista explícita de códigos de produto em vez de substring. **Dono**:
time de dados (medir + decidir) + time comercial (validar a lista de produtos, se a regra for
mantida).

### 5.6 `BSID`: dedup não é mais necessário (confirmado), mas o filtro `blart != 'ZP'` é — e sozinho muda o "a vencer" em ~8x

**Atualizado em 2026-09-08 com VPN/SQL Server disponível** — a hipótese original desta seção
(dedup por `bukrs+mandt+belnr+gjahr+buzei`) foi testada e **descartada**:
`SELECT bukrs,mandt,belnr,gjahr,buzei,COUNT(*) FROM SILVER.dataspherev2.bsid GROUP BY 1,2,3,4,5
HAVING COUNT(*)>1` retornou **0 chaves duplicadas** (48.362 linhas totais, todas únicas) — a
dedup defensiva que os models legados (`dim_limite_credito.sql`, `dim_credito_devolucoes.sql`)
faziam pode ter sido necessária no passado, mas não é hoje. **Sem ação necessária neste
ponto.**

Mas a investigação revelou que o **outro** filtro do mesmo bloco legado —
`WHERE blart != 'ZP'` — é muito mais material do que parecia. Medido ao vivo:
`blart='ZP'` sozinho é **23.435 linhas (48,5% de todo o `BSID`) somando R$ 359,2 milhões
(31,5% do valor total de R$ 1,14 bi)**. `fct_credito_devolucoes_sap.sql` já exclui `ZP`
(linha 69, confirmado no código); **`fct_limite_credito_sap.sql` não exclui — não tem
filtro de `blart` nenhum**.

Recalculando a CTE `faturamento_aberto` de `fct_limite_credito_sap.sql` linha por linha,
com e sem excluir `ZP` (usando a fórmula já corrigida, ver 5.8 abaixo, já que a fórmula atual
em produção dá sempre zero):

| | Incluindo `ZP` (como o código faz hoje) | Excluindo `ZP` (como o legado faz) |
|---|---|---|
| Valor_Saldo_Vencido | R$ 820,95 milhões | R$ 713,77 milhões |
| Valor_Saldo_A_Vencer | R$ 288,45 milhões | **R$ 36,42 milhões** |

**"A vencer" muda quase 8x** dependendo desse único filtro — `ZP` (provável "Pagamento",
a confirmar com o time funcional) parece concentrar lançamentos com vencimento futuro que não
deveriam contar como exposição de crédito real (ex.: adiantamento/programação de pagamento já
combinada, não dívida em aberto de fato). **Proposta**: adicionar `WHERE TRIM(blart) != 'ZP'`
na CTE `faturamento_aberto` de `fct_limite_credito_sap.sql`, igual já existe em
`fct_credito_devolucoes_sap.sql` — mudança de 1 linha, mas só depois de corrigir o bug de
data da 5.8 (sem isso corrigido, o filtro de `ZP` não muda nada porque a soma já dá zero de
qualquer forma). **Esforço**: baixo. **Dono**: time de dados (implementação) + confirmar com
Financeiro se `ZP` deve mesmo ser excluído do saldo de crédito aberto antes de mudar o número
que o negócio vê.

### 5.7 BUG confirmado em produção (2026-09-08, com VPN): `Valor_Saldo_Vencido`/`Valor_Saldo_A_Vencer` de `fct_limite_credito_sap` são SEMPRE ZERO

**Achado mais grave desta investigação — não é regra do legado não portada, é um defeito de
SQL ativo em `vendas_sap` hoje, silenciosamente quebrado.** Confirmado direto em produção:

```sql
SELECT COUNT(*), SUM(Valor_Saldo_A_Vencer), SUM(Valor_Saldo_Vencido), SUM(Valor_Exposicao_Total_SAP)
FROM GOLD.vendas_sap.fct_limite_credito_sap
-- 5.189 linhas | 0,00 | 0,00 | 1.047.498.xxx,xx
```

**100% das 5.189 linhas têm `Valor_Saldo_A_Vencer=0` e `Valor_Saldo_Vencido=0`**, mesmo com
`Valor_Exposicao_Total_SAP` (campo nativo `KNKK.SKFOR`, sem relação com o cálculo quebrado)
mostrando R$ 1,05 bilhão de exposição real. **Causa raiz**: `fct_limite_credito_sap.sql`
calcula a data de vencimento assim:

```sql
DATEADD(DAY, COALESCE(zbd3t, 0), TRY_CONVERT(DATE, CAST(zfbdt AS VARCHAR(8)), 112))
```

Mas `bsid.zfbdt` na Silver **já é `DATE`** (confirmado via `INFORMATION_SCHEMA.COLUMNS`), não
uma string `AAAAMMDD`. `CAST(zfbdt AS VARCHAR(8))` sobre uma coluna `DATE` produz os 8
primeiros caracteres do formato textual padrão (ex. `'2026-09-'`, cortando o dia) — uma string
inválida pro estilo `112` (`AAAAMMDD` sem separador) — então `TRY_CONVERT` retorna `NULL` em
**toda linha**, `DATEADD` sobre `NULL` também vira `NULL`, e as duas comparações
(`< GETDATE()`/`>= GETDATE()`) nunca são verdadeiras — cada `SUM(CASE ...)` cai sempre no
`ELSE 0`. Este é o mesmo padrão de bug já catalogado em `docs/CONTEXTO_VENDAS_SAP.md` §6.4
(SAP grava data como string, e o inverso aqui: uma `DATE` de verdade sendo tratada como se
fosse string SAP) — mas aqui o resultado não é um erro de conversão isolado, é a métrica
inteira zerada.

**Correção testada e confirmada** (bastava remover o `CAST`/`TRY_CONVERT` desnecessários,
já que a coluna já é `DATE`):

```sql
DATEADD(DAY, COALESCE(zbd3t, 0), zfbdt)
```

Recalculado com essa correção: `Valor_Saldo_Vencido` = R$ 820,95 milhões, `Valor_Saldo_A_Vencer`
= R$ 288,45 milhões (antes de aplicar também o filtro de `ZP` da 5.6) — números plausíveis e
na mesma ordem de grandeza de `Valor_Exposicao_Total_SAP` (R$ 1,05 bi).

**Impacto direto no `invest_sap`**: `Valor_Saldo_Vencido`/`Valor_Saldo_A_Vencer` são
consumidos em **`pages/7_Credito_Devolucoes.py`** (métrica "Saldo Vencido Total", hoje
mostrando R$ 0,00 pra qualquer cliente) e **`pages/27_Pendencia_x_Estoque.py`** (inclusive um
alerta condicional `if (df_credito["Valor_Saldo_Vencido"] > 0).any()` que **nunca dispara**
hoje, porque a condição nunca é verdadeira). Ou seja: qualquer visão de aging de crédito
vencido no dashboard está silenciosamente mostrando zero desde que este model existe.

**Proposta**: reportar ao time de dados (`data-platform`) como bug, não como melhoria —
trocar `TRY_CONVERT(DATE, CAST(zfbdt AS VARCHAR(8)), 112)` por `zfbdt` direto em
`fct_limite_credito_sap.sql`, e simultaneamente aplicar o filtro `blart != 'ZP'` da 5.6.
**Esforço**: trivial (2 linhas). **Impacto**: alto — é a métrica de aging de crédito vencido
usada em produção no `invest_sap`, hoje inútil. **Dono**: time de dados (correção no
`data-platform`) — enquanto isso não é corrigido rio acima, considerar avisar/ocultar essas
2 métricas nas páginas do `invest_sap` em vez de mostrar R$ 0,00 (que parece "sem inadimplência"
quando na verdade é "métrica quebrada").

### 5.8 `dt_vencimento_calc`: `ZBD1T` (legado) vs. `ZBD3T` (`vendas_sap`) — divergência, não necessariamente um gap

`dim_limite_credito.sql` (legado) calcula a data de vencimento como `zfbdt + ZBD1T` (dias do
**1º desconto**). `fct_limite_credito_sap.sql` usa `zfbdt + ZBD3T` (dias do **vencimento
líquido**, 3ª faixa da condição de pagamento SAP — sem desconto). Os dois campos existem na
Silver (`bsid.zbd1t`/`bsid.zbd3t`, confirmado em `models/silver/dataspherev2/bsid/bsid.sql`).

**Diferente dos itens 5.1-5.6, isto não é claramente um "gap a corrigir"** — `ZBD3T` (vencimento
líquido) é semanticamente mais correto pra "quando o título vence de fato" do que `ZBD1T`
(prazo do desconto por pagamento antecipado, que é sempre ≤ ZBD3T); é plausível que
`vendas_sap` tenha corrigido um uso impreciso do legado, não o contrário — **a escolha de
`ZBD3T` em si não é o problema** (confirmado na 5.7: o bug real era o `CAST`/`TRY_CONVERT` em
volta dela, não o campo escolhido). Registrado aqui só para quem for comparar números de
aging/vencido entre os dois schemas não estranhar uma divergência sistemática de *janela* de
vencimento (`ZBD1T` sempre ≤ `ZBD3T`, então o legado classifica como "vencido" um pouco mais
cedo) — nenhuma ação proposta sem confirmar com o time financeiro qual convenção reflete o
processo de cobrança real. **Dono**: time financeiro (confirmar convenção) — sem ação técnica
até lá.

### 5.9 Implementado (2026-09-08): régua de `Prioridade_Pedido` + segregação Livre/Carimbado em `vendas_sap`

Reverte a decisão de "não priorizar" das seções 5.1/5.2 depois de investigar, a pedido do
usuário, se "Carimbado" era só uma questão de checar o Centro. **Resposta**: não é 1 Centro =
Carimbado — é uma convenção de código de depósito (`LGORT`) **repetida por planta**, presente
na maioria das plantas de fabricação mas **ausente no maior centro de distribuição**:

| Centro (WERKS) | Nome | Tem "Carimbado" (1080/1190)? |
|---|---|---|
| 1000/1100/1200/1300/1900/2300, BG01, BG02 | Caucaia, P-100/200/300, São Paulo, Anápolis, Jaboatão, Bergamo (2 plantas) | ✅ |
| **1400** | **P-400** | ❌ |
| **2200** | **BLAU LOG** (maior estoque de longe, 16,2 milhões de un.) | ❌ |
| **CO10** | **Colômbia** | ❌ |

Implementado em `data-platform`, branch **`feature/prioridade-pedido-carimbado-sap`**
(a partir de `dev-to-main`), commit `7519cceb` — **só em `gold/vendas_sap/*`, nada tocado em
`gold/vendas/*` (legado)**, a pedido explícito do usuário. Ainda não mergeado/deployado (sem
`dbt build` neste ambiente — validado via SQL direto contra GOLD/SILVER de produção, só
leitura, ver abaixo).

**Mudanças**:

1. **`fct_pendencia_sap.sql`**: `Prioridade_Pedido` volta a ter os 6 níveis do legado
   (`Codigo_Org_Vendas='2000'`→1, `Tipo_Ordem_Venda='ZVCO'`→2, `IN ('ZGOV','ZVLC')`→3,
   `Codigo_Org_Vendas IN ('1000','4000')`→4, `='3000'`→5, demais→99). Nova coluna
   `Qtd_Estoque_Disponivel_Venda_Carimbado` (subconjunto do disponível que está em depósito
   reservado).
2. **`fct_estoque_lote_sap.sql`**: nova coluna `Tipo_Estoque_Deposito`
   (`Livre`/`Carimbado`/`Outros`, derivada de `Codigo_Deposito`).
3. **`fct_pendencia_status_sap.sql`**: FIFO reescrito em **2 bolsas** — "Livre" (tudo que não
   é Carimbado, disputada por todos os pedidos) e "Carimbado" (só disputada por
   `Prioridade_Pedido IN (2,3)`). **Diferente do legado**: a segregação aqui é por
   `(Mandante, Produto, Centro)` — o legado (`dim_pendencia.sql`) somava a bolsa Carimbado de
   *todas as plantas juntas* por material, sem separar por Centro, misturando estoque de
   plantas fisicamente distintas. Corrigido na implementação nova.

**Validação (leitura direta em produção, sem escrever nada)**:
- Nova régua de prioridade contra `fct_vendas_itens_sap` real: distribuição saudável nos 6
  níveis (1: 53.330 / 2: 5.226 / 3: 27.693 / 4: 237.954 / 5: 13.710 / 99: 86.338 itens) — não
  degenera em 1 bucket só.
- `Tipo_Estoque_Deposito` contra `SILVER.dataspherev2.mchb` real: 21.017 unidades em
  "Carimbado" (bate com a medição via HANA de §5.1), 19,1 milhões em "Livre", **440,1 milhões
  em "Outros"** — por isso o desenho ficou "Livre = Total − Carimbado" (inclui "Outros") em vez
  de restringir a só `LGORT IN ('1050','4000')` como o legado fazia; do jeito do legado, 440
  milhões de unidades de estoque real ficariam de fora do FIFO por engano.
- Reconstrução end-to-end do pipeline (as 3 mudanças juntas, via SQL direto contra
  `GOLD.vendas_sap.*` de produção): `Status_Alocacao_Virtual='CARIMBAGEM'` sai de **0** pra
  **12 linhas**; total de linhas em carteira preservado (46.203 vs 46.201 do sistema atual,
  diferença de snapshot, não de lógica).

**Próximos passos**: revisar o diff, rodar `dbt build`/testes no pipeline real do
`data-platform` (o `accepted_values` de `Prioridade_Pedido` e `Tipo_Estoque_Deposito` já foram
atualizados nos `.yml`), decidir se faz push da branch e abre PR. **Dono**: usuário/time de
dados (revisão e deploy) — implementação feita, falta o ciclo normal de PR/review/CI do
`data-platform`.

### 5.10 Implementado (2026-09-08): correção do bug de crédito (5.6/5.7) e reclassificação de Org Vendas (5.3) — na mesma branch; 5.5 medido e descartado

Continuação da 5.9, mesma sessão, pedido do usuário de avançar nas demais propostas
(exceto 5.4, explicitamente deixada de lado — ver nota no início da 5.4).

- **5.7 + 5.6 (bug de `Valor_Saldo_Vencido`/`Valor_Saldo_A_Vencer` + filtro `ZP`)**: commit
  `ea1d11db`. Trocado `TRY_CONVERT(DATE, CAST(zfbdt AS VARCHAR(8)), 112)` por `zfbdt` direto
  e adicionado `WHERE TRIM(blart) != 'ZP'` em `fct_limite_credito_sap.sql`. Valores
  recalculados batem com o medido antes da correção (R$713,8mi vencido, R$36,4mi a vencer).
- **5.3 (Org Vendas)**: commit `5763716b`. Só a regra geral (`VKORG=4000→1000`, BÉRGAMO sob
  BLAU HOSPITALAR) como coluna **aditiva** `Codigo_Org_Vendas_Ajustada` em
  `fct_faturamento_itens_sap.sql` — `Codigo_Org_Vendas` original não foi tocado. Os ~5
  pedidos individuais hardcoded do legado (correções pontuais de 2024/2025) **não foram
  portados** deliberadamente: baixo valor de manter como regra viva, e não dava pra validar
  o casamento de formato de `nr_pedido` (Salesforce) com `VBELN` (SAP) sem mais investigação.
  Volume real da regra geral confirmado: 21.683 faturas de Org 4000
  (`SILVER.dataspherev2.vbrk`).
- **5.5 (Canal MS por produto) — medido e descartado, não implementado**: `Descricao_Produto
  LIKE '%MS%'` contra `dim_material_sap` real bate **694 produtos**, a maioria falso
  positivo — reagente de laboratório ("EMSURE"), acessório de TI ("HD EXTERNO... MS"),
  colunas de cromatografia ("COLUNA X-TERRA MS"). Portar a regra como está introduziria dado
  errado em produção; só valeria a pena com lista curada de `Codigo_Produto` validada pelo
  time comercial, não um `LIKE` de substring. Decisão: não implementar.

Todos os commits desta sessão ficam na mesma branch `feature/prioridade-pedido-carimbado-sap`
(local, sem push ainda) — ver §5.9 pro status de deploy.

### 5.11 Implementado (2026-09-08): as 3 propostas formalizadas (§2) — câmbio TCURR, VBUK/T001K/T003T, MCHBH/MBEWH/`fct_movimento_lote_sap`

Continuação da mesma sessão/branch, a pedido do usuário ("tem a proposta de conversão do
câmbio, de ingestão de crédito, e movimento estoque"). Commits `64509e2e` e `ac897d2f`.

**Achado extra durante a validação**: `SILVER.dataspherev2.tcurr.gdatu` (já ingerida desde
2026-08-25) estava **sempre NULL** — bug real e independente de qualquer proposta, achado só
porque tentei validar a conversão com dado real antes de escrever a Parte B. Corrigido — ver
detalhe em `docs/CONTEXTO_VENDAS_SAP.md` §6.12.

**O que foi validado com dado real** (leitura direta em produção, sem escrever nada):

- Conversão BRL (TCURR): USD/BRL ~5,10-5,13 (set/2026), e o faturamento 2026 YTD convertido
  bate com essas taxas (COP R$362,7mi→R$534mil, USD R$18,9mi→R$97,0mi, UYU R$268,1mi→R$34,7mi).
- `fct_movimento_lote_sap`: 2.523.686 linhas com lote preenchido, movimentos de hoje
  (2026-09-08) presentes e consistentes.

**O que ficou implementado mas SEM validação de dado real** (VBUK, T001K, T003T, MCHBH,
MBEWH são tabelas novas na ingestão — não têm nenhuma linha até a extração Bronze/Silver
rodar em produção pelo menos 1 vez, o que só o time de dados/Airflow consegue disparar):

- `Status_Credito_Documento_SAP_Codigo`/`_Texto` em `fct_pendencia_sap` (via VBUK.CMGST).
- `Empresa_Codigo`/`Moeda_Oficial_Centro` em `dim_centro_sap` (via T001K→T001, aditivo).
- `Descricao_Tipo_Documento_Contabil` em `fct_credito_devolucoes_sap` (via T003T).
- `MCHBH`/`MBEWH` em si (só ingestão — nenhum model Gold novo depende delas ainda; consumo
  fica pro invest_sap trocar `scripts/trace_lote.py::estoque_historico_material_centro()` de
  HANA ao vivo pra `SELECT` na Silver, depois que existir dado).

**Próximo passo obrigatório antes de confiar nesses 4 campos**: rodar a extração
Bronze→Silver→Gold em produção pelo menos 1 vez (fora do alcance desta sessão) e então
reconferir com uma consulta real, igual foi feito com TCURR/`fct_movimento_lote_sap`.

Todos os commits, incluindo estes 2, ficam na mesma branch
`feature/prioridade-pedido-carimbado-sap` (local, sem push ainda — 5 commits no total).

---

## 6. Priorização sugerida

| Item | Esforço | Impacto | Depende de decisão de negócio? |
|---|---|---|---|
| ~~5.7 BUG: `Valor_Saldo_Vencido`/`Valor_Saldo_A_Vencer` sempre zero em `fct_limite_credito_sap`~~ | — | **Implementado (2026-09-08)** — `feature/prioridade-pedido-carimbado-sap` (`data-platform`), commit `ea1d11db`. Corrigido junto com a 5.6. | — |
| ~~5.6 Filtro `blart != 'ZP'` ausente em `fct_limite_credito_sap`~~ | — | **Implementado (2026-09-08)**, mesmo commit `ea1d11db` — valores corrigidos: R$713,8mi vencido, R$36,4mi a vencer (excluindo `ZP`) | — |
| ~~5.6 (dedup de `BSID`)~~ | — | **Descartado, confirmado**: 0 chaves duplicadas em 48.362 linhas de `BSID` hoje — a dedup do legado não é mais necessária | — |
| ~~5.4 `ajuste_tributario` sem equivalente em `fct_faturamento_itens_sap`~~ | — | **Não priorizar (decisão do usuário, 2026-09-08)** — é correção manual de uma alíquota que já foi corrigida na fonte a partir de agora; as 386 linhas/R$646,7mil medidas são resíduo do período com alíquota errada, não fluxo permanente. Manter como está. | — |
| ~~5.1 Régua completa de `Prioridade_Pedido` (VKORG/AUART) não portada~~ | — | **Implementado (2026-09-08)** — decisão revertida depois de confirmar (com o usuário) que os depósitos "Carimbado" seguem uma convenção por planta, não um Centro único. Ver §5.9 abaixo. | — |
| ~~5.2 Estoque "Carimbado" (`LGORT` 1080/1190) não segregado em `fct_estoque_lote_sap`~~ | — | **Implementado (2026-09-08)**, junto com a 5.1 — segregação agora por (Mandante, Produto, Centro), corrigindo o bug do legado que misturava plantas. Ver §5.9. | — |
| ~~5.3 Realocação manual de Org Vendas (`4000→1000`) em `fat_faturamento`~~ | — | **Implementado parcialmente (2026-09-08)** — `feature/prioridade-pedido-carimbado-sap`, commit `5763716b`: só a regra geral (4000→1000, 21.683 faturas medidas), como coluna aditiva `Codigo_Org_Vendas_Ajustada` (não substitui `Codigo_Org_Vendas`). Os ~5 pedidos individuais hardcoded do legado **não foram portados** (correções pontuais históricas de 2024/2025, baixo valor de manter como regra viva) — ainda depende de confirmar com comercial/financeiro se a regra geral é a visão correta antes de usar em relatório oficial. | Parcial |
| ~~5.5 Regra "produto contém 'MS'" (Canal MS por produto) não replicada~~ | — | **Medido e descartado (2026-09-08)**: `LIKE '%MS%'` em `Descricao_Produto` bate 694 produtos em `dim_material_sap`, maioria falso positivo (reagente "EMSURE", "HD EXTERNO... MS", colunas de cromatografia "COLUNA X-TERRA MS") — regra fràgil demais pra portar como está. **Não implementado.** Só valeria a pena com lista curada de códigos de produto pelo time comercial, não substring. | — |
| 5.8 `ZBD1T` (legado) vs. `ZBD3T` (`vendas_sap`) na data de vencimento | — | Divergência a esclarecer, não gap confirmado — `ZBD3T` pode já ser a escolha certa (o problema real era o bug da 5.7, não o campo) | Sim (Financeiro define a convenção) |
| ~~3.2 Deploy fix `KWMENG`/`ZMENG`~~ | — | **Concluído** — confirmado em produção em 2026-09-05 (ver §3.2) | — |
| ~~4.3 `MARA.PRDHA` pra Linha de Negócio (fallback por cliente-dominante)~~ | Testado e revertido | Cobertura alta (92,4% do valor) não virou ganho real — implementado, medido contra `GOLD`, deu **0 clientes novos classificados** (a população que bateria já é 100% coberta pelas camadas 1/2 existentes); só `AEST` discrimina bem (92,6%), FARMA/ONCO-HEMATO não. Ver §4.3 pra linhas em aberto (nível item, nível 2/3 de `PRDHA`) | Não |
| 3.1 Corrigir `Flag_Pendencia` (falso positivo faturado) | Médio | Alto (84,5% da quantidade pendente) | Não |
| ~~4.9 `T001K→T001` como fonte oficial de moeda por centro~~ | — | **Implementado (2026-09-08)** — `Empresa_Codigo`/`Moeda_Oficial_Centro` em `dim_centro_sap` (aditivo). **Não validado com dado real** (T001K é ingestão nova, sem dado até a extração rodar). Ver §5.11 | — |
| **4.11 `salesforce.User.regional`/`unidade_de_negocio` como sinal complementar** | Baixo (campo já acessível) | Médio — **medido**: só ~17,5% dos itens de faturamento têm território geográfico útil (resto é `Matriz`/sem match); pior cobertura que o crosswalk atual, mas é sinal independente sem manutenção manual | Não |
| ~~4.13 `rh.fct_funcionario` (Unidades De Negocios) como auditoria de QA~~ | — | **Implementado** — `auditoria_linha_negocio_rh_vs_estrutura` em `pages/2_Auditoria.py`; precisão alta (82-94%) mas 0,4% de cobertura nova, valor real é achar as divergências (R$57,7mi/12 meses) pra investigar | Parcial (decidir os casos de desacordo é do time comercial) |
| ~~4.10 Elos `HRP1000`/`PA0001` → hierarquia comercial~~ | — | **Superado pela 4.11** — o Salesforce já responde a mesma pergunta sem precisar do elo com RH; não vale investir na trilha do SAP HR pra esse fim específico | — |
| ~~2.1 `TCURR`/conversão BRL~~ | — | **Implementado e validado com dado real (2026-09-08)** — bug de `gdatu` sempre NULL corrigido + conversão nos 3 models multi-moeda. Ver §5.11 | — |
| ~~4.1 `VBUK.CMGST` — status de crédito nativo por pedido~~ | — | **Implementado (2026-09-08)** — `Status_Credito_Documento_SAP_Codigo`/`_Texto` em `fct_pendencia_sap`. **Não validado com dado real** (VBUK é ingestão nova). Ver §5.11 | — |
| 3.4 Flag de backlog sem reserva | Baixo | Médio-alto (67,9% do backlog aberto) | Parcial (validar limiar) |
| 3.5 `Cliente_Bloqueado_Efetivo` | Baixo | Médio (evita falso-negativo de crédito) — **considerar substituir por 4.1 se `VBUK` for ingerida** | Sim (regra exata) |
| 3.3 Cobertura de testes dbt | Médio (contínuo) | Alto (preventivo — evita o próximo achado silencioso) | Não |
| ~~3.8(b) migrar `devolucoes_credito_motivo` pra `fct_credito_devolucoes_sap`~~ | — | **Concluído em 2026-09-05** — função migrada, testada, `Montante` agora com sinal contábil real (ver §3.8) | — |
| ~~3.8(a) Ingerir `T003T`~~ | — | **Implementado (2026-09-08)** — `Descricao_Tipo_Documento_Contabil` em `fct_credito_devolucoes_sap`. **Não validado com dado real** (T003T é ingestão nova). Ver §5.11 | — |
| **4.2 `VBAP.LPRIO` como base de Prioridade_Pedido real** | Baixo técnico + confirmação funcional | Médio (resolve status morto `CARIMBAGEM` com dado real) | Parcial (confirmar texto dos códigos, tabela de customizing ainda não identificada) |
| ~~2.2 `MCHBH`/`MBEWH` + `fct_movimento_lote_sap`~~ | — | **Implementado (2026-09-08)** — `fct_movimento_lote_sap` **validado com dado real** (2,52 milhões de linhas). `MCHBH`/`MBEWH` (ingestão) **não validados** (tabelas novas, sem dado até extração rodar). Ver §5.11 | — |
| **4.5 `ZZNUM_LICITACAO`/`ZZNUM_PREGAO` — sinal nativo de venda pública** | Baixo (campo já ingerido) | Médio (refina Canal MS/Público, hoje por `LIKE` em nome de cliente) | Não |
| 3.9 Dimensões sem match (0,39%) | Baixo | Baixo (escala pequena) | Não |
| 3.11 `VBPA`/vendedor SAP vazio | Baixo (é pergunta) | Médio (destrava decisão de arquitetura de vendedor) — **§4.7 já descarta alternativa técnica** | **Sim** |
| 3.6 `CARIMBAGEM`/Prioridade_Pedido | Baixo técnico | Baixo (status morto, sem uso ativo hoje) — **ver 4.2, pode ter resposta técnica pronta** | **Sim** |
| 3.10 Linha de Negócio como model Gold | Médio | Médio (organização, não cobertura) | Parcial |
| 3.7 Auditoria plan-cache restante | Médio | Alto (risco de número errado, não só lento) | Não (mas é fora do DW, é `invest_sap`) |
| 3.12 Reconciliação `vendas` × `vendas_sap` | Médio-alto | Baixo hoje (sem demanda ativa) | Não |
| 4.6 Bloqueio nativo (`LIFSK`/`FAKSK`/`LIFSP`) pro backlog zumbi | — | Nenhum (achado negativo: mecanismo existe mas quase não é usado) | Não (é achado de processo, não de dado) |
| ~~4.4 `VBAP.PRCTR` como segmentação de negócio~~ | — | **Descartado** — é planta física + produto individual, não Linha de Negócio (revisado após `CEPC`/`CEPCT`, ver §4.4). Achado que sobrevive: `PRCTR='0000005346'` identifica "Botulift" com precisão | — |
| ~~MVKE como fonte de classificação~~ | — | **Descartado** — tabela tem dado mas `MVGR1`/`SPART` não servem (vazia/inexistente), ver §4.9 | — |

---

## 7. Referências

- `docs/CONTEXTO_VENDAS_SAP.md` — arquitetura completa, achados de auditoria (§6), pipeline
  de ingestão (§5).
- `docs/INVESTIGACAO_PENDENCIA_SAP.md` — achado `KWMENG=0`, correção aplicada, auditoria do
  fluxo.
- Specs de câmbio (`TCURR`), movimento de estoque (`MCHBH`/`MBEWH`/`fct_movimento_lote_sap`)
  e crédito/mestres SAP (`VBUK`/`T001K`/`T003T`) — implementadas e mergeadas em `origin/main`
  do `data-platform` (§2, §5.11); os documentos `PROPOSTA_*.md` originais foram removidos
  deste repo em 2026-09-09, conteúdo histórico só no git log.
- `docs/PROPOSTA_CORRECAO_SINAL_FATURAMENTO_SAP.md` — spec ainda em aberto (patch não
  aplicado) pra corrigir o sinal de `Valor_Liquido_Faturamento` em notas de crédito/estorno.
- `docs/COMO_RODAR.md` — como conectar/rodar os scripts citados.
- Investigação ao vivo do §4 (2026-09-05): `scripts/ddic_lookup.py` (DDIC) + `read_hana_sql()`
  direto em `IB_SAPECC`, mesma conexão de `scripts/trace_pedido.py`/`trace_lote.py` — queries
  não persistidas em script do projeto (exploratórias), reproduzíveis com os nomes de
  tabela/campo citados em cada subseção.
- Investigação do §5 (2026-09-08): leitura direta dos 15 `.sql` de
  `airflow/dags/dbt/models/gold/vendas/` no checkout local do `data-platform`
  (`dim_pendencia`, `dim_pendencia_status`, `dim_estoque`, `dim_limite_credito`,
  `dim_credito_devolucoes`, `fat_faturamento`, entre outros) comparados linha a linha com os
  `.sql` de `.../gold/vendas_sap/`, mais `mkdocs/docs/vendas/vendas.md` (documentação textual
  da regra de prioridade, confirma o achado de §5.1) e `read_hana_sql()` pra medir volume real
  de `VKORG`/`AUART`/`LGORT` hoje (§5.1/§5.2). SQL Server (BRONZE/SILVER/GOLD) ficou
  indisponível nesta sessão (sem VPN) — §5.4/§5.6 dependem de acesso a ele pra quantificar
  volume/vigência antes de priorizar.
