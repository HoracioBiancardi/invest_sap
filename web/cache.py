"""Cache de consultas do DW — substitui o `st.cache_data` das páginas Streamlit.

Três diferenças de propósito em relação ao Streamlit:

- **Single-flight**: se N usuários pedem a mesma consulta fria ao mesmo tempo, só 1 thread
  vai ao DW; as outras esperam o mesmo resultado (evita a "manada" em cima do SQL Server
  quando o app abre pra mais gente).
- **Refresh-ahead** (`warm=True`): o `Aquecedor` recalcula em background as consultas
  pesadas registradas antes do TTL vencer, então o usuário quase nunca paga a consulta fria.
- **Um processo só**: o cache vive na memória do processo (como o cofre de credenciais e o
  lockout de login — ver `web/main.py`). O app roda com 1 worker uvicorn + threadpool; o
  gargalo é o DW, não o Python.

Os valores devolvidos são compartilhados entre requisições: quem recebe um DataFrame do cache
não pode alterá-lo in-place (use `.copy()`/`.assign()`), mesma regra do `st.cache_data`
sem a cópia automática que ele fazia.
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Hashable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def _congelar(valor: Any) -> Hashable:
    """Converte argumentos (dict/list/set) em algo hasheável para compor a chave do cache."""
    if isinstance(valor, dict):
        return tuple(sorted((k, _congelar(v)) for k, v in valor.items()))
    if isinstance(valor, (list, tuple)):
        return tuple(_congelar(v) for v in valor)
    if isinstance(valor, set):
        return tuple(sorted(_congelar(v) for v in valor))
    return valor


@dataclass
class _Entrada:
    valor: Any
    expira: float
    criado: float


@dataclass
class _Funcao:
    nome: str
    fn: Callable[..., Any]
    ttl: float
    warm: bool
    entradas: dict[Hashable, _Entrada] = field(default_factory=dict)
    # Args da última chamada por chave — o aquecedor reusa pra recalcular.
    chamadas: dict[Hashable, tuple[tuple, dict]] = field(default_factory=dict)
    locks: dict[Hashable, threading.Lock] = field(default_factory=dict)
    # Último acesso de usuário por chave — o aquecedor abandona chave que ninguém pede mais
    # (ex.: consulta com a data de ontem).
    acessos: dict[Hashable, float] = field(default_factory=dict)


class Cache:
    """Registro global das funções cacheadas."""

    _funcoes: dict[str, _Funcao] = {}
    _lock = threading.Lock()
    MAX_ENTRADAS_POR_FUNCAO = 256

    @classmethod
    def limpar(cls) -> None:
        """Esvazia o cache inteiro (troca de credenciais, testes, botão do Admin)."""
        with cls._lock:
            for f in cls._funcoes.values():
                f.entradas.clear()
                f.chamadas.clear()
                f.acessos.clear()
        Aquecedor.ressemear()

    @classmethod
    def resumo(cls) -> list[dict[str, Any]]:
        """Estado do cache para a tela de Admin."""
        agora = time.time()
        linhas = []
        for f in cls._funcoes.values():
            vivas = [e for e in f.entradas.values() if e.expira > agora]
            linhas.append(
                {
                    "Consulta": f.nome,
                    "TTL (s)": int(f.ttl),
                    "Pré-aquecida": "sim" if f.warm else "não",
                    "Entradas válidas": len(vivas),
                    "Idade da mais recente (s)": int(agora - max((e.criado for e in vivas), default=agora)),
                }
            )
        return sorted(linhas, key=lambda d: d["Consulta"])

    @classmethod
    def _obter(cls, f: _Funcao, args: tuple, kwargs: dict, forcar: bool = False) -> Any:
        chave = (_congelar(args), _congelar(kwargs))
        agora = time.time()
        if not forcar:
            f.acessos[chave] = agora
        entrada = f.entradas.get(chave)
        if entrada and entrada.expira > agora and not forcar:
            return entrada.valor
        with cls._lock:
            lock = f.locks.setdefault(chave, threading.Lock())
        with lock:
            # Outra thread pode ter preenchido enquanto esta esperava o lock (single-flight).
            entrada = f.entradas.get(chave)
            if entrada and entrada.expira > time.time() and not forcar:
                return entrada.valor
            inicio = time.time()
            valor = f.fn(*args, **kwargs)
            logger.info("cache: %s calculado em %.1fs", f.nome, time.time() - inicio)
            with cls._lock:
                if len(f.entradas) >= cls.MAX_ENTRADAS_POR_FUNCAO:
                    mais_velha = min(f.entradas, key=lambda k: f.entradas[k].criado)
                    f.entradas.pop(mais_velha, None)
                    f.chamadas.pop(mais_velha, None)
                    f.acessos.pop(mais_velha, None)
                f.entradas[chave] = _Entrada(valor, time.time() + f.ttl, time.time())
                f.chamadas[chave] = (args, kwargs)
            return valor


def cached(ttl: float, warm: bool = False, nome: str | None = None) -> Callable[[F], F]:
    """Decorator de cache com TTL (segundos).

    Args:
        ttl: Validade de cada resultado, em segundos.
        warm: Se True, o aquecedor recalcula em background as chaves já usadas antes de
            vencerem (usar só em consulta pesada e com poucas combinações de argumento).
        nome: Nome exibido no Admin (default: módulo.função).
    """

    def decorator(fn: F) -> F:
        registro = _Funcao(nome or f"{fn.__module__}.{fn.__qualname__}", fn, ttl, warm)
        Cache._funcoes[registro.nome] = registro

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return Cache._obter(registro, args, kwargs)

        wrapper.cache_registro = registro  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator


class Aquecedor:
    """Thread que recalcula em background as entradas `warm=True` perto de vencer.

    Só roda se o DW estiver acessível (cofre desbloqueado ou `.env`); falha de conexão é
    logada e tentada de novo no próximo ciclo, nunca derruba o app.
    """

    INTERVALO_SEG = 30
    # Só mantém quente o que alguém consultou nesta janela.
    ABANDONO_SEG = 3600
    # Recalcula quando falta menos que esta fração do TTL pra vencer.
    FOLGA = 0.2

    _thread: threading.Thread | None = None
    _parar = threading.Event()
    # Consultas pesadas calculadas assim que o DW fica acessível (subida do app ou desbloqueio
    # do cofre) — o primeiro usuário do dia não paga a consulta fria. Cada view registra as
    # suas em `AQUECER` (ver web/views/__init__.py).
    sementes: list[Callable[[], Any]] = []
    _semeado = False

    @classmethod
    def ressemear(cls) -> None:
        """Pede nova rodada das sementes (ex.: depois de esvaziar o cache)."""
        cls._semeado = False

    @classmethod
    def _semear(cls) -> None:
        cls._semeado = True
        inicio = time.time()
        # Pool separado: as sementes chamam `em_paralelo` (que usa `_pool`); dividir o mesmo
        # pool entre quem espera e quem executa poderia travar tudo.
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="semente") as pool_sementes:
            futuros = [pool_sementes.submit(fn) for fn in cls.sementes]
            for futuro in futuros:
                try:
                    futuro.result()
                except Exception:  # noqa: BLE001
                    logger.warning("cache: falha ao semear consulta", exc_info=True)
        logger.info("cache: %d consultas pré-aquecidas em %.0fs", len(futuros), time.time() - inicio)

    @classmethod
    def iniciar(cls, pode_rodar: Callable[[], bool]) -> None:
        if cls._thread and cls._thread.is_alive():
            return
        cls._parar.clear()
        cls._thread = threading.Thread(target=cls._loop, args=(pode_rodar,), name="cache-warmer", daemon=True)
        cls._thread.start()

    @classmethod
    def parar(cls) -> None:
        cls._parar.set()

    @classmethod
    def _loop(cls, pode_rodar: Callable[[], bool]) -> None:
        espera = 1.0
        while not cls._parar.wait(espera):
            espera = cls.INTERVALO_SEG
            if not pode_rodar():
                cls._semeado = False
                espera = 5.0
                continue
            if not cls._semeado:
                cls._semear()
            for f in list(Cache._funcoes.values()):
                if not f.warm:
                    continue
                agora = time.time()
                for chave, (args, kwargs) in list(f.chamadas.items()):
                    if agora - f.acessos.get(chave, 0) > max(cls.ABANDONO_SEG, 2 * f.ttl):
                        continue
                    entrada = f.entradas.get(chave)
                    if entrada and entrada.expira - agora > f.ttl * cls.FOLGA:
                        continue
                    try:
                        Cache._obter(f, args, kwargs, forcar=True)
                    except Exception:  # noqa: BLE001 — aquecedor nunca pode morrer
                        logger.warning("cache: falha ao pré-aquecer %s", f.nome, exc_info=True)


_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="dw")


def em_paralelo(**tarefas: Callable[[], Any]) -> dict[str, Any]:
    """Roda consultas independentes ao mesmo tempo (tempo total = a mais lenta, não a soma).

    Uso: `em_paralelo(aging=lambda: aging_pendencias(...), estoque=lambda: ...)`. Pool
    próprio de 8 threads, abaixo do pool de conexões do SQLAlchemy (5 + 10 de overflow).
    """
    futuros = {nome: _pool.submit(fn) for nome, fn in tarefas.items()}
    return {nome: futuro.result() for nome, futuro in futuros.items()}
