"""Hallucination guard for fabricated tool-success reports.

Detects when an LLM response claims to have performed an action (e.g.,
"Отправил сообщение", "delivered, message id 1677") while no actual
tool call was registered in `openclaw_client._active_tool_calls`.

Conservative by design: false positives are worse than false negatives —
we only warn (prepend a notice), never block the response.

Bug 33-A (Wave 9-A): root-causes user-visible incident where codex-cli/gpt-5.5
returned "Отправил в личку Дашке: 'Ты какашка 🦀'\n\nДоставка прошла успешно:
chat id 1467625424, message id 1677" while no telegram_send_message tool
was actually invoked — pure hallucination.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping

# Phrases that strongly imply a write/action tool just succeeded.
# Matched on lowercase response text. Conservative: each pattern requires
# at least one Telegram/IM-action keyword, so generic "сделал презентацию"
# in a creative context won't trigger.
_TOOL_SUCCESS_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Russian
    re.compile(r"\bотправил[аи]?\s+(в\s+)?(лич|дм|чат|пользовател|сообщ)", re.IGNORECASE),
    re.compile(r"\bдоставка\s+прошла\s+успешно\b", re.IGNORECASE),
    re.compile(r"\bсообщение\s+отправлено\b", re.IGNORECASE),
    re.compile(r"\bвыполнено\s+успешно\b", re.IGNORECASE),
    re.compile(r"\bотправка\s+(прошла\s+)?успеш", re.IGNORECASE),
    # English
    re.compile(r"\bsent\s+(to|the\s+message|message\s+to)\b", re.IGNORECASE),
    re.compile(r"\bdelivered\s+(to|successfully)\b", re.IGNORECASE),
    re.compile(r"\bmessage\s+(was\s+)?sent\b", re.IGNORECASE),
    # Structured-data tells: an LLM hallucinating a tool result often invents
    # numeric "message id N" / "chat id N" / "msg_id: N" pairs.
    re.compile(r"\bmessage\s+id\s*[:#]?\s*\d{2,}", re.IGNORECASE),
    re.compile(r"\bmsg[_\s]?id\s*[:=#]?\s*\d{2,}", re.IGNORECASE),
)

# Tool-name substrings that indicate a real write/send action did occur.
# Matched as substrings on lowercased tool entry "name".
_WRITE_TOOL_KEYWORDS: tuple[str, ...] = (
    "send_message",
    "send_photo",
    "send_voice",
    "send_reaction",
    "edit_message",
    "forward_message",
    "pin_message",
    "delete_message",
    "telegram_send",
    "imessage_send",
    "send_imessage",
    "notes_create",
    "reminders_create",
    "calendar_create",
)


def _is_write_tool_call(entry: Mapping[str, object]) -> bool:
    """Return True if entry looks like a successful write/action tool call."""
    name = str(entry.get("name") or "").lower()
    status = str(entry.get("status") or "").lower()
    if not name:
        return False
    if status not in ("done", "ok", "success"):
        return False
    return any(kw in name for kw in _WRITE_TOOL_KEYWORDS)


def detect_hallucinated_tool_success(
    response_text: str,
    active_tool_calls_snapshot: Iterable[Mapping[str, object]] | None,
) -> bool:
    """Return True if the response claims tool-success but no write tool ran.

    Args:
        response_text: Final LLM response that would be sent to user.
        active_tool_calls_snapshot: Iterable of tool-call dicts (from
            openclaw_client._active_tool_calls) — entries should have
            "name" and "status" keys.

    Returns:
        True iff response matches a tool-success pattern AND the snapshot
        contains zero successful write-tool entries. False otherwise.
    """
    if not response_text or not isinstance(response_text, str):
        return False

    matched = any(p.search(response_text) for p in _TOOL_SUCCESS_PATTERNS)
    if not matched:
        return False

    # Convert snapshot to a list to allow iteration even if generator.
    entries = list(active_tool_calls_snapshot or [])
    has_write_tool = any(_is_write_tool_call(e) for e in entries if isinstance(e, Mapping))
    if has_write_tool:
        return False  # Legitimate success — real tool was invoked.

    return True


HALLUCINATION_WARNING_PREFIX = (
    "⚠️ Внимание: похоже LLM «сообщает» об отправке, "
    "но реальный вызов tool не зафиксирован. Проверь outgoing DMs.\n\n"
)


__all__ = [
    "HALLUCINATION_WARNING_PREFIX",
    "detect_hallucinated_tool_success",
]
