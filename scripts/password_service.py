"""Serviço de senhas do app — hash, geração e avaliação de força.

Porte da parte de senhas do `crypto_vault_service.py` do app_template
(`generate_password` / `evaluate_password_strength`), com as mesmas regras de pontuação e
níveis, somado ao hash PBKDF2-HMAC-SHA256 (600.000 iterações + salt de 16 bytes, mesmo custo
do template) que antes morava solto em `scripts/app_db.py`. A cifragem Fernet do template não
foi portada: este app não cifra dados, só guarda hash de senha — e assim não precisa da
dependência `cryptography`.

Consumidores: `scripts/app_db.py` (hash/validação ao gravar usuário), `scripts/auth.py`
(bootstrap do admin e medidor de força na troca de senha) e `pages/90_Admin.py` (senha
temporária).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import string
from dataclasses import dataclass


@dataclass(frozen=True)
class ForcaSenha:
    """Resultado da avaliação de força de uma senha.

    Attributes:
        score: Pontuação de 0 a 100.
        nivel: Rótulo do nível ("Muito Fraca", "Fraca", "Média", "Forte", "Excelente").
        feedback: Recomendações para fortalecer a senha (ou a mensagem de senha segura).
    """

    score: int
    nivel: str
    feedback: tuple[str, ...]


class PasswordService:
    """Hash, verificação, geração e avaliação de força de senhas.

    Attributes:
        ITERACOES: Iterações do PBKDF2 nos hashes novos (hashes antigos guardam a própria
            contagem e continuam verificáveis se ela mudar).
        MIN_TAMANHO: Tamanho mínimo aceito para uma senha.
        SCORE_MINIMO: Pontuação mínima aceita (nível "Média" do template).
        SIMBOLOS: Conjunto de símbolos usado na geração e na avaliação (o mesmo do template).
    """

    ITERACOES: int = 600_000
    MIN_TAMANHO: int = 10
    SCORE_MINIMO: int = 45
    SIMBOLOS: str = "!@#$%^&*()_+-=[]{}|;:,.<>?"
    _PREFIXO: str = "pbkdf2_sha256"

    @classmethod
    def hash(cls, senha: str) -> str:
        """Gera o hash PBKDF2-HMAC-SHA256 da senha, com salt aleatório.

        Args:
            senha: Senha em texto puro.

        Returns:
            String no formato `pbkdf2_sha256$<iterações>$<salt b64>$<hash b64>`.
        """
        salt = secrets.token_bytes(16)
        dk = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt, cls.ITERACOES)
        return "{}${}${}${}".format(
            cls._PREFIXO, cls.ITERACOES, base64.b64encode(salt).decode(), base64.b64encode(dk).decode()
        )

    @staticmethod
    def verify(senha: str, armazenado: str) -> bool:
        """Confere a senha contra um hash gerado por `hash()`, em tempo constante.

        Args:
            senha: Senha em texto puro informada.
            armazenado: Hash guardado no banco.

        Returns:
            True se a senha confere; False se não confere ou se o hash é inválido.
        """
        try:
            _, iteracoes, salt_b64, dk_b64 = armazenado.split("$")
            dk = hashlib.pbkdf2_hmac(
                "sha256", senha.encode(), base64.b64decode(salt_b64), int(iteracoes)
            )
            return hmac.compare_digest(dk, base64.b64decode(dk_b64))
        except (ValueError, TypeError):
            return False

    @classmethod
    def generate(
        cls,
        length: int = 16,
        use_uppercase: bool = True,
        use_lowercase: bool = True,
        use_digits: bool = True,
        use_symbols: bool = True,
    ) -> str:
        """Gera uma senha aleatória segura (`secrets`).

        Diferente do template, garante ao menos um caractere de cada classe escolhida,
        então a senha gerada sempre passa em `aceitavel()` com os parâmetros padrão.

        Args:
            length: Tamanho da senha (mínimo 4).
            use_uppercase: Incluir letras maiúsculas.
            use_lowercase: Incluir letras minúsculas.
            use_digits: Incluir dígitos.
            use_symbols: Incluir símbolos de `SIMBOLOS`.

        Returns:
            A senha gerada.
        """
        classes = [
            conjunto
            for usar, conjunto in (
                (use_uppercase, string.ascii_uppercase),
                (use_lowercase, string.ascii_lowercase),
                (use_digits, string.digits),
                (use_symbols, cls.SIMBOLOS),
            )
            if usar
        ] or [string.ascii_letters + string.digits]
        tamanho = max(4, length, len(classes))
        todos = "".join(classes)
        chars = [secrets.choice(c) for c in classes]
        chars += [secrets.choice(todos) for _ in range(tamanho - len(chars))]
        secrets.SystemRandom().shuffle(chars)
        return "".join(chars)

    @classmethod
    def evaluate(cls, senha: str) -> ForcaSenha:
        """Avalia a força da senha (mesmas regras do `evaluate_password_strength` do template).

        Args:
            senha: Senha em texto puro.

        Returns:
            `ForcaSenha` com score 0-100, nível e recomendações.
        """
        if not senha:
            return ForcaSenha(0, "Muito Fraca", ("Digite uma senha",))

        score = 0
        feedback: list[str] = []
        if len(senha) >= 16:
            score += 35
        elif len(senha) >= 12:
            score += 25
        elif len(senha) >= 8:
            score += 15
        else:
            feedback.append("Aumente o comprimento para pelo menos 12 caracteres")

        checagens = (
            (any(c in string.ascii_uppercase for c in senha), "Adicione letras maiúsculas (A-Z)"),
            (any(c in string.ascii_lowercase for c in senha), "Adicione letras minúsculas (a-z)"),
            (any(c in string.digits for c in senha), "Adicione números (0-9)"),
            (any(c in cls.SIMBOLOS for c in senha), "Adicione símbolos especiais (!@#$)"),
        )
        for presente, dica in checagens:
            if presente:
                score += 15
            else:
                feedback.append(dica)
        score = min(100, score)

        if score >= 85:
            nivel = "Excelente"
        elif score >= 65:
            nivel = "Forte"
        elif score >= 45:
            nivel = "Média"
        elif score >= 25:
            nivel = "Fraca"
        else:
            nivel = "Muito Fraca"
        return ForcaSenha(score, nivel, tuple(feedback) or ("Senha altamente segura!",))

    SCORE_FORTE: int = 65

    @classmethod
    def problemas(cls, senha: str, score_minimo: int | None = None) -> list[str]:
        """Lista o que impede a senha de ser aceita (vazia = aceitável).

        Args:
            senha: Senha em texto puro.
            score_minimo: Pontuação mínima exigida; default `SCORE_MINIMO` ("Média"). Use
                `SCORE_FORTE` para segredos mais sensíveis (ex.: senha mestra do cofre).

        Returns:
            Mensagens de erro prontas para exibir; lista vazia se a senha é aceitável.
        """
        minimo = cls.SCORE_MINIMO if score_minimo is None else score_minimo
        if len(senha) < cls.MIN_TAMANHO:
            return [f"A senha precisa ter ao menos {cls.MIN_TAMANHO} caracteres."]
        forca = cls.evaluate(senha)
        if forca.score < minimo:
            rotulo = "Forte" if minimo >= cls.SCORE_FORTE else "Média"
            return [f"Senha {forca.nivel.lower()} — o mínimo é força {rotulo}.", *forca.feedback]
        return []

    @classmethod
    def aceitavel(cls, senha: str) -> bool:
        """Indica se a senha cumpre tamanho mínimo e força mínima.

        Args:
            senha: Senha em texto puro.

        Returns:
            True se `problemas()` não aponta nada.
        """
        return not cls.problemas(senha)
