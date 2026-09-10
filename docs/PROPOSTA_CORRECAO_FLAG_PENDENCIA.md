# Proposta — Corrigir falso-positivo de `Flag_Pendencia`/`Status_Pendencia_Estoque` (itens já faturados) em `fct_pendencia_sap`

> Spec pra levar ao time de dados propor no repo `data-platform`
> (`/home/swordpower/Documentos/REPO/TRABALHO/data-platform`). Documento separado de
> `REGRAS_E_MELHORIAS_DW.md` §3.1 (que registra o achado e a proposta em prosa) porque este
> aqui é a proposta de implementação — campo real, distribuição medida, patch de código —
> pronta pra copiar/colar e ajustar, mesmo formato de
> `docs/PROPOSTA_CORRECAO_SINAL_FATURAMENTO_SAP.md`.
>
> **Status (2026-09-10): patch aplicado** no `data-platform`
> (`airflow/dags/dbt/models/gold/vendas_sap/fct_pendencia_sap/fct_pendencia_sap.sql` +
> `.yml`), branch `feature/prioridade-pedido-carimbado-sap`, ainda **não commitado/sem
> deploy** — dbt não rodou em produção com o patch, então o teste de regressão
> antes/depois descrito abaixo ainda não foi executado. Não confiar no `GOLD` atual como
> "já corrigido" até isso rodar.

## Contexto

`GOLD.vendas_sap.fct_pendencia_sap` marca `Flag_Pendencia=1` (e
`Status_Pendencia_Estoque='Pendente sem Estoque'`) em itens que já estão
`Flag_Totalmente_Faturado=1` — logicamente contraditório: o pedido já foi 100%
faturado/creditado, mas o modelo continua contando como backlog em aberto.

Achado quantificado em 2026-09-03 (reconfirmado contra o código atual em 2026-09-09):
**23.981 dos 46.132 itens "pendentes" (52%), 141,7 milhões de unidades (84,5% de toda a
quantidade pendente da base)** caem nessa contradição. Todos têm
`Status_Pendencia='Pendente Logistico (Remessa)'` e `Valor_Pendente_Faturamento=0` — por
isso os KPIs em R$ não eram afetados, só as métricas de quantidade/backlog.

Descoberto ao vivo pelo usuário testando a página Pendência x Estoque, com 3 pedidos reais de
devolução (`0060008372`, `0060009216`, `0060011929`, todos tipo `ZREB`/`ZROB`) que ele sabia
de cor que já estavam resolvidos.

## Achado — `qtd_pendente_operacional` toma o `MAX()` entre remessa e faturamento, não o `MIN()`

Em `fct_pendencia_sap.sql`, CTE `base_consolidada` (linhas ~147-152):

```sql
(SELECT MAX(x.saldo) FROM (
    VALUES
    (v.qtd_pedida - COALESCE(r.qtd_remetida, 0)),
    (v.qtd_pedida - COALESCE(f.qtd_faturada, 0)),
    (CAST(0 AS DECIMAL(15, 3)))
) AS x (saldo)) AS qtd_pendente_operacional
```

A lógica pega o **maior** saldo pendente entre "quanto falta remeter" e "quanto falta
faturar" — ou seja, um item só é considerado concluído quando **os dois** canais (remessa E
faturamento) chegam a zero. Isso é uma armadilha pra pedidos que, por tipo de ordem, nunca
populam `Qtd_Remetida` de verdade — típico de devolução (`Tipo_Ordem_Venda` `ZREB`, `ZROB`,
`ZRSG`, `ZRES`, `ZRET`, `ZDV1`, `ZBON`, etc., e também alguns tipos "normais" como `ZVCO`,
`ZIND`, `ZDES`, `UVCO`, `UNCR`, `UDEV`). Pra esses, `qtd_remetida` fica em `0` pra sempre,
então `(qtd_pedida - qtd_remetida)` nunca zera — e como o cálculo usa `MAX`, esse saldo
"fantasma" de remessa domina mesmo depois do item estar 100% faturado
(`qtd_faturada >= qtd_pedida`, ou seja `f.qtd_faturada - v.qtd_pedida >= 0`).

`Flag_Pendencia`, `Status_Pendencia` e `Status_Pendencia_Estoque` (linhas 253-268) são todos
derivados de `qtd_pendente_operacional` — corrigir na CTE resolve os 3 de uma vez, sem
precisar tocar cada `CASE` downstream.

Mitigação hoje só no consumo, não na fonte: `pages/27_Pendencia_x_Estoque.py` (`invest_sap`)
já classifica esses casos como `Motivo_Principal = "Falso Positivo (já faturado)"`,
verificado **antes** de qualquer outro motivo:

```python
if row.get("Flag_Totalmente_Faturado") == 1:
    return "Falso Positivo (já faturado)"
```

A proposta abaixo é levar exatamente essa mesma regra (`Flag_Totalmente_Faturado=1` ⇒
resolvido) pro `dbt`, em vez de deixar cada consumidor reimplementar a mitigação.

## Proposta de correção

Em
`airflow/dags/dbt/models/gold/vendas_sap/fct_pendencia_sap/fct_pendencia_sap.sql`, CTE
`base_consolidada` (linhas ~147-152), envolver o cálculo existente com um `CASE` que trata
faturamento completo como conclusão, independente do saldo de remessa:

```sql
-- Falso-positivo corrigido (achado 2026-09-03, patch 2026-09-09, ver docs/
-- PROPOSTA_CORRECAO_FLAG_PENDENCIA.md no invest_sap): pedidos de devolução (ZREB/ZROB/...)
-- nunca populam Qtd_Remetida, então o saldo de remessa nunca zera sozinho. Sem esse CASE,
-- MAX() abaixo mantém o item "pendente" pra sempre mesmo 100% faturado/creditado.
CASE
    WHEN COALESCE(f.qtd_faturada, 0) >= v.qtd_pedida THEN CAST(0 AS DECIMAL(15, 3))
    ELSE (SELECT MAX(x.saldo) FROM (
        VALUES
        (v.qtd_pedida - COALESCE(r.qtd_remetida, 0)),
        (v.qtd_pedida - COALESCE(f.qtd_faturada, 0)),
        (CAST(0 AS DECIMAL(15, 3)))
    ) AS x (saldo))
END AS qtd_pendente_operacional
```

A condição (`qtd_faturada >= qtd_pedida`) é exatamente a mesma já usada pra computar
`Flag_Totalmente_Faturado` na linha 269 — reaproveita uma definição que já existe no model,
não introduz conceito novo.

`qtd_pendente_remessa` e `qtd_pendente_faturamento` (colunas de diagnóstico, linhas 137-146,
expostas como `Qtd_Pendente_Remessa`/`Qtd_Pendente_Faturamento`) ficam **intocadas** —
continuam mostrando o saldo por canal individualmente; só o `qtd_pendente_operacional`
consolidado (e o que deriva dele) muda.

## O que fica em aberto — NÃO mergear sem isso

- **Teste de regressão antes/depois**: comparar `SUM(Qtd_Pendente_Operacional)` e contagem de
  `Flag_Pendencia=1` antes/depois do patch, no padrão do que já foi feito pro fix de
  `KWMENG` (`scripts/audit_pendencia_flow.py` no `invest_sap`, ver
  `docs/INVESTIGACAO_PENDENCIA_SAP.md`). Esperado: queda de ~141,7 milhões de unidades em
  `Qtd_Pendente_Operacional` e de ~23.981 itens em `Flag_Pendencia=1`, concentrada nos tipos
  de ordem de devolução listados acima.
- **Não confundir com pendência real**: existe pelo menos 1 caso confirmado que **não** é
  falso-positivo — pedido `0000134668`/item `000120` (material `PA8116`, tipo `ZPRI`) tem
  `Flag_Totalmente_Faturado=0` genuinamente (outros itens do mesmo pedido foram enviados no
  mesmo dia, só esse ficou pra trás — material oncológico/cadeia fria, pode ter handling
  especial não capturado nos dados). O patch não afeta esse caso porque a condição só dispara
  quando `qtd_faturada >= qtd_pedida` — mas vale conferir esse pedido especificamente
  depois do deploy pra confirmar que ele continua aparecendo como pendente.
- **Efeito em cascata no app**: depois do patch, a mitigação client-side em
  `pages/27_Pendencia_x_Estoque.py::_classificar_motivo_principal` (categoria "Falso Positivo
  (já faturado)") deixa de encontrar casos — pode ser simplificada/removida depois de
  confirmar em produção, mas isso é limpeza no `invest_sap`, tarefa separada e não bloqueia
  este patch.

## Impacto — magnitude

**23.981 de 46.132 itens "pendentes" (52%)** e **141,7 milhões de unidades (84,5% de toda a
quantidade pendente medida)** deixam de aparecer como backlog em aberto. `Valor_Pendente_Faturamento`
já é `0` nesses casos hoje (não muda) — o impacto é só nas métricas de **quantidade/backlog**
(`Qtd_Pendente_Operacional`, `Flag_Pendencia`, `Status_Pendencia`, `Status_Pendencia_Estoque`),
não nos KPIs em R$.

## Quem mexe em quê

- **Patch no dbt** (`base_consolidada` em `fct_pendencia_sap.sql`): time de dados, repo
  `data-platform` — nenhuma mudança de ingestão Bronze/Silver necessária, os campos
  (`qtd_remetida`, `qtd_faturada`, `qtd_pedida`) já existem e já são usados no mesmo model.
- **Depois de implementado**: reconferir no `invest_sap` se
  `pages/27_Pendencia_x_Estoque.py::_classificar_motivo_principal` ainda encontra casos de
  "Falso Positivo (já faturado)" (esperado: não, ou muito poucos) e simplificar a função se
  confirmado; atualizar `docs/REGRAS_E_MELHORIAS_DW.md` §3.1/§1.1.1 com o achado fechado.
