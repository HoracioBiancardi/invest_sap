# Investigação — Pendência "sumida" no pedido 137490

> Registro da investigação de 2026-08-24: por que o pedido 137490 não aparecia em nenhuma
> visão de pendência, o escopo real do problema, a correção aplicada e o resultado da
> auditoria geral do fluxo. Para entender a arquitetura e os modelos citados aqui, veja
> **`CONTEXTO_VENDAS_SAP.md`**. Para como rodar os scripts usados nesta investigação, veja
> **`COMO_RODAR.md`**.

## 1. O sintoma: pedido não aparece na pendência

Investigando por que o pedido **137490** (`0000137490`, item `000010`, AUART `ZVCO`,
cliente `0003000678`) não aparecia em nenhuma visão de pendência: ele **existe** em
`fct_vendas_itens_sap` e `fct_pendencia_sap`, mas com `Qtd_Pedida = 0` — apesar de
`Valor_Liquido_Pedido = R$ 167.110,95`. Como toda a cadeia de pendência é calculada sobre
quantidade (`Qtd_Pendente_* = MAX(Qtd_Pedida - Qtd_Remetida/Faturada, 0)`), o resultado é
`Flag_Pendencia = 0` e `Status_Pendencia = 'Concluido'` — **um pedido que nunca foi remetido
nem faturado é classificado como concluído**, e some de qualquer filtro `WHERE
Flag_Pendencia = 1`.

## 2. Causa raiz: `VBAP.KWMENG=0`, quantidade real em `ZMENG`

Confirmado via HANA (`VBAP`): para este pedido, `KWMENG` (campo que
`fct_vendas_itens_sap`/`fct_pendencia_sap` usam como `Qtd_Pedida`) é **0**, mas
`ZMENG = 1092` e `NETWR / NETPR = 167110.95 / 153.03 ≈ 1092` — a quantidade real do item
está em `ZMENG`, não em `KWMENG`. O pedido também não tem nenhuma linha em `VBEP`
(cronograma de entrega), o que é consistente com pedidos do tipo `ZVCO` (contrato de
valor / liberação de contrato), onde o SAP não necessariamente popula `KWMENG`.

Validação em amostra maior (não só esse pedido): entre **7.539 itens** com `KWMENG=0` e
`NETWR>0` em `VBAP` (toda a base, todos os tipos de pedido), `ZMENG` bate com
`NETWR/NETPR` ajustado por `KPEIN` (unidade de preço) em **98,4%** dos casos — os poucos
que não batem exatamente são diferenças de arredondamento, não divergência real.

## 3. Escopo — não é um caso isolado

Consulta em `fct_pendencia_sap` agrupando por `Tipo_Ordem_Venda` com
`Qtd_Pedida = 0 AND Valor_Liquido_Pedido > 0`:

| Tipo_Ordem_Venda | Itens afetados | % do tipo | Valor afetado |
|---|---|---|---|
| **ZVCO** | 5.116 | **100%** (todos os itens ZVCO) | **R$ 2,45 bilhões** |
| UNCR | 784 | 100% | R$ 70,7 milhões |
| ZN01 | 551 | 100% | R$ 12,0 milhões |
| ZDES | 512 | 100% | R$ 10,7 milhões |
| ZDRB | 11 | 100% | R$ 43,9 mil |
| ZPEC | 8 de 9 | 89% | R$ 22,7 mil |
| ZD01 | 7 | 100% | R$ 788,6 mil |

Todos os demais tipos de ordem (ZPRI, ZGOV, URCO, ZV01, ...) têm 0 itens afetados — o
problema é **específico destes 7 tipos**, e para `ZVCO` é sistemático (100% dos itens).

O mesmo padrão também aparece em `fct_vendas_canceladas_sap` (`Qtd_Cancelada=0` com
`Valor_Liquido_Cancelado>0`): **550 itens, R$ 454,7 milhões** (548 ZVCO + 2 UNCR).

E no schema legado `GOLD.vendas.dim_pendencia` (pipeline separado, também lê
`VBAP.kwmeng` diretamente): o efeito é **pior** — a linha inteira some da tabela em vez de
ficar com um flag zerado, porque o filtro `WHERE kwmeng > remetido OR kwmeng > faturado`
nunca é satisfeito com `kwmeng=0` (`0 > 0` é falso nos dois lados). Confirmado: o pedido
137490 **não existe** em `GOLD.vendas.dim_pendencia` — zero linhas. Isolei as 4 condições
do `WHERE` desse model contra `SILVER.dataspherev2.vbap`/`vbup`/`vbfa`: não está cancelado,
não tem motivo de recusa, `gbsta='A'` (passaria) — o único filtro que reprova é mesmo o de
pendência.

## 4. Por que importa mais do que parece

`ZVCO` é justamente o tipo de ordem que a regra de `Prioridade_Pedido`
(`CONTEXTO_VENDAS_SAP.md` §6.3) marca como prioridade **1** (a mais alta) — ou seja, o bug
atinge exatamente a categoria de pedido que o negócio trata como mais urgente. R$ 2,45 bi
em ZVCO não apareciam como backlog em nenhum dashboard de pendência.

## 5. Cross-validação via Salesforce

Rastreando o pedido 137490 pelas 3 camadas (SAP cru via HANA → Gold `vendas_sap` →
Salesforce `Opportunity`/`OpportunityLineItem`, ver `CONTEXTO_VENDAS_SAP.md` §8 pra
entender o elo entre as tabelas), o Salesforce **confirma de forma independente** que o bug
estava de fato escondendo uma pendência real, não é só um artefato do SQL:

| Camada | Fonte | O que mostra |
|---|---|---|
| SAP cru | `VBAP` (HANA) | `KWMENG=0`, `ZMENG=1092` |
| Gold (antes do fix) | `fct_pendencia_sap` | `Qtd_Pedida=0`, `Flag_Pendencia=0`, `Status_Pendencia='Concluido'` ❌ |
| Salesforce | `OpportunityLineItem` | `Quantity=1092`, `Qtde_Pendente__c=1092`, `Status_Faturamento__c='Não Faturado'` ✅ |
| Salesforce | `Opportunity` (`006N500000qRyerIAC`, "Empenho 212003 OF 368/2026") | `is_won=True`, `pendencia_pedido=R$ 189.898,80`, `qtde_faturada=0`, `situacao_pedido_del='PEDIDO CRIADO'`, `retorno_motivo_status='O pedido foi criado com sucesso!'` |

O Salesforce nunca teve o problema — ele calcula `Qtde_Pendente__c`/`pendencia_pedido` a
partir dos próprios campos (`Qtde_Ordem__c` etc.), sem depender de `VBAP.KWMENG`. Isso deu
confiança de que (a) o campo certo do lado SAP é mesmo `ZMENG` para pedidos
`ZVCO`/similares, e (b) `SILVER.salesforce.OpportunityLineItem.Qtde_Pendente__c` poderia
servir de fonte alternativa/cross-check enquanto o fix não estivesse deployado.

(Nota: `Valor_Liquido_Pedido` no SAP = R$ 167.110,95 vs. `amount`/`Pendencia__c` no
Salesforce = R$ 189.898,80 — divergência de ~13,6%, provavelmente bruto vs. líquido
[impostos/descontos]. Não investigado a fundo; citado aqui para não confundir quem for
conciliar os dois valores depois.)

## 6. Correção aplicada

**2026-08-24, commit local `33d0cf49` no `data-platform`, branch `feature/restruct-sap-vendas`
(sem push).** Regra aplicada: `COALESCE(NULLIF(kwmeng, 0), zmeng, 0)` — usa `KWMENG`, só
cai pra `ZMENG` quando `KWMENG` é zero. Arquivos alterados:

- `airflow/dags/dbt/models/gold/vendas_sap/fct_vendas_itens_sap/fct_vendas_itens_sap.sql`
  — campos `Qtd_Pedida_Original` e `Valor_Unitario_Pedido`
- `airflow/dags/dbt/models/gold/vendas_sap/fct_vendas_canceladas_sap/fct_vendas_canceladas_sap.sql`
  — campo `Qtd_Cancelada`
- `airflow/dags/dbt/models/gold/vendas/dim_pendencia/dim_pendencia.sql` (schema legado) —
  campo `kwmeng_quantidade_da_ordem_acumulada_em_unidade_de_venda`, usado no filtro de
  pendência

`fct_pendencia_sap.sql` e `dim_pendencia_status.sql` não precisaram de mudança — herdam o
campo já corrigido via `ref()`/select-through.

**Validação (sem escrever nada em produção):** rodei a expressão exata do patch direto
contra `SILVER.dataspherev2.vbap` (469.008 linhas, sem erro de runtime, sem divisão por
zero) e confirmei o pedido 137490 corrigido: `1092` unidades, `R$ 153,03` unitário — bate
com `NETWR/NETPR` original.

**Ainda não commitado remotamente nem passou por `dbt run`/`dbt build`** — precisa validar
em dev antes do próximo deploy em `GOLD`. Ver §8 (Próximos passos).

## 7. Auditoria do fluxo — resultados

Depois do fix aplicado localmente (mas **antes do deploy**), rodei
`scripts/audit_pendencia_flow.py` (ver `COMO_RODAR.md` para o que cada checagem faz) pra
ver se havia problemas parecidos em outros lugares do fluxo. Resultado (2026-08-24):

- **`valor_sem_quantidade`**: confirma os números da §3 acima (`fct_vendas_itens_sap`,
  `fct_vendas_canceladas_sap`). `fct_faturamento_itens_sap` e o `dim_pendencia` legado
  (já com o fix local aplicado) vieram limpos.
- **`pendencia_escondida`**: só **15 itens** (não 5.116) batem no critério mais estrito de
  "zero movimento em absoluto" (nunca remetido nem faturado). A maioria dos itens
  `Qtd_Pedida=0` já tem alguma remessa/fatura registrada (que vem de `VBFA`, não depende de
  `VBAP.KWMENG`), então não caem nesse filtro específico — mas **ainda estão errados**: com
  `Qtd_Pedida=0`, `Flag_Totalmente_Faturado` fica sempre 1 (`Qtd_Faturada >= 0` é sempre
  verdade) mesmo que só uma fração real tenha sido faturada. `valor_sem_quantidade` é o
  detector mais completo pra esse bug específico; `pendencia_escondida` pega só o
  subconjunto "nunca tocado" (o caso do pedido 137490), mas fica como sentinela genérico
  pra bugs *futuros* parecidos, de qualquer causa.
- **`reconciliacao_contagem`**: nenhuma anomalia — confirma que o bug é de **valor**
  (quantidade zerada), não de **linhas perdidas** no join Silver→Gold.
- **`integridade_dimensoes`**: achado novo, **separado** do bug de `KWMENG` — **1.621 itens
  (0,39%)** de `fct_pendencia_sap` sem `Descricao_Produto` (join com `dim_material_sap`
  falhou), e um número bem menor sem `Nome_Cliente` (66, 0,02%) ou `Nome_Centro` (24,
  0,01%). Escala pequena, não investigado a fundo — provavelmente material/cliente/centro
  cadastrado no pedido mas ausente/desatualizado na respectiva dimensão. Fica como próximo
  item de investigação se alguém notar produtos "sem nome" em algum relatório.

## 8. Próximos passos desta investigação

- **Deploy do fix (pendente):** o commit `33d0cf49` está só local. Falta: validar em
  ambiente de dev/staging, dar push, rodar
  `dbt build --select fct_vendas_itens_sap+ fct_vendas_canceladas_sap+ dim_pendencia+`
  (o `+` reconstrói os models downstream) e então conferir de novo o pedido 137490 em
  `GOLD.vendas_sap.fct_pendencia_sap` (deve virar `Flag_Pendencia=1`,
  `Status_Pendencia='Pendente Logistico e Fiscal'`).
- **Re-rodar a auditoria pós-deploy:** `valor_sem_quantidade` e `pendencia_escondida`
  devem zerar depois que o fix for pra produção. Se não zerarem, o fix não cobriu tudo.
- **Investigar `integridade_dimensoes`:** os 1.621 itens sem `Descricao_Produto` (§7) —
  é uma dimensão desatualizada, uma chave de join divergente, ou material realmente sem
  cadastro completo?
