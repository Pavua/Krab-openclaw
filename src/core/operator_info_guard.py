"""
Operator info guard — вторая линия защиты от утечек personal info оператора.

Вызывается из llm_flow.py перед отправкой ответа guest/partial пользователям.
Тонкая обёртка над `memory_pii_redactor.PIIRedactor` + дополнительные паттерны:
приватные GitHub URL, SSH-ключи, полный путь к домашней директории оператора.

Public API:
- sanitize_for_context(text, context) -> tuple[str, list[str]]
  Возвращает (санитизированный_текст, список_категорий_редакций).
  context: "guest" | "partial" (поведение одинаковое, для совместимости).
"""

from __future__ import annotations

import re

from .memory_pii_redactor import PIIRedactor

# ─── расширенные паттерны сверх базовых PIIRedactor ──────────────────────────

# SSH public keys: ssh-rsa/ssh-ed25519 AAAA... [comment]
_SSH_KEY_RE = re.compile(
    r"ssh-(?:rsa|ed25519|dss|ecdsa)\s+[A-Za-z0-9+/=]{20,}(?:\s+\S+)?",
    re.IGNORECASE,
)

# Приватные github URL оператора: github.com/Pavua/<repo> + локальные пути к репо
_PRIVATE_GITHUB_RE = re.compile(
    r"(?:https?://)?github\.com/(?:Pavua|pablito)/[A-Za-z0-9_.\-]+",
    re.IGNORECASE,
)

# Домашняя директория оператора: /Users/pablito/...
_HOME_PATH_RE = re.compile(r"/Users/pablito/[^\s`'\")]+")

# Скрытые пути к секретам openclaw workspace
_WORKSPACE_PATH_RE = re.compile(r"\.openclaw/[A-Za-z0-9_./\-]+")

_PLACEHOLDER_SSH = "[REDACTED_SSH_KEY]"
_PLACEHOLDER_GITHUB = "[REDACTED_PRIVATE_REPO]"
_PLACEHOLDER_HOME = "[REDACTED_PATH]"
_PLACEHOLDER_WS = "[REDACTED_WORKSPACE]"

# Shared redactor без owner whitelist — для гостевого контекста всё удаляется
_redactor = PIIRedactor(owner_whitelist=None)


def sanitize_for_context(text: str, *, context: str = "guest") -> tuple[str, list[str]]:
    """Санитизирует ответ LLM перед отправкой non-owner пользователю.

    Args:
        text: исходный текст ответа.
        context: "guest" | "partial" (сейчас используется одинаково).

    Returns:
        (sanitized_text, redacted_categories) — список категорий, где реально
        было что-то вырезано. Пустой список означает «без изменений».
    """
    if not text:
        return text, []

    categories: list[str] = []
    result = text

    # 1. SSH keys — первыми, т.к. высокорисковое (инцидент SwMaster).
    new_result, hits = _SSH_KEY_RE.subn(_PLACEHOLDER_SSH, result)
    if hits:
        categories.append("ssh_key")
        result = new_result

    # 2. Приватные GitHub URL.
    new_result, hits = _PRIVATE_GITHUB_RE.subn(_PLACEHOLDER_GITHUB, result)
    if hits:
        categories.append("private_repo")
        result = new_result

    # 3. Home path.
    new_result, hits = _HOME_PATH_RE.subn(_PLACEHOLDER_HOME, result)
    if hits:
        categories.append("home_path")
        result = new_result

    # 4. Workspace paths.
    new_result, hits = _WORKSPACE_PATH_RE.subn(_PLACEHOLDER_WS, result)
    if hits:
        categories.append("workspace_path")
        result = new_result

    # 5. Остальное — через базовый PIIRedactor (email, phone, cards, crypto, tokens).
    redaction = _redactor.redact(result)
    if redaction.stats.total > 0:
        result = redaction.text
        for cat, count in redaction.stats.counts.items():
            if count > 0:
                categories.append(cat)

    return result, categories
