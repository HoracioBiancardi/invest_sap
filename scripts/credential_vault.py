"""Cofre de credenciais do DW — conexões HANA/SQL Server cifradas no SQLite do app.

Substitui o `.env` como fonte das credenciais de banco, para elas não ficarem em texto puro
no disco. A cifragem é o porte do `crypto_vault_service.py` do app_template: Fernet
(AES-128-CBC + HMAC-SHA256) com chave derivada da **senha mestra** por PBKDF2-HMAC-SHA256
(600.000 iterações + salt aleatório de 16 bytes, gravado junto do blob cifrado).

A senha mestra nunca é gravada. Depois de desbloqueado, o cofre guarda as credenciais
decifradas só na memória do processo (compartilhadas por todas as sessões do Streamlit) até
`bloquear()` ou o processo reiniciar — aí um admin precisa desbloquear de novo.

Fonte das credenciais em `scripts/db.py` (`CredentialVault.valor`):
- cofre configurado → só o cofre (o `.env` é ignorado para estas chaves);
- cofre não configurado → `.env`, como antes (compatibilidade até migrar).

Sem `streamlit` aqui (mesma convenção do resto de `scripts/`): a UI fica em `scripts/auth.py`
e `pages/90_Admin.py`. Nos CLIs, `desbloquear_interativo()` pede a senha mestra via `getpass`.
"""

from __future__ import annotations

import base64
import getpass
import json
import os
import sys
import threading
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from scripts import app_db
from scripts.password_service import PasswordService

# Chaves guardadas no cofre (mesmos nomes das variáveis do .env). As `SENHAS` nunca voltam
# pra tela; as demais podem ser exibidas para edição na página Admin.
CHAVES: tuple[str, ...] = (
    "HANA_ADDRESS", "HANA_PORT", "HANA_USER", "HANA_PASSWORD", "DDIC_SCHEMA",
    "SQLSERVER_HOST", "SQLSERVER_PORT", "SQLSERVER_USER", "SQLSERVER_PASSWORD",
)
SENHAS: frozenset[str] = frozenset({"HANA_PASSWORD", "SQLSERVER_PASSWORD"})
OBRIGATORIAS: frozenset[str] = frozenset(
    {"HANA_ADDRESS", "HANA_USER", "HANA_PASSWORD", "SQLSERVER_HOST", "SQLSERVER_USER", "SQLSERVER_PASSWORD"}
)


class CofreError(ValueError):
    """Erro de regra do cofre, com mensagem pronta para exibir na tela."""


class CryptoVault:
    """Cifra/decifra bytes com Fernet + PBKDF2 (porte do CryptoVaultService do app_template).

    Attributes:
        SALT_PREFIX: Marcador de versão do formato do blob.
        SALT_LEN: Tamanho do salt em bytes.
        ITERACOES: Iterações do PBKDF2.
    """

    SALT_PREFIX: bytes = b"SALT_PBKDF2_V1:"
    SALT_LEN: int = 16
    ITERACOES: int = 600_000

    @classmethod
    def _derivar_chave(cls, senha_mestra: str, salt: bytes) -> bytes:
        """Deriva a chave Fernet da senha mestra.

        Args:
            senha_mestra: Senha mestra em texto puro.
            salt: Salt aleatório do blob.

        Returns:
            Chave Fernet (32 bytes, base64 url-safe).
        """
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=cls.ITERACOES)
        return base64.urlsafe_b64encode(kdf.derive(senha_mestra.encode("utf-8")))

    @classmethod
    def cifrar(cls, dados: bytes, senha_mestra: str) -> bytes:
        """Cifra os dados com um salt novo.

        Args:
            dados: Conteúdo em claro.
            senha_mestra: Senha mestra.

        Returns:
            `SALT_PREFIX + salt + token Fernet`.
        """
        salt = os.urandom(cls.SALT_LEN)
        return cls.SALT_PREFIX + salt + Fernet(cls._derivar_chave(senha_mestra, salt)).encrypt(dados)

    @classmethod
    def decifrar(cls, blob: bytes, senha_mestra: str) -> bytes:
        """Decifra um blob gerado por `cifrar()`.

        Args:
            blob: Conteúdo cifrado.
            senha_mestra: Senha mestra.

        Returns:
            Conteúdo em claro.

        Raises:
            InvalidToken: Senha mestra errada ou blob corrompido/adulterado.
        """
        if not blob.startswith(cls.SALT_PREFIX):
            raise InvalidToken("Formato de cofre desconhecido.")
        inicio = len(cls.SALT_PREFIX)
        salt = blob[inicio : inicio + cls.SALT_LEN]
        return Fernet(cls._derivar_chave(senha_mestra, salt)).decrypt(blob[inicio + cls.SALT_LEN :])


class CredentialVault:
    """Cofre de credenciais do DW, persistido no `app.db` e desbloqueado na memória do processo.

    Estado de classe (e não de instância) de propósito: o Streamlit roda todas as sessões no
    mesmo processo, e o desbloqueio feito por um admin vale para todas até `bloquear()`.

    Attributes:
        MAX_FALHAS: Tentativas erradas de senha mestra antes do bloqueio temporário.
        BLOQUEIO_SEG: Duração do bloqueio temporário, em segundos.
    """

    MAX_FALHAS: int = 5
    BLOQUEIO_SEG: int = 60
    # False no servidor web (web/main.py): lá o desbloqueio é pela tela, e um getpass()
    # travaria a thread da requisição esperando o terminal.
    interativo: bool = True
    _credenciais: dict[str, str] | None = None
    _falhas: list[float] = []
    _lock = threading.Lock()

    # ── persistência ─────────────────────────────────────────────────────────

    @staticmethod
    def _ler_blob() -> bytes | None:
        """Lê o blob cifrado do `app.db`.

        Returns:
            O blob, ou None se o cofre não foi configurado.
        """
        with app_db.connect() as c:
            row = c.execute("SELECT blob FROM vault WHERE id = 1").fetchone()
        return bytes(row["blob"]) if row else None

    @staticmethod
    def _gravar_blob(blob: bytes) -> None:
        """Grava (ou substitui) o blob cifrado no `app.db`.

        Args:
            blob: Conteúdo cifrado.
        """
        with app_db.connect() as c:
            c.execute(
                "INSERT INTO vault (id, blob, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET blob = excluded.blob, updated_at = excluded.updated_at",
                (blob, app_db.agora()),
            )

    # ── estado ───────────────────────────────────────────────────────────────

    @classmethod
    def configurado(cls) -> bool:
        """Indica se existe um cofre gravado no `app.db`.

        Returns:
            True se o cofre foi criado.
        """
        return cls._ler_blob() is not None

    @classmethod
    def desbloqueado(cls) -> bool:
        """Indica se as credenciais estão decifradas na memória do processo.

        Returns:
            True se desbloqueado.
        """
        return cls._credenciais is not None

    @classmethod
    def segundos_bloqueado(cls) -> int:
        """Tempo restante do bloqueio por excesso de tentativas erradas.

        Returns:
            Segundos restantes (0 se liberado).
        """
        agora = time.time()
        with cls._lock:
            cls._falhas[:] = [t for t in cls._falhas if agora - t < cls.BLOQUEIO_SEG]
            if len(cls._falhas) < cls.MAX_FALHAS:
                return 0
            return int(cls.BLOQUEIO_SEG - (agora - cls._falhas[0])) + 1

    # ── operações ────────────────────────────────────────────────────────────

    @classmethod
    def _decifrar(cls, senha_mestra: str) -> dict[str, str]:
        """Decifra o cofre gravado, com controle de tentativas.

        Args:
            senha_mestra: Senha mestra.

        Returns:
            Credenciais em claro.

        Raises:
            CofreError: Cofre inexistente, bloqueio temporário ativo ou senha mestra errada.
        """
        restante = cls.segundos_bloqueado()
        if restante:
            raise CofreError(f"Muitas tentativas. Aguarde {restante}s.")
        blob = cls._ler_blob()
        if blob is None:
            raise CofreError("O cofre ainda não foi configurado.")
        try:
            dados = json.loads(CryptoVault.decifrar(blob, senha_mestra))
        except InvalidToken as exc:
            with cls._lock:
                cls._falhas.append(time.time())
            raise CofreError("Senha mestra incorreta.") from exc
        with cls._lock:
            cls._falhas.clear()
        return {k: str(v) for k, v in dados.items()}

    @classmethod
    def desbloquear(cls, senha_mestra: str) -> None:
        """Decifra o cofre e mantém as credenciais na memória do processo.

        Args:
            senha_mestra: Senha mestra.

        Raises:
            CofreError: Ver `_decifrar`.
        """
        credenciais = cls._decifrar(senha_mestra)
        with cls._lock:
            cls._credenciais = credenciais
        cls._invalidar_conexoes()

    @classmethod
    def bloquear(cls) -> None:
        """Descarta as credenciais da memória e as conexões abertas com elas."""
        with cls._lock:
            cls._credenciais = None
        cls._invalidar_conexoes()

    @staticmethod
    def _validar(credenciais: dict[str, str]) -> dict[str, str]:
        """Normaliza e valida as credenciais a gravar.

        Args:
            credenciais: Valores por chave de `CHAVES` (chaves extras são descartadas).

        Returns:
            Só as chaves conhecidas e preenchidas, sem espaços nas pontas.

        Raises:
            CofreError: Campo obrigatório vazio ou porta não numérica.
        """
        limpas = {k: str(credenciais.get(k, "")).strip() for k in CHAVES}
        limpas = {k: v for k, v in limpas.items() if v}
        faltando = sorted(OBRIGATORIAS - limpas.keys())
        if faltando:
            raise CofreError("Preencha: " + ", ".join(faltando) + ".")
        for porta in ("HANA_PORT", "SQLSERVER_PORT"):
            if porta in limpas and not limpas[porta].isdigit():
                raise CofreError(f"{porta} deve ser numérica.")
        return limpas

    @classmethod
    def criar(cls, credenciais: dict[str, str], senha_mestra: str) -> None:
        """Cria o cofre (só se ainda não existir) e já o deixa desbloqueado.

        Args:
            credenciais: Valores por chave de `CHAVES`.
            senha_mestra: Nova senha mestra (força mínima "Forte").

        Raises:
            CofreError: Cofre já existe, senha mestra fraca ou credenciais inválidas.
        """
        if cls.configurado():
            raise CofreError("O cofre já existe — use Atualizar.")
        problemas = PasswordService.problemas(senha_mestra, PasswordService.SCORE_FORTE)
        if problemas:
            raise CofreError(" ".join(problemas))
        limpas = cls._validar(credenciais)
        cls._gravar_blob(CryptoVault.cifrar(json.dumps(limpas).encode(), senha_mestra))
        with cls._lock:
            cls._credenciais = limpas
        cls._invalidar_conexoes()

    @classmethod
    def atualizar(cls, alteracoes: dict[str, str], senha_mestra: str) -> None:
        """Atualiza credenciais do cofre; campos vazios mantêm o valor atual.

        Args:
            alteracoes: Novos valores por chave (vazio = manter).
            senha_mestra: Senha mestra atual (confirma a operação e re-cifra).

        Raises:
            CofreError: Senha mestra errada ou credenciais inválidas.
        """
        atuais = cls._decifrar(senha_mestra)
        novas = cls._validar({**atuais, **{k: v for k, v in alteracoes.items() if str(v).strip()}})
        cls._gravar_blob(CryptoVault.cifrar(json.dumps(novas).encode(), senha_mestra))
        with cls._lock:
            cls._credenciais = novas
        cls._invalidar_conexoes()

    @classmethod
    def trocar_senha_mestra(cls, atual: str, nova: str) -> None:
        """Re-cifra o cofre com uma nova senha mestra.

        Args:
            atual: Senha mestra atual.
            nova: Nova senha mestra (força mínima "Forte").

        Raises:
            CofreError: Senha atual errada ou nova senha fraca/igual.
        """
        if nova == atual:
            raise CofreError("A nova senha mestra deve ser diferente da atual.")
        problemas = PasswordService.problemas(nova, PasswordService.SCORE_FORTE)
        if problemas:
            raise CofreError(" ".join(problemas))
        credenciais = cls._decifrar(atual)
        cls._gravar_blob(CryptoVault.cifrar(json.dumps(credenciais).encode(), nova))

    @classmethod
    def apagar(cls) -> None:
        """Apaga o cofre (recuperação de senha mestra perdida); o app volta a ler o `.env`."""
        with app_db.connect() as c:
            c.execute("DELETE FROM vault WHERE id = 1")
        cls.bloquear()

    # ── leitura ──────────────────────────────────────────────────────────────

    @classmethod
    def valores_visiveis(cls) -> dict[str, str]:
        """Credenciais atuais sem as senhas (para pré-preencher formulário de edição).

        Returns:
            Chave → valor, sem `SENHAS`; vazio se bloqueado.
        """
        credenciais = cls._credenciais or {}
        return {k: v for k, v in credenciais.items() if k not in SENHAS}

    @classmethod
    def desbloquear_interativo(cls) -> None:
        """Nos CLIs (terminal interativo, fora do Streamlit), pede a senha mestra via getpass.

        Não faz nada se já desbloqueado, se não há terminal ou se roda dentro do Streamlit
        (lá o desbloqueio é pela tela, por um admin).
        """
        if cls.desbloqueado() or not cls.interativo or not sys.stdin.isatty() or _dentro_do_streamlit():
            return
        for _ in range(3):
            try:
                cls.desbloquear(getpass.getpass("Senha mestra do cofre de credenciais: "))
                return
            except CofreError as exc:
                print(exc, file=sys.stderr)

    @classmethod
    def valor(cls, nome: str, default: str | None = None) -> str | None:
        """Valor de uma credencial: do cofre se configurado, senão do ambiente (`.env`).

        Args:
            nome: Nome da variável (ex.: "SQLSERVER_HOST").
            default: Valor se ausente.

        Returns:
            O valor, ou `default`.

        Raises:
            CofreError: Cofre configurado e bloqueado.
        """
        if nome not in CHAVES or not cls.configurado():
            return os.environ.get(nome, default)
        cls.desbloquear_interativo()
        credenciais = cls._credenciais
        if credenciais is None:
            raise CofreError(
                "Cofre de credenciais bloqueado (o app foi reiniciado). "
                "Um administrador precisa desbloqueá-lo com a senha mestra."
            )
        return credenciais.get(nome, default)

    @staticmethod
    def _invalidar_conexoes() -> None:
        """Descarta engines SQL Server cacheadas (foram criadas com as credenciais antigas)."""
        from scripts import db  # import tardio: db importa este módulo

        for engine in db.engines_em_cache():
            engine.dispose()
        db.get_sqlserver_engine.cache_clear()

    @classmethod
    def estado(cls) -> dict[str, Any]:
        """Resumo para a tela de administração.

        Returns:
            Dict com `configurado`, `desbloqueado` e `origem` ("cofre" ou ".env").
        """
        configurado = cls.configurado()
        return {
            "configurado": configurado,
            "desbloqueado": cls.desbloqueado(),
            "origem": "cofre" if configurado else ".env",
        }


def _dentro_do_streamlit() -> bool:
    """Indica se o código roda dentro de um servidor Streamlit.

    Returns:
        True se há runtime Streamlit ativo.
    """
    try:
        from streamlit import runtime

        return runtime.exists()
    except ImportError:
        return False
