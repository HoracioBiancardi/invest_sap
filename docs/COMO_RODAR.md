# Como rodar os scripts (`invest_sap/scripts`)

> Guia prático de setup e uso dos scripts deste projeto. Para contexto sobre o que cada
> tabela/model significa, veja **`CONTEXTO_VENDAS_SAP.md`**. Para o histórico da
> investigação que motivou vários desses scripts (e exemplos reais de uso), veja
> **`INVESTIGACAO_PENDENCIA_SAP.md`**. Para o dashboard visual (Streamlit), veja §10.

## 1. Setup

```bash
uv sync
```

Isso já resolve e instala tudo (runtime + grupo `dev`) — não precisa de nenhum
`uv pip install` manual depois. `pyproject.toml` foi convertido em 2026-08-24 do formato
Poetry legado (`[tool.poetry.dependencies]`, que `uv sync` ignora) para `[project.dependencies]`
(PEP 621), que `uv sync` entende nativamente. `[tool.uv] package = false` porque este
projeto é só scripts/notebooks, não uma biblioteca instalável — sem isso o `uv sync`
tentaria buildar "blau" como pacote.

`snowflake-connector-python` foi removido do `pyproject.toml` original (era herdado do
`data-platform`, mas nada aqui usa Snowflake) porque sua última versão trava em
`pandas<3.0.0`, e o projeto pede `pandas>=3.0.1` — conflito real de dependências, não só de
formato. Esse conflito nunca tinha aparecido antes porque o `uv sync` com o pyproject.toml
antigo (Poetry) simplesmente não instalava as deps de runtime, então ninguém tinha
resolvido essa árvore de fato. Se algum dia precisar de Snowflake aqui, vai precisar
rebaixar o pandas ou esperar uma versão do connector compatível com pandas 3.x.

Driver `ODBC Driver 18 for SQL Server` precisa estar instalado no SO (`odbcinst -q -d` pra
conferir) — neste ambiente já está.

## 2. Credenciais (produção)

Vêm do `.env` na raiz do projeto (**nunca commitar valores, nunca colar em chat/PR**). Duas
origens:

- **SQL Server (BRONZE/SILVER/GOLD)**: `SQLSERVER_HOST`, `SQLSERVER_PORT`,
  `SQLSERVER_USER`, `SQLSERVER_PASSWORD` — mesma instância, banco muda por parâmetro
  (`database="GOLD"`, `"SILVER"` ou `"BRONZE"`).
- **SAP HANA/Datasphere**: `HANA_ADDRESS`, `HANA_PORT`, `HANA_USER`, `HANA_PASSWORD`,
  `DDIC_SCHEMA` (schema padrão, = `IB_SAPECC`), `DDIC_LANGUAGE` (= `P`, português).

Todos os scripts abaixo usam `scripts/db.py`, que já lê essas variáveis — não escreva
lógica de conexão nova, importe daí.

## 3. `scripts/db.py` — módulo base

Funções pra importar em qualquer script/notebook novo:

- `get_sqlserver_engine(database="GOLD")` — engine SQLAlchemy (pyodbc).
- `read_sql(query, database="GOLD", params=None)` — roda uma query e retorna
  `pandas.DataFrame`. Use `params` (bind nomeado `:nome`) em vez de f-string quando o valor
  vier de fora do código.
- `get_hana_connection(schema=None)` — conexão `hdbcli` direta (não suporta `with`, feche
  com `.close()`).
- `read_hana_sql(query, schema=None)` — roda uma query no HANA e retorna DataFrame.

## 4. `scripts/check_connections.py`

Roda `SELECT 1`/versão em BRONZE/SILVER/GOLD e no HANA, e confere se as 8 tabelas-chave de
`vendas_sap` existem e têm linhas.

```bash
uv run python scripts/check_connections.py
```

Rodar primeiro sempre que algo parecer "sem dados" — descarta problema de credencial/rede
antes de suspeitar do SQL da análise.

## 5. `scripts/query_vendas_sap.py`

Funções prontas, todas retornam `pandas.DataFrame`:

- `pendencias_abertas(limit=None)`
- `aging_pendencias()` — backlog por faixa de dias
- `pendencia_status_estoque()` — backlog por cobertura de estoque
- `top_clientes_pendentes(n=20)`
- `alocacao_virtual_fifo(codigo_centro=None)`
- `faturamento_periodo(data_inicio, data_fim)`
- `credito_disponivel_clientes(apenas_bloqueados=False)`
- `faturamento_por_org_vendas_linha_negocio(data_inicio=None, data_fim=None, tipo_cliente=None)`
- `meta_vs_realizado_mensal(data_inicio=None, data_fim=None, bu=None)`

```bash
uv run python -c "from scripts.query_vendas_sap import aging_pendencias; print(aging_pendencias())"
```

## 6. `scripts/ddic_lookup.py`

Explica o que é uma tabela/campo SAP direto do dicionário de dados (DDIC — `DD02T`/`DD03L`
via HANA), sem precisar abrir o SAP GUI ou perguntar pro time funcional.

```bash
uv run python scripts/ddic_lookup.py VBAK --campo AUART
uv run python scripts/ddic_lookup.py VBRK
```

## 7. `scripts/trace_pedido.py`

Dado um número de pedido, busca nas 3 camadas de uma vez e imprime tudo lado a lado:

1. SAP cru via HANA (`VBAK`/`VBAP`)
2. Gold `vendas_sap` (`fct_vendas_itens_sap`, `fct_pendencia_sap`, `fct_vendas_canceladas_sap`)
3. Salesforce (`Opportunity`/`OpportunityLineItem`) — ver `CONTEXTO_VENDAS_SAP.md` §8 pro
   funcionamento do elo Opportunity→Pedido

```bash
uv run python scripts/trace_pedido.py 137490
uv run python scripts/trace_pedido.py 137490 --item 10
```

Exemplo real de uso e o que os resultados significaram: `INVESTIGACAO_PENDENCIA_SAP.md` §5.

## 8. `scripts/audit_pendencia_flow.py`

Varre o fluxo inteiro (não um pedido específico) procurando padrões de anomalia, sem
precisar já saber qual pedido está quebrado. 4 checagens independentes:

| Checagem | O que detecta | Onde roda |
|---|---|---|
| `valor_sem_quantidade` | Linha com valor > 0 e quantidade = 0 | `fct_vendas_itens_sap`, `fct_vendas_canceladas_sap`, `fct_faturamento_itens_sap`, `vendas.dim_pendencia` |
| `pendencia_escondida` | `Status_Pendencia='Concluido'` com valor > 0 mas **zero** remessa e **zero** fatura — não depende de saber a causa raiz | `fct_pendencia_sap` |
| `reconciliacao_contagem` | Perda de linhas inteiras entre SAP cru e Gold (join quebrado), por tipo de pedido | `VBAP`/`VBAK` (HANA) vs `fct_vendas_itens_sap` |
| `integridade_dimensoes` | % de linhas com join de dimensão falho (`NULL`) — dimensão desatualizada ou chave divergente | `fct_pendencia_sap` × `dim_cliente_sap`/`dim_centro_sap`/`dim_material_sap` |

```bash
uv run python scripts/audit_pendencia_flow.py                                    # roda tudo
uv run python scripts/audit_pendencia_flow.py --checks valor_sem_quantidade,pendencia_escondida
```

Resultados da primeira rodada (2026-08-24) e o que eles significaram:
`INVESTIGACAO_PENDENCIA_SAP.md` §7. Vale rodar de novo depois de qualquer deploy em
`GOLD.vendas_sap`/`GOLD.vendas` pra conferir se alguma checagem regrediu ou zerou.

## 9. Dashboard visual (Streamlit)

Uso pessoal, local — não é hospedado nem multiusuário (ver decisão de escopo na
conversa que motivou isso: só uma pessoa acessa, então dashboard local resolve sem
precisar lidar com autenticação/hospedagem de credenciais de produção). Cada página é
uma casca fina em cima dos módulos de `scripts/` — não duplica SQL, só troca
`print()`/tabela de texto por uma tela com tabelas e gráficos.

```bash
uv run streamlit run app.py
```

Abre em `http://localhost:8501`. Ctrl+C no terminal encerra o servidor.

`app.py` não tem conteúdo próprio (2026-08-25) — é só um router (`st.navigation`) que monta
o menu lateral em 3 seções + o filtro global (ver §9.1). O conteúdo de cada página vive em
`pages/*.py`. Tema visual em `.streamlit/config.toml` (cor/fonte — ver §9.2).

A separação entre **📊 Dashboards** e **💰 Faturamento (Painel Vendas)** não é estética: são
duas *consultas* diferentes sobre a mesma fonte (`vendas_sap.fct_faturamento_itens_sap`) —
Dashboards soma o total bruto (sem recorte comercial); Faturamento (Painel Vendas) passa pelo
crosswalk cliente→setor (`scripts/query_faturamento_comercial.py`, ~52% de cobertura) pra
poder quebrar por Canal/Divisional/Regional/Distrital/Setor. Ver `CONTEXTO_VENDAS_SAP.md`
§10 — inclusive o histórico de por que uma versão anterior usava `vendas.fat_faturamento`
(schema legado) e foi descartada. Ao criar uma página nova, decida a seção pela consulta que
ela usa, não pelo tema de negócio.

**📊 Dashboards** — sem botão de "Buscar": a consulta roda direto ao mudar qualquer filtro
(resultado cacheado 5 min por combinação de parâmetro via `st.cache_data`, pra não bater no
banco de novo se você voltar pro mesmo filtro).

| Página | Reusa | O que mostra |
|---|---|---|
| `pages/0_Home.py` | `scripts/query_vendas_sap.py` | **Visão executiva** (2026-08-25): KPIs gerais (valor pendente, faturado no mês, valor em estoque, % backlog 60+ dias), evolução do faturamento nos últimos 12 meses, e "Pontos de atenção" calculados ao vivo (aging concentrado, backlog sem estoque, clientes bloqueados por crédito, devoluções recentes) — pensada pra leitura rápida tipo resumo pra diretoria, sem entrar no detalhe operacional |
| `pages/1_Pendencias.py` | `scripts/query_vendas_sap.py` | Aging do backlog, cobertura de estoque, top clientes, backlog por tipo de ordem de venda — com gráficos de barra. Usa o filtro global de tipo de cliente, mas **não** a janela de dias (backlog precisa mostrar tudo em aberto, inclusive antigo) |
| `pages/5_Jornada_Pedido.py` | `scripts/query_vendas_sap.py` | Oportunidade (Salesforce) → Pedido → Pendência → Fatura numa linha, com filtro global (dias/tipo de cliente) + filtro local por pedido/cliente/backlog aberto; abas de funil de conversão, divergência de valor Oportunidade x Pedido, aging por Governo x Privado e impacto de crédito bloqueado no backlog |
| `pages/6_Estoque.py` | `scripts/query_vendas_sap.py` | 2 abas: **Restrito x Disponível** — Qualidade e Bloqueado quebrados separados (não somados), x Disponível, por Material+Centro, com filtro **Produto Acabado x Não Acabado** (`Tipo_Material` ZFER/ZPFA vs o resto, via `dim_material_sap`); **Validade dos lotes** — faixas Vencido/0-30/31-90/91-180/180+ dias, lotes mais urgentes primeiro. **Não** usa o filtro global — estoque não tem dimensão de cliente/data |
| `pages/7_Credito_Devolucoes.py` | `scripts/query_vendas_sap.py` | Limite/exposição de crédito por cliente + devoluções/abatimentos com motivo em texto livre (fonte `vendas.dim_credito_devolucoes`). Usa o filtro global de tipo de cliente; a janela de dias só vale pra aba de devoluções |
| `pages/8_Faturamento_Org_Vendas.py` | `scripts/query_vendas_sap.py` | Faturamento cruzando Organização de Vendas (SAP) x Linha de Negócio (Estética/Farma/Onco-Hemato/Não Alocado, via crosswalk `vendas.dim_cliente_setor` + `vendas.dim_estrutura`). Usa o filtro global completo (dias + tipo de cliente) |
| `pages/10_Analise_Historica.py` | `scripts/query_vendas_sap.py` | Faturamento, pedidos entrando no funil e devoluções/abatimentos por mês (só essas 3 têm histórico real — backlog/estoque só guardam o estado de hoje). Comparação últimos 12 meses x 12 anteriores. **Não** usa o filtro global (janela própria em meses) |
| `pages/11_Metas.py` | `scripts/query_vendas_sap.py` | Meta (planejamento, `vendas.fat_meta_equipe`) x Realizado (faturamento SAP), por mês x BU, com % de atingimento. Meta é decisão de orçamento — não tem fonte SAP/Salesforce, ver `CONTEXTO_VENDAS_SAP.md` §8.3. Realizado herda a cobertura ~52% do crosswalk cliente→setor; sobra vira BU 'NAO ALOCADO'. **Não** usa o filtro global (janela própria em meses + seletor de BU) |
| `pages/16_Relatorio_Pedidos.py` | `scripts/query_vendas_sap.py` | Quantidade e valor médio de pedido por mês + ranking de pedidos por cliente — "pedido entrando no funil" é conceito de ordem de venda SAP, não de faturamento comercial (por isso fica aqui, não na seção Faturamento). Complementa (não duplica) `pages/10_Analise_Historica.py` |

**💰 Faturamento (Painel Vendas)** — inspirada no Painel Vendas (Power BI) enviado pelo
usuário (2026-08-25), sobre `scripts/query_faturamento_comercial.py` — **mesma fonte**
(`vendas_sap.fct_faturamento_itens_sap`) de 📊 Dashboards, mas passando pelo crosswalk
cliente→setor pra ganhar a quebra comercial (~52% de cobertura — cliente sem match cai em
'NAO ALOCADO'). Ver `CONTEXTO_VENDAS_SAP.md` §10 pro histórico completo (inclusive por que
não bate mais 1:1 com o Painel Vendas de referência que a inspirou). Todas usam o filtro
global de tipo de cliente (proxy via Canal Venda) e têm um expander **"🔍 Filtros de
recorte"** próprio (Canal/Linha de Negócio/Divisional/Regional/Distrital/Setor/Família/
Produto/Cliente/Estado/Tipo Documento Faturamento — ver `scripts/ui_filtros_comercial.py`),
que restringe os números da página a um valor específico sem mudar a dimensão do
gráfico/tabela — não confundir com o seletor "Quebrar por", que muda o que aparece nas linhas.

| Página | Reusa | O que mostra |
|---|---|---|
| `pages/12_Faturamento_vs_Meta.py` | `scripts/query_faturamento_comercial.py` | Gauges MTD/YTD, evolução diária/mensal/trimestral, Meta x Realizado por Canal/Linha de Negócio/Divisional/Regional/Distrital/Setor/Família |
| `pages/13_Faturamento_Diario.py` | `scripts/query_faturamento_comercial.py` | Faturamento do dia/MTD, quebra por dimensão comercial e por Estado (UF) — sempre olha o mês corrente, não o filtro global de dias |
| `pages/14_Faturamento_Anual.py` | `scripts/query_faturamento_comercial.py` | Comparativo YoY (YTD ano corrente x YTD ano anterior x ano anterior inteiro) por dimensão comercial, + top clientes YTD |
| `pages/15_Produto_Cliente.py` | `scripts/query_faturamento_comercial.py` | Faturamento e preço médio por mês, SKUs vendidos/clientes atendidos por mês, ranking mensal (matriz) e média dos últimos 6 meses por Cliente/Família/Produto |
| `pages/17_Relatorio_Analitico.py` | `scripts/query_faturamento_comercial.py` | Detalhe linha a linha (1 linha = 1 item de fatura) com seletor de colunas — a única que não agrega. Consulta mais pesada (join linha a linha via `dim_material_sap` pra "Nome Produto", resolvido em 2 passos — ver `CONTEXTO_VENDAS_SAP.md` §6.10) |
| `pages/17_Relatorio_Analitico.py` | `scripts/query_faturamento_comercial.py` | Detalhe linha a linha (1 linha = 1 item de fatura) com seletor de colunas (`st.multiselect`) — diferente das outras 4, não agrega. Período livre (não é MTD/YTD fixo) |

**🛠️ Técnico** — ferramentas de investigação pontual, mantidas com botão/input porque
precisam de um valor específico (número de pedido, tabela SAP) pra fazer sentido; não tem
"estado padrão" que valha rodar sozinho, e por isso não usam o filtro global.

| Página | Reusa | O que mostra |
|---|---|---|
| `pages/2_Auditoria.py` | `scripts/audit_pendencia_flow.py` | As 4 checagens de §8, com seleção de quais rodar |
| `pages/3_Rastrear_Pedido.py` | `scripts/trace_pedido.py` | Rastreamento de um pedido pelas 3 camadas, em expansores |
| `pages/4_DDIC_Lookup.py` | `scripts/ddic_lookup.py` | Consulta de tabela/campo SAP |
| `pages/9_Conectividade.py` | `scripts/db.py` | Botão de conectividade (SQL Server BRONZE/SILVER/GOLD + HANA) — movida da Home (2026-08-25), é diagnóstico técnico, não KPI de negócio |

### 9.1 Filtro global (sidebar)

`app.py` renderiza 2 widgets no sidebar, acima do menu de navegação, com `key` fixa:
`st.session_state["flt_dias"]` (número, default 30) e `st.session_state["flt_tipo_cliente"]`
(`"Todos"`/`"Governo"`/`"Privado"`, default `"Todos"`). Como `session_state` persiste entre
páginas na mesma sessão, qualquer página em `pages/*.py` pode ler esses valores direto:

```python
dias = st.session_state.get("flt_dias", 30)
tipo_cliente_opcao = st.session_state.get("flt_tipo_cliente", "Todos")
tipo_cliente = None if tipo_cliente_opcao == "Todos" else tipo_cliente_opcao
```

Nem toda página usa os dois — Estoque não usa nenhum (sem dimensão de cliente/data),
Pendências usa só tipo de cliente (dias esconderia backlog antigo, que é o mais importante
de ver). Antes de aplicar o filtro global numa página nova, pense se `dias` faz sentido pro
que ela mostra — não é automático.

`tipo_cliente` chega até `scripts/query_vendas_sap.py` via dois helpers reusados por várias
funções: `_filtro_dias_tipo_cliente` (quando a query já tem a chave composta de
`dim_cliente_sap` disponível, ex. `fct_pendencia_sap`) e `_condicao_tipo_cliente_por_codigo`
(quando só se tem `Codigo_Cliente` solto, ex. `fct_limite_credito_sap`,
`vendas.dim_credito_devolucoes`, `fct_faturamento_itens_sap` — agrega por
`MAX(canal=governo)` pra não gerar fanout contra o grão real de `dim_cliente_sap`, que é
Cliente+OrgVendas+Canal+Setor).

### 9.2 Tema visual

`.streamlit/config.toml` define um tema escuro consistente pra todas as páginas — não
precisa (e não deve) repetir CSS inline por página. Paleta extraída do CSS público de
**blaumotorsport.com.br** em 2026-08-25 (`#26b4e9` ciano de destaque, `#2f343c` painel
escuro do `.navbar-inverse`, fonte "Roboto"); o ciano bate com a cor de marca do site
institucional blau.com (`#36b3e3`), confirmando que é a cor real da Blau em toda a empresa,
não só do time de motorsport. Sidebar usa o `#2f343c` (a cor real da navbar do site) pra um
contraste sutil com o conteúdo principal, que fica um pouco mais escuro (`#1C1F26`). Pra
mudar a paleta, edite só esse arquivo — chaves disponíveis (inclusive `[theme.sidebar]`
separado) documentadas em `streamlit/config.py` do pacote instalado.

Cada página faz consulta **ao vivo** em produção — não é um snapshot estático. Como os
scripts de `query_vendas_sap.py` e `audit_pendencia_flow.py` já retornam `pandas.DataFrame`,
e `trace_pedido.py` retorna um dict `{titulo: DataFrame}`, adicionar uma página nova é só
importar a função e chamar `st.dataframe(df)` — não precisa reescrever a lógica de consulta.
Pra adicionar aos Dashboards, registrar em `app.py` (dentro de `st.navigation`) e envolver a
chamada da função num wrapper `@st.cache_data` local à página, do jeito que as páginas
5/6/7/8 já fazem — mantém `scripts/` livre de import de `streamlit` (reusável em CLI/notebook).

## 10. Comandos rápidos (cheat sheet)

```bash
# Setup
uv sync

# Sanity check de conexão (rodar sempre primeiro)
uv run python scripts/check_connections.py

# Rastrear um pedido específico pelas 3 camadas
uv run python scripts/trace_pedido.py <numero_pedido>

# Auditoria geral do fluxo
uv run python scripts/audit_pendencia_flow.py

# Consulta rápida no DDIC
uv run python scripts/ddic_lookup.py <TABELA> [--campo <CAMPO>]

# Análise ad hoc em Python
uv run python -c "from scripts.query_vendas_sap import aging_pendencias; print(aging_pendencias())"

# Dashboard visual
uv run streamlit run app.py
```
