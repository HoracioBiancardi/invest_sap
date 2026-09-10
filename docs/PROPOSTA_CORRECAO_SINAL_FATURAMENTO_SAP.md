# Proposta — Corrigir sinal de `Valor_Liquido_Faturamento` (nota de crédito/estorno) em `fct_faturamento_itens_sap`

> Spec pra levar ao time de dados propor no repo `data-platform`
> (`/home/swordpower/Documentos/REPO/TRABALHO/data-platform`). Documento separado de
> `CONTEXTO_VENDAS_SAP.md` porque este aqui é a proposta de implementação — campo real,
> distribuição medida, patch de código — pronta pra copiar/colar e ajustar, não só o resumo
> do achado.
>
> **Status (2026-09-10): patch aplicado só pra `vbtyp='O'`** (confirmado) no `data-platform`
> (`airflow/dags/dbt/models/gold/vendas_sap/fct_faturamento_itens_sap/
> fct_faturamento_itens_sap.sql` + `.yml`), branch `feature/prioridade-pedido-carimbado-sap`,
> ainda **não commitado/sem deploy**. `vbtyp` exposto como nova coluna
> `Categoria_Documento_Faturamento` (auditável). `S`/`N`/`5`/`6` continuam com o sinal
> original, sem correção — aguardando confirmação de negócio (ver "O que fica em aberto").

## Contexto

Usuário comparou `GOLD.vendas.fat_faturamento` (legado) vs
`GOLD.vendas_sap.fct_faturamento_itens_sap` (SAP) por produto+mês e achou divergência
sistemática (SAP sempre maior, ~R$22mi acumulados jan-ago/2026). Investigação inicial (ver
`CONTEXTO_VENDAS_SAP.md` §10) descartou sincronismo/atraso de carga como causa — os meses
divergentes eram todos meses já fechados, não concentrados em fronteira de mês.

Rastreamento ponta a ponta (produto `PA6019`, jan/2026, pedido `0060011615`) achou 2
documentos de fatura pro mesmo pedido/produto: a fatura original (`0090261747`,
`Tipo_Documento_Faturamento='ZGOV'`, `+R$106.520,00`, 07/01) e o estorno dela 9 dias depois
(`0090262737`, `Tipo_Documento_Faturamento='REB'`, também **positivo** `+R$106.520,00`,
16/01) — deveria ser negativo pra zerar a fatura original, mas não é. O legado já tinha essa
mesma NF de estorno com valor **negativo** (`-R$106.520,00`).

## Achado — `VBRK.VBTYP` já resolve isso, sem precisar de tabela nova

Primeira hipótese (`TVFK`, categoria de fatura) tinha um erro de leitura de coluna — o campo
certo é outro, e **já está disponível**, sem nenhuma ingestão nova:

`SILVER.dataspherev2.vbrk` já tem a coluna `vbtyp` (`vbrk.sql:40` — categoria do documento de
vendas/SD, mesmo domínio de `vbfa.vbtyp_n` já usado nesse mesmo model pra achar a remessa,
ver `fct_faturamento_itens_sap.sql` linha ~203). Ela só não é selecionada na CTE `vbrk_src`
do model Gold hoje.

Confirmado ao vivo (`SELECT` direto em `SILVER.dataspherev2.vbrk`) pro caso rastreado:

| Numero_Faturamento | Tipo_Documento_Faturamento | `vbtyp` |
|---|---|---|
| `0090261747` (fatura original) | ZGOV | **M** |
| `0090262737` (estorno) | REB | **O** |

`M` = Rechnung (fatura normal), `O` = Gutschrift (nota de crédito) — domínio SAP padrão.
Confirma exatamente o caso: o estorno é uma nota de crédito de verdade, categorizada como tal
pelo próprio SAP, só que com `NETWR` guardado em módulo (positivo) — convenção SAP normal pra
Gutschrift, o sinal contábil nasce da categoria do documento, não do valor gravado.

### Distribuição de sinal por `vbtyp` (histórico completo, `SILVER.dataspherev2.vbrp`+`vbrk`)

| `vbtyp` | Significado (domínio SAP) | Positivos | Negativos | Nº `fkart` distintos |
|---|---|---|---|---|
| `M` | Rechnung (fatura) | 420.505 | 7.664 | 31 |
| `O` | **Gutschrift (nota de crédito)** | **20.818** | **42** | **7** |
| `S` | Rechnungsstorno (estorno de fatura) | 2.274 | 73 | 2 |
| `N` | (provável: estorno de nota de crédito) | 19.930 | 863 | 3 |
| `5`/`6` | não identificado, volume baixo (97/535 linhas) | — | — | 1 cada |

`M` (fatura normal) é majoritariamente positivo com uma cauda negativa natural (linha de
desconto/correção dentro de fatura normal — esperado, não é bug). `O` é **quase 100%
positivo** (20.818 de 20.860) apesar de ser, por definição SAP, nota de crédito — mesmo
padrão do caso rastreado, agora confirmado em **7 tipos de documento diferentes**, não só
`REB` (achado original só tinha pego 1 desses 7 na amostra testada).

## Proposta de correção

Em `airflow/dags/dbt/models/gold/vendas_sap/fct_faturamento_itens_sap/fct_faturamento_itens_sap.sql`:

**1.** Adicionar `vbtyp` na CTE `vbrk_src` (linha ~118-136):

```sql
vbrk_src AS (
    SELECT
        CAST(mandt AS VARCHAR(3)) AS mandt,
        CAST(vbeln AS VARCHAR(10)) AS vbeln,
        fkdat,
        erdat,
        CAST(erzet AS VARCHAR(6)) AS erzet,
        CAST(kunrg AS VARCHAR(10)) AS kunrg,
        CAST(vkorg AS VARCHAR(4)) AS vkorg,
        CAST(vtweg AS VARCHAR(2)) AS vtweg,
        CAST(spart AS VARCHAR(2)) AS spart,
        CAST(fkart AS VARCHAR(4)) AS fkart,
        CAST(waerk AS VARCHAR(5)) AS waerk,
        CAST(rfbsk AS VARCHAR(2)) AS rfbsk,
        CAST(belnr AS VARCHAR(20)) AS belnr,
        CAST(gjahr AS VARCHAR(4)) AS gjahr,
        fksto,
        CAST(vbtyp AS VARCHAR(1)) AS vbtyp  -- NOVO: categoria SD do documento (M/O/S/N/...)
    FROM {{ ref('vbrk') }}
),
```

**2.** Trocar o cálculo de `Valor_Liquido_Faturamento` (linha 299) e tudo que deriva dele
(`_BRL`, margem, unitário — linhas 299-322) pra usar o valor com sinal:

```sql
-- Sinal do documento (achado 2026-09-09, ver docs/PROPOSTA_CORRECAO_SINAL_FATURAMENTO_SAP.md
-- no invest_sap): VBRK.VBTYP='O' (Gutschrift/nota de crédito) vem com NETWR positivo por
-- convenção SAP — o sinal contábil real nasce da categoria do documento, não do valor
-- gravado. Sem essa correção, estorno soma como faturamento novo em vez de subtrair.
CASE WHEN TRIM(vbrk.vbtyp) = 'O' THEN -1 ELSE 1 END * COALESCE(vbrp.netwr, 0)
    AS "Valor_Liquido_Faturamento",
...
CASE
    WHEN TRIM(vbrk.waerk) = 'BRL' THEN CASE WHEN TRIM(vbrk.vbtyp) = 'O' THEN -1 ELSE 1 END * COALESCE(vbrp.netwr, 0)
    WHEN tc.taxa_brl IS NOT null THEN CASE WHEN TRIM(vbrk.vbtyp) = 'O' THEN -1 ELSE 1 END * COALESCE(vbrp.netwr, 0) * tc.taxa_brl
END AS "Valor_Liquido_Faturamento_BRL",
```

(mais limpo: calcular `valor_liquido_com_sinal` como CTE/coluna intermediária uma vez, reusar
nos 3 lugares — `Valor_Liquido_Faturamento`, `Valor_Liquido_Faturamento_BRL`,
`Valor_Margem_Contribuicao_Bruta`, `Valor_Unitario_Faturado` — em vez de repetir o `CASE`.)

**3.** Considerar expor `vbrk.vbtyp` como coluna própria no Gold
(`"Categoria_Documento_Faturamento"`) além de já usar pro sinal — deixa auditável pra quem
consumir, mesmo padrão de `Tipo_Documento_Faturamento` hoje.

## O que fica em aberto — NÃO mergear sem isso

- **`vbtyp='O'` está confirmado** (1 caso rastreado ponta a ponta + padrão em 20.860 linhas,
  7 tipos de documento). Aplicar sinal negativo pra esse é seguro.
- **`vbtyp='S'` (Rechnungsstorno) não foi validado com um caso concreto** — só a distribuição
  agregada (2.274 pos / 73 neg, mesmo padrão suspeito de `O`). Por definição SAP, estorno de
  fatura também deveria reduzir receita — mas sem rastrear 1 caso de verdade (mesmo pedido,
  antes/depois), não dá pra confirmar com a mesma certeza de `O`.
- **`vbtyp='N'` não foi investigado** — hipótese (não confirmada) é "estorno de nota de
  crédito", que devolveria ao sinal positivo (reverte o `O`) — se for isso, `N` não precisa de
  correção nenhuma, já está certo como está.
- **`5`/`6`** — volume baixo (97/535 linhas no total histórico), não identificados, não
  bloqueiam a correção principal mas merecem 1 linha de investigação antes do PR.

Recomendação: aplicar a correção pra `vbtyp='O'` (confirmado), documentar `S`/`N`/`5`/`6`
como achado aberto no mesmo commit, sem tentar resolver os 4 na mesma tacada.

## Impacto — magnitude

Histórico completo de `vbtyp='O'`: **R$281,1 milhões** somados como positivo hoje. Se a
correção for aplicada, o efeito na métrica de faturamento é o dobro disso (deixa de contar
como soma, passa a subtrair) — na ordem de **R$560 milhões** de diferença acumulada,
histórico completo, na métrica "Faturamento Total" que hoje soma
`Valor_Liquido_Faturamento` sem filtro de tipo de documento
(`scripts/query_faturamento_comercial.py`, de propósito).

## Quem mexe em quê

- **Confirmação de negócio** (`vbtyp='S'`/`N`): time comercial/financeiro, ou consulta rápida
  à transação SAP GUI que mostra o texto do domínio `VBTYP` (não precisa acesso a tabela nova
  — é texto de domínio fixo do SAP, não config específica deste cliente).
- **Patch no dbt** (`vbrk_src` + cálculo de `Valor_Liquido_Faturamento`/`_BRL`/margem/
  unitário): time de dados, repo `data-platform`
  (`airflow/dags/dbt/models/gold/vendas_sap/fct_faturamento_itens_sap/
  fct_faturamento_itens_sap.sql`) — **nenhuma mudança de ingestão Bronze/Silver necessária**,
  `vbrk.vbtyp` já existe na Silver hoje.
- **Depois de implementado**: atualizar `CONTEXTO_VENDAS_SAP.md` §6/§10 com o achado
  fechado, e conferir se `scripts/query_faturamento_comercial.py`/`query_vendas_sap.py` no
  `invest_sap` precisam de ajuste (provavelmente não — eles consomem
  `Valor_Liquido_Faturamento` como está, a correção fica transparente pra eles).
