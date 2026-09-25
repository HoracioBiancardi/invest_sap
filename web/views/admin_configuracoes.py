"""Administração → Configurações: tema padrão, inatividade, limiar de pedido zumbi e filtros
padrão (porte de `pages/92_Admin_Configuracoes.py`). Gravado na tabela `settings` do SQLite.
"""

from __future__ import annotations

from scripts import app_db
from web.temas import TEMAS
from web.ui import Ctx, Pagina

_CHECKS = (
    (
        "excluir_estoque_internacional",
        "Excluir estoque internacional (Uruguai/Colômbia) das métricas por padrão",
        "Aplica-se às páginas Home e Estoque: tira os centros de Uruguai/Colômbia (`Pais_Centro` "
        "UY/CO) das quantidades agregadas — hoje elas somam junto mesmo quando o valor financeiro "
        "já fica restrito a BRL. Alemanha (DE) não entra nessa exclusão. Não afeta filtro manual "
        "de País na página Estoque.",
    ),
    (
        "excluir_intercompany",
        'Excluir clientes intercompany (filiais "BLAU*") das métricas por padrão',
        "Aplica-se a Pedidos, Faturamento, Cliente 360, Remessas, Crédito/Devoluções, Oportunidade "
        "e Pendência x Estoque: tira clientes com `Nome_Cliente` contendo \"BLAU\" — transferência "
        "entre filiais do próprio grupo, não cliente comercial (infla 'NAO ALOCADO' de Linha de "
        "Negócio pra ~98% se não excluído).",
    ),
    (
        "excluir_org_vendas_internacional",
        "Excluir Organização de Vendas Colômbia/Uruguai das métricas por padrão",
        "Aplica-se a Faturamento e Pendência x Estoque: tira pendência/faturamento atribuído às "
        "Organizações de Vendas \"Blau Farma Colombia\"/\"Blau Farma Uruguay\" — diferente do "
        "intercompany (esse é por cliente): aqui sai mesmo quando o cliente final não é filial.",
    ),
    (
        "excluir_possivel_zumbi",
        "Excluir possíveis pedidos zumbi do ranking por material por padrão",
        "Aplica-se ao ranking \"Materiais sem cobertura de estoque\" em Pendência x Estoque: tira "
        "Material+Centro onde o pedido mais antigo passa o limiar de backlog \"recente\" **e** a "
        "reserva SAP é menor que a quantidade sem estoque (`Possivel_Zumbi`).",
    ),
)


def render(p: Pagina, ctx: Ctx) -> None:
    admin = ctx.usuario
    p.title("Configurações do Sistema", "tune")
    p.caption("Aparência padrão, sessão e filtros aplicados por padrão nas páginas.")

    if ctx.acao == "salvar_config":
        try:
            tema = ctx.form_get("tema")
            if tema not in TEMAS:
                raise app_db.AppDbError("Tema inválido.")
            idle = max(1, min(480, int(ctx.form_get("idle") or 30)))
            zumbi = max(30, min(3650, int(ctx.form_get("zumbi") or 365)))
        except ValueError:
            p.error("Valores numéricos inválidos.")
        except app_db.AppDbError as exc:
            p.error(str(exc))
        else:
            flags = {chave: ctx.form_get(chave, "0") == "1" for chave, _, _ in _CHECKS}
            app_db.set_setting("default_theme", tema)
            app_db.set_setting("idle_minutes", idle)
            app_db.set_setting("limiar_zumbi_dias", zumbi)
            for chave, valor in flags.items():
                app_db.set_setting(chave, valor)
            app_db.audit(
                admin["username"], "config_alterada",
                f"{tema}/{idle}/{zumbi}/" + "/".join(f"{k}={v}" for k, v in flags.items()),
            )
            p.success("Configurações salvas.")

    p.subheader("Configurações do app")
    with p.card() as card, card.acao("salvar_config") as f:
        c1, c2, c3 = f.columns(3)
        c1.selectbox("Tema padrão (quem ainda não escolheu um)", list(TEMAS), "tema",
                     default=app_db.get_setting("default_theme"), formatar=lambda k: TEMAS[k])
        c2.number_input("Bloqueio por inatividade (minutos)", "idle", 1, 480, int(app_db.get_setting("idle_minutes")))
        c3.number_input(
            'Pedidos: backlog "recente" até quantos dias (limiar de pedido zumbi)', "zumbi", 30, 3650,
            int(app_db.get_setting("limiar_zumbi_dias")), passo=30,
        )
        for chave, rotulo, ajuda in _CHECKS:
            f.checkbox(rotulo, chave, default=bool(app_db.get_setting(chave)), help=ajuda)
        f.submit("Salvar", icone_nome="save")
