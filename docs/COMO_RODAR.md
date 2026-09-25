# Como rodar os scripts (`invest_sap/scripts`)

> Guia prático de setup e uso dos scripts deste projeto. Para contexto sobre o que cada
> tabela/model significa, veja **`CONTEXTO_VENDAS_SAP.md`**. Para o histórico da
> investigação que motivou vários desses scripts (e exemplos reais de uso), veja
> **`INVESTIGACAO_PENDENCIA_SAP.md`**. Para o dashboard web, veja §8.5 e §9.

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

## 8.5 App web (FastAPI + Jinja + HTMX)

Migração feita em 2026-09-25 (branch `feat/fastapi-htmx`): as 23 páginas do Streamlit foram
portadas para `web/`, com a mesma regra de negócio (a camada `scripts/query_*` é a mesma). O
Streamlit foi removido depois da validação (mesmo dia).

```bash
uv run python -m web                                  # http://127.0.0.1:8000
APP_PORT=8001 uv run python -m web                    # outra porta
uv run pytest tests/test_web.py -v                    # testes do app web (sem DW)
```

**Estrutura**

| Onde | O quê |
|---|---|
| `web/main.py` | Rotas, sessão, CSRF, headers de segurança, gzip, ciclo de render |
| `web/ui.py` | Kit de UI com a semântica do Streamlit (`caption`, `columns`, `metric`, `table`, `tabs`, `selectbox`…) gerando HTML + HTMX |
| `web/views/<pagina>.py` | 1 módulo por página: `render(p, ctx)`, opcionais `BLOCOS` (lazy), `DOWNLOADS`, `AQUECER` |
| `web/views/__init__.py` | Registro de páginas e seções do menu |
| `web/views/_comum.py` | Filtros compartilhados (período/tipo de cliente, recorte comercial, executivo) |
| `web/cache.py` | Cache TTL com single-flight, `em_paralelo`, pré-aquecimento |
| `web/auth.py` | Login, lockout, sessão server-side (tabela `sessions` no `app.db`) |
| `web/static/app.js` | Tabelas (Tabulator), gráficos (ECharts), IndexedDB, eventos de filtro |

**Adicionar uma página:** criar `web/views/nova.py` com `render(p, ctx)` e registrar em
`PAGINAS` (`web/views/__init__.py`). Filtros = widgets do `p` (`p.selectbox(...)` devolve o
valor já lido da URL); consultas ao DW sempre via função decorada com `@cached(ttl=...)`.

**Desempenho (o que mudou em relação ao Streamlit)**

- Mudar filtro troca só o `<main>` (HTMX), sem reexecutar a página no navegador; a URL guarda
  os filtros (link compartilhável).
- Abas de servidor: só a aba aberta consulta o DW.
- Blocos lazy (`p.lazy`): a página abre na hora e as seções pesadas chegam em paralelo;
  detalhe de item (MCHBH), Oportunidade/Remessas e seções da Home/Pedidos são blocos.
- `em_paralelo`: pacotes de consultas independentes rodam juntos (Home: 50s → 13s frio).
- Cache compartilhado entre usuários com single-flight (N pessoas pedindo a mesma consulta
  fria = 1 ida ao DW) e pré-aquecimento das consultas pesadas ao subir/desbloquear o cofre.
- Listas grandes de filtro (6 mil clientes, 1,7 mil produtos) ficam no IndexedDB do
  navegador e só são rebaixadas quando mudam.
- Tabelas e gráficos só são montados quando ficam visíveis na tela.

**Deploy para mais usuários (servidor interno, VPN)**

- **Um processo só** (`workers=1`, já fixo em `python -m web`): cofre desbloqueado, lockout e
  cache vivem na memória do processo. As requisições rodam no threadpool.
- O app escuta em `127.0.0.1`; exponha só por um proxy reverso com HTTPS. Exemplo Caddy:

  ```
  invest.intranet.exemplo {
      reverse_proxy 127.0.0.1:8000
  }
  ```
- `APP_COOKIE_SECURE` fica `1` (padrão) atrás do HTTPS; `0` só para teste local em HTTP fora
  de `localhost`. `APP_FORWARDED_IPS` = IP do proxy (log com IP real).
- Após cada reinício um admin desbloqueia o cofre (a tela aparece sozinha para admin).

**Docker na VM (mesma do `input_arquivos`, `172.16.109.61`)** — mesmo padrão dele:
`Dockerfile` (Python 3.12 + ODBC Driver 18 + fuso `America/Sao_Paulo`, roda sem root) e
`docker-compose.yml` na porta **8005** (a 8004 é do `input_arquivos`).

```bash
git clone git@github.com:HoracioBiancardi/invest_sap.git && cd invest_sap
cp .env.example .env          # preencher; APP_COOKIE_SECURE=0 enquanto o acesso for HTTP direto
mkdir -p data && sudo chown 10001:10001 data
docker compose up -d --build
docker compose logs -f        # 1º start: senha do admin em data/.access_key (se APP_ACCESS_KEY vazia)
```

Acesso: `http://172.16.109.61:8005`. Para o HTTPS, use **um Caddy só para a VM** (o do
`input_arquivos` está pronto e comentado no compose dele): ative-o, ponha os dois containers
na mesma rede Docker e acrescente no Caddyfile dele um site para este app, por exemplo:

```
{$SITE_ADDRESS}:8443 {
	tls internal
	encode gzip
	reverse_proxy invest-sap:8005
}
```

Depois troque o bind deste compose para `"127.0.0.1:8005:8005"` e volte `APP_COOKIE_SECURE=1`.

**Segurança:** sessão server-side (cookie só com token aleatório, banco guarda o hash),
`HttpOnly`/`SameSite=Strict`/`Secure`, CSRF em todo POST, CSP `script-src 'self'` (bibliotecas
JS servidas localmente em `web/static/vendor`), `Cache-Control: no-store`, HSTS, logout apaga
IndexedDB (`Clear-Site-Data`), desativar/redefinir senha derruba as sessões do usuário na hora,
CSV exportado com proteção contra injeção de fórmula.

## 9. Páginas do app web

Cada página é um módulo de `web/views/` em cima das funções de `scripts/` — não duplica SQL.
O menu tem as mesmas seções de antes; a separação entre **Funil de Vendas** e **Faturamento
(Painel Vendas)** não é estética: são duas *consultas* diferentes sobre a mesma fonte
(`vendas_sap.fct_faturamento_itens_sap`) — Funil soma o total bruto; Painel Vendas passa pelo
crosswalk cliente→setor (`scripts/query_faturamento_comercial.py`, ~52% de cobertura) pra
quebrar por Canal/Divisional/Regional/Distrital/Setor (ver `CONTEXTO_VENDAS_SAP.md` §10). Ao
criar uma página nova, decida a seção pela consulta que ela usa.

| Seção | Página (`/p/<slug>`) | View | Reusa |
|---|---|---|---|
| — | Home (`home`) | `home.py` | `query_vendas_sap` + `query_faturamento_comercial` (Backlog e Operação x Faturamento Comercial, não somam entre si) |
| Funil | Oportunidade (`oportunidade`) | `oportunidade.py` | `correlacao_oportunidade_pedido_pendencia_fatura` |
| Funil | Pedidos (`pedidos`) | `pedidos.py` | aging, cobertura, top clientes, tipo de ordem, volume mensal, ranking, **radar de pedido zumbi** e rastreio de 1 pedido (`trace_pedido.py`) |
| Funil | Pendência x Estoque (`pendencia-estoque`) | `pendencia_estoque.py` | `pendencia_x_estoque_global` → `Motivo_Principal`, ranking por material, drill-down até o estoque real na data do pedido (`IB_SAPECC.MCHBH`) |
| Funil | Estoque (`estoque`) | `estoque.py` | restrito x disponível, validade dos lotes, rastreio de lote (MSEG) |
| Funil | Remessas (`remessas`) | `remessas.py` | `remessas`/`remessas_resumo` (status SAP 100% NULL, sem filtro por eles) |
| Funil | Faturamento (`faturamento`) | `faturamento.py` | Org Vendas x Linha de Negócio + tendência mensal com devoluções |
| Funil | Crédito e Devoluções (`credito-devolucoes`) | `credito_devolucoes.py` | limite/exposição + devoluções com motivo |
| Metas | Faturamento x Meta (`metas`) | `metas.py` | `meta_vs_realizado_mensal` |
| Metas | Vendedor (`vendedor`) | `vendedor.py` | ranking/drill-down (vendedor só confiável na origem Salesforce) |
| Metas | Vendedor x Meta x Faturamento (`vendedor-meta`) | `vendedor_meta.py` | meta da BU, não do vendedor |
| Cadastros | Cliente 360 (`cliente-360`) | `cliente_360.py` | `cliente_360` |
| Cadastros | Material (`material`) | `material.py` | catálogo + ficha (`dim_material_sap`) |
| Painel | Painel Vendas (`painel-vendas`) | `painel_vendas.py` | MTD/YTD/Trimestral vs Meta, Diário, Anual (YoY) |
| Painel | Produto / Cliente (`produto-cliente`) | `produto_cliente.py` | preço médio, SKUs, ranking mensal |
| Painel | Relatório Analítico (`relatorio-analitico`) | `relatorio_analitico.py` | detalhe linha a linha com seletor de colunas |
| Técnico | Auditoria do Fluxo (`auditoria`) | `auditoria.py` | as checagens de §8 |
| Admin | Painel, Usuários, Configurações, Dados de negócio, Cofre, Auditoria (`admin*`) | `admin_*.py` | SQLite do app (`app_db`) + cofre |

### 9.1 Filtros

- **Período + Tipo de cliente** (`web/views/_comum.py::filtro_periodo_tipo_cliente`) aparecem
  dentro de cada página que usa e são **compartilhados na sessão**: o valor escolhido numa
  página vale nas outras (Oportunidade, Faturamento, Vendedor, Crédito e Devoluções,
  Vendedor x Meta; só Tipo de cliente em Pedidos, Painel Vendas, Produto/Cliente, Relatório).
- **Recorte comercial** (`filtros_comercial`): expander com 1 filtro por dimensão; restringe os
  números sem mudar o "Quebrar por". Listas grandes (Cliente, Produto) vêm do IndexedDB.
- Todo filtro vai para a URL — copiar o endereço compartilha a visão filtrada.
- Exclusões padrão (intercompany, Org Vendas CO/UY, estoque internacional, pedido zumbi) são
  configuradas em Admin → Configurações.

### 9.2 Tema visual

4 temas do ecossistema (Corporativo, Verde Neutro, Cyber Dark, Blau), tokens em
`web/static/app.css`. Cada pessoa escolhe no topo (fica num cookie do navegador); o padrão de
quem nunca escolheu vem de Admin → Configurações. Gráficos usam uma paleta categórica
validada para daltonismo nos 4 fundos, sem eixo Y duplo.

### 9.3 Reset do admin via CLI

Perdeu a senha do único admin? No servidor, com o app parado ou não:

```bash
uv run python -c "
from scripts import app_db
u = app_db.get_user('admin'); t = app_db.gerar_senha_temporaria()
app_db.definir_senha(u['id'], t, must_change_password=True); app_db.apagar_sessoes_usuario(u['id'])
app_db.audit(None, 'senha_redefinida_cli', 'admin'); print('senha temporária:', t)"
```

A troca é obrigatória no próximo login. Banco sem nenhum usuário recria o `admin` sozinho
(senha em `APP_ACCESS_KEY` ou em `.access_key`, ver §1).

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

# Dashboard web
uv run python -m web
```
