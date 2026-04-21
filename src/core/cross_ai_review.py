"""Cross-AI review helper (Chado §9 P2).

Prompt другого Claude-Code/AI коллегу с design-артефактом, собирай
structured feedback в bullet-list, эмит как Linear-style task items.

Public:
- async request_review(artifact_url, topic_key, prompt_context, timeout=600) -> ReviewResult
- parse_review_bullets(text) -> list[str]  # extract top-level bullets
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Regex для top-level bullet: строка начинается с "- ", "* " или "<N>. "
# Вложенные строки (с отступом) пропускаем.
_BULLET_RE = re.compile(r"^(?:-|\*|\d+\.)\s+(.+)", re.MULTILINE)
_INDENTED_RE = re.compile(r"^\s+(?:-|\*|\d+\.)\s+")


@dataclass(frozen=True)
class ReviewResult:
    ok: bool
    bullets: list[str]
    raw_text: str
    reviewer: str
    error: str | None = None


def parse_review_bullets(text: str) -> list[str]:
    """Extract top-level bullet points from markdown-ish text.

    Matches lines starting with '- ', '* ', or '<digit>.'. Strips.
    Nested bullets (indented) are skipped.
    """
    results: list[str] = []
    for line in text.splitlines():
        # Пропускаем вложенные (с отступом перед маркером)
        if _INDENTED_RE.match(line):
            continue
        m = _BULLET_RE.match(line)
        if m:
            stripped = m.group(1).strip()
            if stripped:
                results.append(stripped)
    return results


async def request_review(
    artifact_url: str,
    topic_key: str,
    prompt_context: str,
    *,
    timeout_sec: int = 600,
    swarm_channels: Any = None,  # injected for testability
) -> ReviewResult:
    """Post in Forum topic, wait for reply containing review link/reference.

    Отправляет запрос на ревью в Forum-топик свёрма и сразу возвращает
    ReviewResult(ok=True, bullets=[]).

    Pickup ответа — отдельная follow-up задача (P3):
    подписчик должен слушать Forum-топик и вызывать parse_review_bullets()
    на входящих сообщениях.

    Args:
        artifact_url: URL или path дизайн-артефакта для ревью.
        topic_key: ключ топика свёрма (например "coders", "analysts").
        prompt_context: контекст/инструкции для ревьюера.
        timeout_sec: таймаут ожидания (используется asyncio.wait_for при
                     реальном pickup).
        swarm_channels: объект с методом broadcast_to_topic(topic_key, text).
                        Если None — только логируем.

    Returns:
        ReviewResult с ok=True при успешной отправке или ok=False при ошибке.
    """
    message = (
        f"[Cross-AI Review Request]\n"
        f"Artifact: {artifact_url}\n\n"
        f"{prompt_context}\n\n"
        f"Пожалуйста, ответь структурированным списком замечаний (bullet-list)."
    )

    async def _do_broadcast() -> None:
        if swarm_channels is not None:
            await swarm_channels.broadcast_to_topic(topic_key, message)
        else:
            logger.info(
                "cross_ai_review_no_channel topic=%s url=%s",
                topic_key,
                artifact_url,
            )

    try:
        await asyncio.wait_for(_do_broadcast(), timeout=timeout_sec)
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning(
            "cross_ai_review_timeout topic=%s url=%s timeout=%ds",
            topic_key,
            artifact_url,
            timeout_sec,
        )
        return ReviewResult(
            ok=False,
            bullets=[],
            raw_text="",
            reviewer=topic_key,
            error=f"broadcast timeout after {timeout_sec}s",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "cross_ai_review_error topic=%s url=%s error=%s",
            topic_key,
            artifact_url,
            str(exc),
        )
        return ReviewResult(
            ok=False,
            bullets=[],
            raw_text="",
            reviewer=topic_key,
            error=str(exc),
        )

    logger.info(
        "cross_ai_review_sent topic=%s url=%s",
        topic_key,
        artifact_url,
    )
    return ReviewResult(
        ok=True,
        bullets=[],
        raw_text="",
        reviewer=topic_key,
    )
