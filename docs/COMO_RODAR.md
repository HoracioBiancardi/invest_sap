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

### 2.1 Cofre de credenciais (recomendado)

Pra não deixar essas credenciais em texto puro no `.env`, elas podem ir pro **cofre** no banco
local do app (`data/app.db`, tabela `vault`) — `scripts/credential_vault.py`, porte do
`crypto_vault_service` do app_template: Fernet + PBKDF2-HMAC-SHA256 (600.000 iterações, salt
aleatório), com uma **senha mestra** (mín. 10 caracteres, força Forte) que não é gravada em
lugar nenhum.

1. Admin → aba **Cofre** → os campos vêm preenchidos com o `.env` atual → defina a senha
   mestra → **Criar cofre**.
2. Apague do `.env` as variáveis `HANA_*`, `DDIC_SCHEMA` e `SQLSERVER_*` (a aba lista as que
   ainda estão lá) e reinicie o app.
3. A cada reinício do app, o cofre sobe **bloqueado**: o admin vê a tela "Cofre de credenciais
   bloqueado" e digita a senha mestra; leitores veem um aviso até isso acontecer. A página
   Admin fica acessível mesmo com o cofre bloqueado.

Regras: cofre configurado → o `.env` é **ignorado** para essas chaves; sem cofre → `.env`, como
antes. Nos CLIs de `scripts/` (terminal interativo) a senha mestra é pedida via `getpass`.
5 tentativas erradas bloqueiam o desbloqueio por 60s. Admin → Cofre também atualiza
credenciais (senha vazia = manter), troca a senha mestra, bloqueia na hora e, se a senha mestra
se perder, **apaga** o cofre para recadastrar. Eventos vão pro log de auditoria (`cofre_*`).

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

`app.py` não tem conteúdo próprio — é só um router (`st.navigation`) que monta o menu
lateral em 6 grupos (reorganizado em 2026-09-05, ver docstring de `app.py`); o filtro de
Período/Tipo de cliente não mora mais aqui, foi movido pra dentro de cada página que usa
(ver §9.1). O conteúdo de cada página vive em `pages/*.py`. Tema visual em
`.streamlit/config.toml` (cor/fonte — ver §9.2).

**Histórico da reorganização**: as páginas atuais nasceram fundindo/reduzindo páginas mais
antigas (algumas citadas ainda em `docs/CONTEXTO_VENDAS_SAP.md`/`docs/REGRAS_E_MELHORIAS_DW.md`
com o nome/número antigo — ex. `1_Pendencias.py`, `3_Rastrear_Pedido.py`, `4_DDIC_Lookup.py`,
`5_Jornada_Pedido.py`, `8_Faturamento_Org_Vendas.py`, `9_Conectividade.py`,
`10_Analise_Historica.py`, `16_Relatorio_Pedidos.py` **não existem mais como página** — o
conteúdo foi absorvido pelas páginas atuais, ver a coluna "Reusa"/docstring de cada uma
abaixo). Se um documento mais antigo citar um desses nomes, o conteúdo equivalente hoje está
em `20_Pedidos.py` (Pendências + Relatório de Pedidos + Rastrear Pedido),
`19_Oportunidade.py` (Jornada do Pedido), `22_Faturamento.py` (Faturamento Org Vendas +
parte de Análise Histórica) ou não tem mais página Streamlit (`ddic_lookup.py`/`db.py`
seguem como CLI, ver §6/§4).

A separação entre **Funil de Vendas** (+ Metas e Performance/Cadastros) e **Faturamento
(Painel Vendas)** não é estética: são duas *consultas* diferentes sobre a mesma fonte
(`vendas_sap.fct_faturamento_itens_sap`) — Funil de Vendas soma o total bruto (sem recorte
comercial); Faturamento (Painel Vendas) passa pelo crosswalk cliente→setor
(`scripts/query_faturamento_comercial.py`, ~52% de cobertura) pra poder quebrar por
Canal/Divisional/Regional/Distrital/Setor. Ver `CONTEXTO_VENDAS_SAP.md` §10 — inclusive o
histórico de por que uma versão anterior usava `vendas.fat_faturamento` (schema legado) e
foi descartada. Ao criar uma página nova, decida o grupo pela consulta que ela usa, não pelo
tema de negócio.

Sem botão de "Buscar" na maioria: a consulta roda direto ao mudar qualquer filtro (resultado
cacheado 5 min por combinação de parâmetro via `st.cache_data`, pra não bater no banco de
novo se você voltar pro mesmo filtro).

**Home** (sem seção, primeira da navegação):

| Página | Reusa | O que mostra |
|---|---|---|
| `pages/0_Home.py` | `scripts/query_vendas_sap.py` + `scripts/query_faturamento_comercial.py` | Visão executiva em 2 blocos lado a lado, sem misturar: **Backlog e Operação** (`vendas_sap`, total bruto) e **Faturamento Comercial** (mesma fonte, via crosswalk cliente→setor, ~52% de cobertura) — mesma tabela fonte, escopo/consulta diferente, por isso os totais não somam entre os blocos. Resumo rápido tipo "pra diretoria", sem detalhe operacional |

**Funil de Vendas** — segue a ordem cronológica do pedido (Oportunidade → Pedido →
Pendência/Estoque → Remessa → Faturamento → Crédito e Devoluções):

| Página | Reusa | O que mostra |
|---|---|---|
| `pages/19_Oportunidade.py` | `scripts/query_vendas_sap.py::correlacao_oportunidade_pedido_pendencia_fatura` | Funil Oportunidade (Salesforce) → Pedido → Pendência → Fatura, agregado em pandas pra visão de portfólio (funil por estágio, conversão, aging) |
| `pages/20_Pedidos.py` | `scripts/query_vendas_sap.py` (`aging_pendencias`/`pendencia_status_estoque`/`pendencia_por_tipo_ordem_venda`/`top_clientes_pendentes`/`pedidos_mensal`/`pedidos_por_cliente`) + `scripts/trace_pedido.py` | Funde 3 páginas antigas: visão geral do backlog (aging, cobertura de estoque, top clientes, tipo de ordem), volume/valor médio de pedido por mês + ranking por cliente, e busca de 1 pedido específico pelas 3 camadas (SAP cru + Gold + Salesforce). Inclui o "Radar de pedido zumbi" (backlog antigo sem reserva viva no SAP) |
| `pages/27_Pendencia_x_Estoque.py` | `scripts/query_vendas_sap.py::pendencia_x_estoque_global` | Visão global do backlog aberto: classifica cada item num `Motivo_Principal` real (Falso Positivo já faturado / Sem Estoque / Estoque Parcial / Financeiro-Crédito / Fiscal-Faturamento / Logístico-Remessa), quebra por Organização de Vendas, com drill-down até o estoque real na data do pedido (via `IB_SAPECC.MCHBH`) |
| `pages/6_Estoque.py` | `scripts/query_vendas_sap.py::estoque_restrito_disponivel`/`estoque_validade_resumo`/`estoque_validade` | 2 abas: Restrito x Disponível (Qualidade/Bloqueado separados, por Material+Centro, filtro Produto Acabado x Não Acabado) e Validade dos lotes (faixas Vencido/0-30/31-90/91-180/180+ dias) |
| `pages/21_Remessas.py` | `scripts/query_vendas_sap.py::remessas`/`remessas_resumo` | Volume (quantidade/peso) e data real de saída por remessa — os 4 campos de status SAP (`Wbsta`/`Lfgsa`/`Lvsta`/`Fksta`) estão 100% NULL nesta base, não dá pra filtrar/segmentar por eles |
| `pages/22_Faturamento.py` | `scripts/query_vendas_sap.py::faturamento_por_org_vendas_linha_negocio`/`faturamento_mensal`/`devolucoes_mensal` | Visão executiva resumida: total bruto de `vendas_sap` cruzando Organização de Vendas x Linha de Negócio, mais faturamento/devoluções mensais. Diferente de Faturamento (Painel Vendas): aqui não passa pelo crosswalk cliente→setor |
| `pages/7_Credito_Devolucoes.py` | `scripts/query_vendas_sap.py::credito_disponivel_clientes`/`devolucoes_credito_motivo` | Limite/exposição de crédito por cliente + devoluções/abatimentos com motivo em texto livre (fonte `vendas.dim_credito_devolucoes`) |

**Metas e Performance** — acompanhamento, não fluxo de pedido:

| Página | Reusa | O que mostra |
|---|---|---|
| `pages/11_Metas.py` | `scripts/query_vendas_sap.py::meta_vs_realizado_mensal` | Meta (planejamento, `vendas.fat_meta_equipe`) x Realizado (faturamento SAP), por mês x BU, com % de atingimento. Realizado herda a cobertura ~52% do crosswalk cliente→setor; sobra vira BU 'NAO ALOCADO' |
| `pages/18_Visao_Vendedor.py` | `scripts/query_vendas_sap.py::faturamento_por_vendedor`/`faturamento_vendedor_mensal`/`top_clientes_por_vendedor` | Ranking e drill-down individual de faturamento por vendedor. `Codigo_Vendedor` só é confiável quando `Origem_Vendedor='SALESFORCE'` (~82% dos itens, medido 2026-08-26) — a origem SAP está sempre vazia em produção |
| `pages/24_Vendedor_x_Meta.py` | `scripts/query_vendas_sap.py::faturamento_vendedor_com_meta_bu` | Faturamento real por vendedor ao lado do atingimento de meta da BU dele — não existe meta oficial por vendedor na base, só por Setor/BU |

**Cadastros** — consulta pontual de dimensão:

| Página | Reusa | O que mostra |
|---|---|---|
| `pages/25_Cliente_360.py` | `scripts/query_vendas_sap.py::cliente_360` | Pedido/pendência/fatura + crédito + devoluções, tudo por cliente — busca por `Codigo_Cliente` |
| `pages/23_Material.py` | `scripts/query_vendas_sap.py::materiais_catalogo`/`ficha_material` | Ficha de cadastro do material (`dim_material_sap`): descrição, tipo, status, unidade de medida, peso — sem quantidade/estoque (isso mora em Estoque/Pendência x Estoque) |

**Faturamento (Painel Vendas)** — inspirada no Painel Vendas (Power BI) enviado pelo
usuário (2026-08-25), sobre `scripts/query_faturamento_comercial.py` — **mesma fonte**
(`vendas_sap.fct_faturamento_itens_sap`) do Funil de Vendas, mas passando pelo crosswalk
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
| `pages/12_Painel_Vendas.py` | `scripts/query_faturamento_comercial.py` | 3 abas (fundidas em 2026-09-04, cada 1 com filtro de recorte próprio): **MTD/YTD/Trimestral** — gauges + Meta x Realizado por Canal/Linha de Negócio/Divisional/Regional/Distrital/Setor/Família; **Diário** — dia/MTD + Estado (UF), sempre mês corrente (não o filtro global); **Anual (YoY)** — comparativo YTD ano corrente x ano anterior + top clientes |
| `pages/15_Produto_Cliente.py` | `scripts/query_faturamento_comercial.py` | Faturamento e preço médio por mês, SKUs vendidos/clientes atendidos por mês, ranking mensal (matriz) e média dos últimos 6 meses por Cliente/Família/Produto |
| `pages/17_Relatorio_Analitico.py` | `scripts/query_faturamento_comercial.py` | Detalhe linha a linha (1 linha = 1 item de fatura) com seletor de colunas (`st.multiselect`) — a única que não agrega. Consulta mais pesada (join linha a linha via `dim_material_sap` pra "Nome Produto", resolvido em 2 passos — ver `CONTEXTO_VENDAS_SAP.md` §6.10). Período livre (não é MTD/YTD fixo) |

**Técnico** — ferramentas de investigação pontual, mantidas com botão/input porque
precisam de um valor específico pra fazer sentido; não tem "estado padrão" que valha rodar
sozinho, e por isso não usam o filtro global. Só resta a Auditoria do Fluxo como página
Streamlit hoje — Rastrear Pedido virou aba de `pages/20_Pedidos.py`, e DDIC Lookup/
Conectividade não têm mais página própria (seguem só como CLI, ver §6 e `scripts/db.py`):

| Página | Reusa | O que mostra |
|---|---|---|
| `pages/2_Auditoria.py` | `scripts/audit_pendencia_flow.py` | As 4 checagens de §8, com seleção de quais rodar |

### 9.1 Filtro de Período + Tipo de cliente (local a cada página, não mais no sidebar)

Até 2026-09-04 isso vivia sozinho na sidebar de `app.py` ("filtro global"), afetando 9
páginas sem ficar visível em nenhuma delas — confuso (setava o filtro num lugar, via o
efeito em outro). Agora cada página que usa chama uma função de
`scripts/ui_theme.py` no topo do próprio corpo:

- `render_filtro_periodo_tipo_cliente()` — Período (`st.date_input` com range) + Tipo de
  cliente. Usada por: Oportunidade, Remessas, Faturamento, Vendedor, Crédito e Devoluções,
  Vendedor x Meta x Faturamento.
- `render_filtro_tipo_cliente()` — só Tipo de cliente (a página tem sua própria janela de
  tempo, período não faria sentido). Usada por: Pedidos, Painel Vendas, Produto/Cliente,
  Relatório Analítico.

As duas escrevem nas MESMAS chaves de `st.session_state` (`flt_data_inicio`/`flt_data_fim`/
`flt_tipo_cliente`) — é o mesmo widget/estado compartilhado entre todas as páginas que
chamam uma das duas funções, só renderizado localmente em cada uma, não um filtro isolado
por página. Qualquer página lê os valores do jeito de sempre:

```python
from scripts.ui_theme import render_filtro_periodo_tipo_cliente

render_filtro_periodo_tipo_cliente()  # ou render_filtro_tipo_cliente(), se não usa período
data_inicio = st.session_state.get("flt_data_inicio", datetime.date.today() - datetime.timedelta(days=30))
data_fim = st.session_state.get("flt_data_fim", datetime.date.today())
tipo_cliente_opcao = st.session_state.get("flt_tipo_cliente", "Todos")
tipo_cliente = None if tipo_cliente_opcao == "Todos" else tipo_cliente_opcao
```

Nem toda página usa — Estoque não usa nenhum (sem dimensão de cliente/data). Antes de
aplicar numa página nova, pense se período faz sentido pro que ela mostra — não é
automático (ex.: Pedidos mostra backlog aberto, que não pode esconder pedido antigo por
trás de uma janela de dias).

`tipo_cliente` chega até `scripts/query_vendas_sap.py` via dois helpers reusados por várias
funções: `_filtro_dias_tipo_cliente` (quando a query já tem a chave composta de
`dim_cliente_sap` disponível, ex. `fct_pendencia_sap`) e `_condicao_tipo_cliente_por_codigo`
(quando só se tem `Codigo_Cliente` solto, ex. `fct_limite_credito_sap`,
`vendas.dim_credito_devolucoes`, `fct_faturamento_itens_sap` — agrega por
`MAX(canal=governo)` pra não gerar fanout contra o grão real de `dim_cliente_sap`, que é
Cliente+OrgVendas+Canal+Setor).

### 9.2 Tema visual

Padrão visual SwordPower (app_template / input_arquivos), aplicado em `scripts/ui_theme.py` e
`.streamlit/config.toml`:

- **Paleta base** (`config.toml`) = tema **Corporativo** do template (`#12141A` fundo,
  `#5B8DEF` primária, fonte Inter). É ela que pinta o que CSS não alcança: gráficos
  Vega/Altair (`chartCategoricalColors`), a grade do `st.dataframe` e widgets nativos.
- **Temas** Corporativo (padrão), Verde Neutro, Cyber Dark e Blau (marca: a paleta original
  `#26B4E9`/`#2F343C`/Roboto) — escolhidos em **Configurações** na Topbar (vale pra sessão);
  o padrão de novas sessões fica em Administração → Configurações. Cada tema expõe os tokens
  do template como variáveis CSS (`--primary`, `--surface`, `--border`, `--glow-ring`...).
- **Topbar**: marca (raio + selo SwordPower em SVG) à esquerda; à direita selo de conexão
  (`online` / `cofre bloqueado`), saudação, **Admin**, **Minha senha** (troca com senha atual,
  medidor de força e gerador), **Configurações** e **Sair**. O indicador "Running/Stop" do
  Streamlit vira um selo no canto inferior direito.
- **Componentes**: título de página compacto + legenda; métricas em card (`--surface-alt`, valor
  quebra linha em vez de cortar); `card()` sem faixa/bandeirinha; alertas escuros com borda
  colorida (`.page-alert`); rótulos de campo em maiúsculo pequeno (`.form-label`). Helpers
  reusáveis: `section_header()`, `badge()`, `html_table()`, `nav_card()`.
- Seletores usam `data-testid`/react-aria do Streamlit instalado (1.62) — se o visual "voltar
  ao padrão" depois de um upgrade, conferir os seletores primeiro.

**Administração** (só admin): painel de cards (`pages/90_Admin.py`) → Usuários (91),
Configurações (92), Dados de negócio (93), Cofre de credenciais (94, com "Testar conexão") e
Auditoria (95). Todas acessíveis mesmo com o cofre bloqueado.

Cada página faz consulta **ao vivo** em produção — não é um snapshot estático. Como os
scripts de `query_vendas_sap.py` e `audit_pendencia_flow.py` já retornam `pandas.DataFrame`,
e `trace_pedido.py` retorna um dict `{titulo: DataFrame}`, adicionar uma página nova é só
importar a função e chamar `st.dataframe(df)` — não precisa reescrever a lógica de consulta.
Pra adicionar uma página nova, registrar em `app.py` (dentro de `st.navigation`, no grupo
certo — ver §9) e envolver a chamada da função num wrapper `@st.cache_data` local à página,
do jeito que a maioria das páginas atuais já faz — mantém `scripts/` livre de import de
`streamlit` (reusável em CLI/notebook).

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
