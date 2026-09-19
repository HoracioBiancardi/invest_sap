# CLAUDE.md — Contexto e Diretrizes do Invest SAP

## Visão Geral do Projeto

Dashboard **Streamlit** (multipage, `pages/`) de análise comercial/vendas
sobre dados SAP — pendências, faturamento, metas, estoque, crédito/devoluções,
rastreamento de pedido e lookup DDIC. Sem framework FastAPI/auth própria
(app "internal-only" por design, roda só em `127.0.0.1`).

---

## 🛠️ Comandos de Execução

```bash
cd /home/swordpower/Documentos/REPO/PESSOAL/invest_sap
uv sync
uv run streamlit run app.py

# Testes (pytest)
uv run pytest -v
```

- **URL local**: `http://127.0.0.1:8501` — `.streamlit/config.toml` fixa `server.address`
  em loopback, XSRF ligado e erros sem traceback na tela. Credenciais do DW: copie
  `.env.example` para `.env` (ignorado pelo git).
- **Login**: usuário/senha em SQLite local (`data/app.db`, ignorado pelo git; `scripts/auth.py` +
  `scripts/app_db.py`). 1ª execução cria `admin` — senha em `.access_key` (0600) ou
  `APP_ACCESS_KEY`; troca obrigatória no 1º login. Página **Admin** (`pages/90_Admin.py`, só
  perfil admin): usuários, configurações e solicitações de ajuste (o DW segue só leitura).

---

## 📐 Estrutura

- **`app.py`**: entrypoint Streamlit.
- **`pages/`**: uma página por análise (`0_Home.py`, `1_Pendencias.py`, etc.).
- **`scripts/`**: lógica de consulta/negócio compartilhada entre páginas
  (`db.py`, `query_vendas_sap.py`, `ddic_lookup.py`, `trace_pedido.py`, etc.).
- Sem app_template/FastAPI — arquitetura própria de app Streamlit.

---

## 🔒 Revisão de Segurança

@~/.claude/security-review-checklist.md

Log histórico de achados por projeto em `app_template/SECURITY_CHECKLIST.md`.
