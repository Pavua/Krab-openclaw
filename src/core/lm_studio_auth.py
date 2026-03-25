# -*- coding: utf-8 -*-
"""
Единые helpers для аутентификации к LM Studio API.

Зачем это нужно:
- LM Studio с включённым `Require Authentication` требует API token даже для
  локальных `GET /v1/models` и `POST /v1/chat/completions`;
- до этого токеновая логика была размазана по нескольким местам и фактически
  отсутствовала в runtime/ops-скриптах;
- единая точка сборки заголовков упрощает поддержку и исключает дрейф.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values


def _strip_wrapping_quotes(value: str) -> str:
    """
    Снимает внешние кавычки с секрета, если они попали в env как часть значения.

    Почему это важно:
    - часть наших путей читает `.env` через shell/dotenv и получает уже чистое значение;
    - часть вспомогательных инструментов может передать строку с внешними кавычками;
    - для `Authorization: Bearer ...` это превращается в "формально есть header,
      но токен невалиден".
    """
    text = str(value or "").strip()
    if len(text) >= 2 and text[:1] == text[-1:] and text[:1] in {'"', "'"}:
        return text[1:-1].strip()
    return text


def _project_env_candidates() -> tuple[Path, ...]:
    """
    Возвращает кандидаты project-level env файлов для автономных helper-путей.

    Почему helper читает их сам:
    - часть standalone-скриптов импортирует только этот модуль, без `src.config`;
    - в таком режиме `.env` проекта не загружается автоматически;
    - из-за этого в LM Studio уходил пустой bearer и появлялись ложные WARN/ERROR.
    """
    project_root = Path(__file__).resolve().parents[2]
    return (project_root / ".env", project_root / ".env.local")


def _project_env_scope() -> dict[str, str]:
    """
    Читает `.env` проекта без мутации process environment.

    Приоритет:
    - `.env`
    - `.env.local` поверх `.env`
    - `os.environ` поверх файлового слоя уже на этапе merge в resolve.
    """
    merged: dict[str, str] = {}
    for path in _project_env_candidates():
        if not path.exists() or not path.is_file():
            continue
        try:
            payload = dotenv_values(path)
        except Exception:
            continue
        for raw_key, raw_value in dict(payload or {}).items():
            key = str(raw_key or "").strip()
            if not key or raw_value is None:
                continue
            merged[key] = _strip_wrapping_quotes(str(raw_value))
    return merged


def resolve_lm_studio_api_key(env: Mapping[str, str] | None = None) -> str:
    """
    Возвращает токен LM Studio из env.

    Порядок приоритета:
    1) `LM_STUDIO_API_KEY` — каноничное имя в проекте;
    2) `LM_STUDIO_AUTH_TOKEN` — совместимый legacy alias.
    """
    scope: Mapping[str, str]
    if env is None:
        scope = {**_project_env_scope(), **os.environ}
    else:
        scope = env
    for key in ("LM_STUDIO_API_KEY", "LM_STUDIO_AUTH_TOKEN"):
        value = _strip_wrapping_quotes(str(scope.get(key, "") or ""))
        if value:
            return value
    return ""


def build_lm_studio_auth_headers(
    *,
    api_key: str | None = None,
    include_json_accept: bool = False,
    extra_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """
    Собирает заголовки для LM Studio API.

    Почему кладём и `Authorization`, и `x-api-key`:
    - официальный LM Studio contract принимает bearer token;
    - часть клиентов и промежуточных адаптеров ожидают именно `x-api-key`;
    - дублирование здесь безопасно и даёт максимально совместимое поведение.
    """
    headers = {str(key): str(value) for key, value in (extra_headers or {}).items()}
    if include_json_accept and "Accept" not in headers:
        headers["Accept"] = "application/json"

    token = _strip_wrapping_quotes(
        str(api_key if api_key is not None else resolve_lm_studio_api_key() or "")
    )
    if not token:
        return headers

    headers.setdefault("Authorization", f"Bearer {token}")
    headers.setdefault("x-api-key", token)
    return headers
