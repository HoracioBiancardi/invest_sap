"""Administração — painel de entrada com cards para cada área (porte de `pages/90_Admin.py`).

Usuários/configurações/ajustes/cofre vivem no SQLite local do app (`scripts/app_db.py`); o DW
(SQL Server/HANA) continua só leitura.
"""

from __future__ import annotations

from markupsafe import Markup, escape

from scripts.credential_vault import CredentialVault
from web.ui import Ctx, Pagina, icone

_CARDS = (
    ("group", "Controle de Usuários",
     "Cadastro de contas, perfis de acesso (admin/leitor), ativação e redefinição de senha.",
     "/p/admin-usuarios", "Gerenciar Usuários →"),
    ("tune", "Configurações do Sistema",
     "Tema padrão, bloqueio por inatividade e filtros aplicados por padrão nas páginas.",
     "/p/admin-configuracoes", "Gerenciar Configurações →"),
    ("database", "Dados de Negócio",
     "Consulta das tabelas manuais do DW e solicitações de ajuste exportáveis em CSV.",
     "/p/admin-dados", "Ver Dados de Negócio →"),
    ("lock", "Cofre de Credenciais",
     "Conexões do HANA e do SQL Server cifradas em repouso, com senha mestra.",
     "/p/admin-cofre", "Gerenciar Cofre →"),
    ("history", "Audit Log & Histórico",
     "Logins, trocas de senha, operações do cofre, mudanças de configuração e estado do cache.",
     "/p/admin-auditoria", "Ver Audit Log →"),
)


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Painel de Administração", "admin_panel_settings")
    p.caption("Gerenciamento de usuários, configurações, dados de negócio, credenciais e auditoria.")

    estado = CredentialVault.estado()
    if estado["configurado"] and not estado["desbloqueado"]:
        p.warning(
            "Cofre de credenciais bloqueado — as páginas de dados não consultam o DW até você "
            "desbloqueá-lo em **Cofre de Credenciais**.",
            "lock",
        )
    elif not estado["configurado"]:
        p.info(
            "As credenciais do DW ainda vêm do .env em texto puro — mova-as para o **Cofre de Credenciais**.",
            "lock_open",
        )

    for inicio in range(0, len(_CARDS), 3):
        for coluna, (ic, titulo, descricao, href, rotulo) in zip(p.columns(3, gap="lg"), _CARDS[inicio : inicio + 3]):
            coluna.card().html(
                Markup(
                    f'<div class="navcard"><div class="navcard-icone">{icone(ic)}</div>'
                    f'<div class="navcard-titulo">{escape(titulo)}</div>'
                    f'<div class="navcard-desc">{escape(descricao)}</div>'
                    f'<a class="link" href="{escape(href)}">{escape(rotulo)}</a></div>'
                )
            )
