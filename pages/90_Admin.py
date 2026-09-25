"""Administração — painel de entrada com cards para cada área (padrão do input_arquivos).

Só perfil `admin` (`scripts/auth.py::require_admin`). Cada card leva a uma página própria:
Usuários (91), Configurações (92), Dados de negócio (93), Cofre de credenciais (94) e
Auditoria (95). Usuários/configurações/ajustes/cofre vivem no SQLite local do app
(`scripts/app_db.py`); o DW (SQL Server/HANA) continua só leitura.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Administração — Invest SAP", page_icon="🛠️", layout="wide")

from scripts.auth import require_admin  # noqa: E402
from scripts.credential_vault import CredentialVault  # noqa: E402
from scripts.ui_theme import nav_card  # noqa: E402

admin = require_admin()

st.title(":material/admin_panel_settings: Painel de Administração")
st.caption("Gerenciamento de usuários, configurações, dados de negócio, credenciais e auditoria.")

estado = CredentialVault.estado()
if estado["configurado"] and not estado["desbloqueado"]:
    st.warning(
        "Cofre de credenciais bloqueado — as páginas de dados não consultam o DW até você "
        "desbloqueá-lo em **Cofre de Credenciais**.",
        icon=":material/lock:",
    )
elif not estado["configurado"]:
    st.info(
        "As credenciais do DW ainda vêm do .env em texto puro — mova-as para o "
        "**Cofre de Credenciais**.",
        icon=":material/lock_open:",
    )

_CARDS = (
    ("usuarios", "👥", "Controle de Usuários",
     "Cadastro de contas, perfis de acesso (admin/leitor), ativação e redefinição de senha.",
     "pages/91_Admin_Usuarios.py", "Gerenciar Usuários →"),
    ("config", "⚙️", "Configurações do Sistema",
     "Tema padrão, bloqueio por inatividade e filtros aplicados por padrão nas páginas.",
     "pages/92_Admin_Configuracoes.py", "Gerenciar Configurações →"),
    ("dados", "🗂️", "Dados de Negócio",
     "Consulta das tabelas manuais do DW e solicitações de ajuste exportáveis em CSV.",
     "pages/93_Admin_Dados.py", "Ver Dados de Negócio →"),
    ("cofre", "🔐", "Cofre de Credenciais",
     "Conexões do HANA e do SQL Server cifradas em repouso, com senha mestra.",
     "pages/94_Admin_Cofre.py", "Gerenciar Cofre →"),
    ("auditoria", "🕒", "Audit Log & Histórico",
     "Registro de logins, trocas de senha, operações do cofre e mudanças de configuração.",
     "pages/95_Admin_Auditoria.py", "Ver Audit Log →"),
)
for inicio in range(0, len(_CARDS), 3):
    colunas = st.columns(3, gap="medium")
    for coluna, card in zip(colunas, _CARDS[inicio:inicio + 3]):
        with coluna:
            nav_card(*card)
