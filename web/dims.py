"""Listas grandes de filtro (clientes, produtos, vendedores...) servidas para o IndexedDB.

Um `<select data-dim="nome" data-dim-ver="hash">` chega com só a opção escolhida no HTML; o
`static/app.js` completa a lista a partir do IndexedDB do navegador e só baixa de novo
(`GET /api/dim/<nome>`) quando a versão (hash do conteúdo) muda. Assim cada troca de filtro
não reenvia milhares de `<option>`.

Só nomes de dimensão (sem valor financeiro). O IndexedDB é apagado no logout
(`Clear-Site-Data: "storage"`).
"""

from __future__ import annotations

import hashlib
from typing import Callable

from scripts.query_faturamento_comercial import DIMENSOES_FATURAMENTO


def _comercial(dimensao: str) -> Callable[[], list[str]]:
    def _fonte() -> list[str]:
        from web.views._comum import valores_dimensao_cached

        return ["Todos"] + valores_dimensao_cached(dimensao)

    return _fonte


def _vendedores() -> list[str]:
    from web.views._comum import vendedores_cached

    return ["Todos"] + [nome for nome, _ in vendedores_cached()]


FONTES: dict[str, Callable[[], list[str]]] = {f"com:{d}": _comercial(d) for d in DIMENSOES_FATURAMENTO}
FONTES["vendedores"] = _vendedores


def versao(itens: list[str]) -> str:
    return hashlib.blake2b("\x1f".join(itens).encode("utf-8"), digest_size=8).hexdigest()


def carregar(nome: str) -> tuple[str, list[str]]:
    """(versão, itens) da lista `nome`. KeyError se não existe."""
    itens = FONTES[nome]()
    return versao(itens), itens
