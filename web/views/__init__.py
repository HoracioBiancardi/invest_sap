"""Registro das páginas do app web — mesmas seções/ordem do menu do antigo `app.py`.

Cada página é um módulo em `web/views/` com:

- `render(p: Pagina, ctx: Ctx) -> None` (obrigatório): desenha a tela;
- `BLOCOS: dict[str, Callable[[Node, Ctx], None]]` (opcional): blocos carregados à parte
  via `Node.lazy()` (rota `/p/<slug>/_bloco/<nome>`);
- `DOWNLOADS: dict[str, Callable[[Ctx], tuple[bytes, str]]]` (opcional): arquivos gerados
  (rota `/p/<slug>/_download/<nome>`);
- `AQUECER: list[Callable[[], object]]` (opcional): consultas pesadas pré-calculadas quando o
  DW fica acessível (ver `web.cache.Aquecedor.sementes`).
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType


@dataclass(frozen=True)
class InfoPagina:
    slug: str
    titulo: str
    icone: str
    secao: str
    modulo: str
    admin: bool = False
    # Páginas do Admin continuam acessíveis com o cofre bloqueado (é por lá que se desbloqueia).
    sem_cofre: bool = False

    def carregar(self) -> ModuleType:
        return importlib.import_module(f"web.views.{self.modulo}")

    @property
    def url(self) -> str:
        return f"/p/{self.slug}"


PAGINAS: tuple[InfoPagina, ...] = (
    InfoPagina("home", "Home", "dashboard", "", "home"),
    InfoPagina("oportunidade", "Oportunidade", "target", "Funil de Vendas", "oportunidade"),
    InfoPagina("pedidos", "Pedidos", "receipt_long", "Funil de Vendas", "pedidos"),
    InfoPagina("pendencia-estoque", "Pendência x Estoque", "pending_actions", "Funil de Vendas", "pendencia_estoque"),
    InfoPagina("estoque", "Estoque", "inventory_2", "Funil de Vendas", "estoque"),
    InfoPagina("remessas", "Remessas", "local_shipping", "Funil de Vendas", "remessas"),
    InfoPagina("faturamento", "Faturamento", "payments", "Funil de Vendas", "faturamento"),
    InfoPagina("credito-devolucoes", "Crédito e Devoluções", "credit_card", "Funil de Vendas", "credito_devolucoes"),
    InfoPagina("metas", "Faturamento x Meta", "track_changes", "Metas e Performance", "metas"),
    InfoPagina("vendedor", "Vendedor", "badge", "Metas e Performance", "vendedor"),
    InfoPagina("vendedor-meta", "Vendedor x Meta x Faturamento", "leaderboard", "Metas e Performance", "vendedor_meta"),
    InfoPagina("cliente-360", "Cliente 360", "account_circle", "Cadastros", "cliente_360"),
    InfoPagina("material", "Material", "inventory", "Cadastros", "material"),
    InfoPagina("painel-vendas", "Painel Vendas", "speed", "Faturamento (Painel Vendas)", "painel_vendas"),
    InfoPagina("produto-cliente", "Produto / Cliente", "category", "Faturamento (Painel Vendas)", "produto_cliente"),
    InfoPagina("relatorio-analitico", "Relatório Analítico", "query_stats", "Faturamento (Painel Vendas)", "relatorio_analitico"),
    InfoPagina("auditoria", "Auditoria do Fluxo", "fact_check", "Técnico", "auditoria"),
    InfoPagina("admin", "Painel", "admin_panel_settings", "Administração", "admin_painel", admin=True, sem_cofre=True),
    InfoPagina("admin-usuarios", "Usuários", "group", "Administração", "admin_usuarios", admin=True, sem_cofre=True),
    InfoPagina("admin-configuracoes", "Configurações", "tune", "Administração", "admin_configuracoes", admin=True, sem_cofre=True),
    InfoPagina("admin-dados", "Dados de negócio", "database", "Administração", "admin_dados", admin=True),
    InfoPagina("admin-cofre", "Cofre de credenciais", "lock", "Administração", "admin_cofre", admin=True, sem_cofre=True),
    InfoPagina("admin-auditoria", "Auditoria", "history", "Administração", "admin_auditoria", admin=True, sem_cofre=True),
)

POR_SLUG = {p.slug: p for p in PAGINAS}

# nome da seção -> (ícone, título completo, rótulo curto sob o ícone)
SECOES: dict[str, tuple[str, str, str]] = {
    "": ("dashboard", "Início", "Início"),
    "Funil de Vendas": ("filter_alt", "Funil de Vendas", "Funil"),
    "Metas e Performance": ("track_changes", "Metas e Performance", "Metas"),
    "Cadastros": ("contacts", "Cadastros", "Cadastro"),
    "Faturamento (Painel Vendas)": ("payments", "Faturamento (Painel Vendas)", "Painel"),
    "Técnico": ("build", "Técnico", "Técnico"),
    "Administração": ("admin_panel_settings", "Administração", "Admin"),
}


def menu(role: str) -> dict[str, list[InfoPagina]]:
    """Seções → páginas visíveis para o perfil."""
    secoes: dict[str, list[InfoPagina]] = {}
    for pagina in PAGINAS:
        if pagina.admin and role != "admin":
            continue
        secoes.setdefault(pagina.secao, []).append(pagina)
    return secoes


def sementes_de_aquecimento() -> list:
    """Junta o `AQUECER` de todas as views (importa todas — usado na subida do app)."""
    sementes = []
    for pagina in PAGINAS:
        sementes.extend(getattr(pagina.carregar(), "AQUECER", []))
    return sementes
