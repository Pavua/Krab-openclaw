"""Wave 23-C: Anthropic Claude через Vertex AI direct bypass.

Минует OpenClaw broken transport. Использует AnthropicVertex client из
anthropic[vertex] package, который под капотом дёргает Vertex AI API
с Anthropic schemas.

Поддерживаемые модели:
- anthropic-vertex/claude-opus-4-7
- anthropic-vertex/claude-sonnet-4-6
- anthropic-vertex/claude-opus-4-6
- anthropic-vertex/claude-opus-4-5
- anthropic-vertex/claude-haiku-4-5
- anthropic-vertex/claude-sonnet-4-5

Auth: ADC (~/.config/gcloud/application_default_credentials.json) с
quota_project_id=caramel-anvil-492816-t5.
Region: us-east5 (где Anthropic выкладывает Claude на Vertex).

ENV gates:
- KRAB_ANTHROPIC_VERTEX_BYPASS_ENABLED=1 (default ON)
- KRAB_ANTHROPIC_VERTEX_PROJECT (default caramel-anvil-492816-t5)
- KRAB_ANTHROPIC_VERTEX_REGION (default us-east5)
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from ..core.logger import get_logger

logger = get_logger(__name__)

# Префикс провайдера для Anthropic-via-Vertex моделей
ANTHROPIC_VERTEX_PREFIX = "anthropic-vertex/"
DEFAULT_PROJECT = "caramel-anvil-492816-t5"
DEFAULT_REGION = "us-east5"  # регион где Anthropic публикует Claude на Vertex


def is_anthropic_vertex_enabled() -> bool:
    """Включён ли Anthropic Vertex bypass. Default ON (opt-out через env=0)."""
    return os.environ.get("KRAB_ANTHROPIC_VERTEX_BYPASS_ENABLED", "1").strip() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_anthropic_vertex_model(model: str) -> bool:
    """True если модель имеет префикс anthropic-vertex/."""
    return bool(model) and model.startswith(ANTHROPIC_VERTEX_PREFIX)


def _strip_prefix(model: str) -> str:
    """'anthropic-vertex/claude-opus-4-7' → 'claude-opus-4-7'."""
    if model.startswith(ANTHROPIC_VERTEX_PREFIX):
        return model[len(ANTHROPIC_VERTEX_PREFIX) :]
    return model


def _build_messages_for_anthropic(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Разделяет messages на system prompt и chat-messages для Anthropic API.

    Anthropic API принимает system как отдельный top-level kwarg,
    а messages — только role=user/assistant.
    Tool/function роли пропускаем — bypass упрощённый, без tool-calls.

    Args:
        messages: входной список {"role": ..., "content": ...}

    Returns:
        (system_str | None, chat_messages) — system None если не было system-роли.
    """
    system_parts: list[str] = []
    chat_messages: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        # multimodal payload — берём только текстовые части
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        content = str(content)

        if role == "system":
            system_parts.append(content)
        elif role in ("user", "assistant"):
            chat_messages.append({"role": role, "content": content})
        # tool/function роли игнорируем в bypass-режиме

    # Anthropic требует хотя бы одно сообщение в messages
    if not chat_messages:
        chat_messages = [{"role": "user", "content": ""}]

    system = "\n\n".join(system_parts) if system_parts else None
    return system, chat_messages


async def complete_via_anthropic_vertex(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float = 0.7,
    max_tokens: int = 8192,
    project: str | None = None,
    region: str | None = None,
) -> str:
    """Прямой Anthropic-via-Vertex call. Возвращает text.

    Sync AnthropicVertex client оборачивается в asyncio.to_thread
    (аналогично Wave 23-A Vertex bypass).

    Args:
        model: e.g. 'anthropic-vertex/claude-opus-4-7' или bare 'claude-opus-4-7'
        messages: список {"role": "user"|"assistant"|"system", "content": str}
        temperature: temperature генерации (default 0.7)
        max_tokens: лимит токенов в ответе (default 8192)
        project: GCP project override (default из env / DEFAULT_PROJECT)
        region: Vertex region override (default из env / DEFAULT_REGION)

    Returns:
        text ответа (str). Пустая строка если ответа нет.

    Raises:
        ImportError: если anthropic[vertex] SDK не установлен
        Exception: пробрасывает любые ошибки SDK / ADC выше
    """
    from anthropic import AnthropicVertex  # noqa: PLC0415

    proj = project or os.environ.get("KRAB_ANTHROPIC_VERTEX_PROJECT") or DEFAULT_PROJECT
    reg = region or os.environ.get("KRAB_ANTHROPIC_VERTEX_REGION") or DEFAULT_REGION
    bare_model = _strip_prefix(model)

    system, chat_messages = _build_messages_for_anthropic(messages)

    def _sync_call() -> str:
        # AnthropicVertex использует ADC для auth — явный api_key не нужен
        client = AnthropicVertex(region=reg, project_id=proj)
        kwargs: dict[str, Any] = {
            "model": bare_model,
            "max_tokens": max_tokens,
            "messages": chat_messages,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        resp = client.messages.create(**kwargs)
        # Anthropic SDK: resp.content — список ContentBlock, берём первый TextBlock
        if resp.content and len(resp.content) > 0:
            return resp.content[0].text or ""
        return ""

    text = await asyncio.to_thread(_sync_call)
    logger.info(
        "anthropic_vertex_complete_done",
        model=bare_model,
        region=reg,
        project=proj,
        length=len(text),
    )
    return text
