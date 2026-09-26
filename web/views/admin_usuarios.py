"""Administração → Usuários: contas, perfis (admin/leitor), ativação e reset de senha
(porte de `pages/91_Admin_Usuarios.py`).

Diferença do Streamlit: desativar um usuário ou redefinir a senha dele derruba na hora as
sessões abertas desse usuário (`app_db.apagar_sessoes_usuario`).
"""

from __future__ import annotations

from markupsafe import Markup, escape

from scripts import app_db
from web.ui import Ctx, Pagina


def _sim_nao(p: Pagina, valor: int, sim: str = "ok", nao: str = "muted") -> Markup:
    return p.badge("Sim", sim) if valor else p.badge("Não", nao)


def render(p: Pagina, ctx: Ctx) -> None:
    admin = ctx.usuario
    p.title("Gerenciamento de Usuários", "group")
    p.caption("Cadastro de contas, perfis de acesso e redefinição de senha.")

    falhou_novo = False
    senha_mostrada: tuple[str, str] | None = None
    if ctx.acao == "criar_usuario":
        nome, role, senha = ctx.form_get("novo_nome"), ctx.form_get("novo_role"), ctx.form_get("novo_senha")
        try:
            app_db.criar_usuario(nome, senha, role=role)
        except app_db.AppDbError as exc:
            p.error(str(exc))
            falhou_novo = True
        else:
            app_db.audit(admin["username"], "usuario_criado", f"{nome} ({role})")
            p.toast(f"Usuário {nome} criado.")
    elif ctx.acao in ("salvar_usuario", "redefinir_senha"):
        try:
            alvo_id = int(ctx.form_get("alvo"))
        except ValueError:
            alvo_id = -1
        alvo = app_db.get_user_by_id(alvo_id)
        if not alvo:
            p.error("Usuário não encontrado.")
        elif ctx.acao == "salvar_usuario":
            perfil = ctx.form_get(f"perfil_{alvo_id}", alvo["role"])
            ativo = ctx.form_get(f"ativo_{alvo_id}", "0") == "1"
            if alvo["id"] == admin["id"] and not ativo:
                p.error("Você não pode desativar a si mesmo.")
            else:
                try:
                    app_db.alterar_usuario(alvo["id"], role=perfil, active=ativo)
                except app_db.AppDbError as exc:
                    p.error(str(exc))
                else:
                    if not ativo:
                        app_db.apagar_sessoes_usuario(alvo["id"])
                    app_db.audit(admin["username"], "usuario_alterado", f"{alvo['username']}: perfil={perfil} ativo={ativo}")
                    p.toast("Usuário atualizado.")
        else:
            temporaria = app_db.gerar_senha_temporaria()
            app_db.definir_senha(alvo["id"], temporaria, must_change_password=True)
            app_db.apagar_sessoes_usuario(alvo["id"])
            app_db.audit(admin["username"], "senha_redefinida", alvo["username"])
            senha_mostrada = (alvo["username"], temporaria)

    usuarios = app_db.listar_usuarios()
    col_novo, col_gerir = p.columns(2, gap="lg")
    with col_novo.card() as card, card.acao("criar_usuario", manter_valores=falhou_novo) as f:
        f.subheader(":material/person_add: Novo usuário")
        f.text_input("Usuário", "novo_nome", placeholder="3 a 32 caracteres")
        f.selectbox("Perfil", list(app_db.ROLES), "novo_role", default="leitor")
        f.text_input(
            "Senha temporária", "novo_senha", tipo="password", autocomplete="new-password",
            help=f"Mínimo {app_db.MIN_SENHA} caracteres e força Média.",
        )
        f.html(
            Markup(
                '<button type="button" class="botao" hx-get="/senha/sugerir" hx-target="next .sugestao" hx-swap="innerHTML">'
                "Gerar senha forte</button><div class=\"sugestao\" data-sugestao-para=\"novo_senha\"></div>"
            )
        )
        f.caption("O usuário será obrigado a trocar esta senha no primeiro acesso.")
        f.submit("Criar usuário", icone_nome="add")

    with col_gerir.card() as card:
        card.subheader(":material/manage_accounts: Gerenciar usuário")
        if senha_mostrada:
            card.warning(f"Senha temporária de **{senha_mostrada[0]}** (mostrada só agora):")
            card.code(senha_mostrada[1])
        alvo = card.selectbox(
            "Usuário", usuarios, "alvo_sel",
            formatar=lambda u: f"{u['username']} ({u['role']})", valor_de=lambda u: str(u["id"]),
        )
        if alvo:
            with card.acao("salvar_usuario") as f:
                f.html(Markup(f'<input type="hidden" name="alvo" value="{int(alvo["id"])}" form="_acao">'))
                f.selectbox("Perfil", list(app_db.ROLES), f"perfil_{alvo['id']}", default=alvo["role"])
                f.checkbox("Ativo", f"ativo_{alvo['id']}", default=bool(alvo["active"]))
                f.submit("Salvar", "salvar_usuario", icone_nome="save")
                f.submit(
                    "Redefinir senha", "redefinir_senha", primario=False, icone_nome="lock_reset",
                    confirmar=f"Gerar senha temporária para {alvo['username']}? As sessões abertas dele serão encerradas.",
                )

    # Lista embaixo dos formulários: as ações ficam à vista sem rolar.
    with p.card(f"Usuários ({len(usuarios)})", icone_nome="group") as lista:
        lista.html_table(
            ["Usuário", "Perfil", "Ativo", "Troca de senha pendente", "Criado em (UTC)"],
            [
                [
                    Markup(f"<strong>{escape(u['username'])}</strong>"),
                    p.badge(u["role"], "primario" if u["role"] == "admin" else "muted"),
                    _sim_nao(p, u["active"], "ok", "erro"),
                    _sim_nao(p, u["must_change_password"], "aviso", "muted"),
                    u["created_at"],
                ]
                for u in usuarios
            ],
        )
