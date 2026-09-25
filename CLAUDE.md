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
  `scripts/app_db.py`; hash/geração/força de senha em `scripts/password_service.py`, porte do
  app_template — mín. 10 caracteres e força Média). Reset do admin via CLI: `docs/COMO_RODAR.md` §9.3.
- **Credenciais do DW**: cofre cifrado com senha mestra no `app.db` (`scripts/credential_vault.py`,
  Admin → Cofre; §2.1) — se configurado, o `.env` é ignorado para HANA/SQL Server e o app sobe
  bloqueado até um admin digitar a senha mestra. 1ª execução cria `admin` — senha em `.access_key` (0600) ou
  `APP_ACCESS_KEY`; troca obrigatória no 1º login. Página **Admin** (painel `pages/90_Admin.py` + páginas 91–95, só
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
