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
```

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
