# -*- coding: utf-8 -*-
"""
CommandIntentExtractor (Wave 44-O-nlu) — natural-language → !command intent.

Цель: переводит описание владельца на естественном языке (RU/EN) в конкретную
Krab-команду с аргументами. Например:
    "запусти аналитиков на тему BTC за 2 раунда"
        → !swarm analysts loop 2 BTC  (confidence ~0.9)
    "проверь статус"
        → !status  (confidence ~0.85)
    "удали все задачи"
        → destructive guard → confidence < 0.8

Pipeline:
    1. Regex pre-pass: явный `!cmd ...` → confidence=1.0
    2. Regex/keyword templates для популярных !command-семейств (deterministic, fast).
    3. LM Studio fallback для сложных формулировок (structured JSON).
    4. Destructive-guard: any "delete/reset/wipe/удали/сброс" → cap confidence < 0.8.

Owner-only gating передаётся в `extract_command_intent(..., owner_only=True)`;
caller отвечает за проверку ID.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

LM_STUDIO_URL = "http://127.0.0.1:1234/v1/chat/completions"
LM_STUDIO_TIMEOUT = 3.0

# ---------- Destructive markers ----------
_DESTRUCTIVE_MARKERS = (
    "delete",
    "remove",
    "reset",
    "wipe",
    "drop",
    "purge",
    "clear all",
    "удали",
    "удалить",
    "сброс",
    "снеси",
    "очисти все",
    "очисти всё",
    "стереть",
    "стри",
)

# ---------- Known teams ----------
_TEAMS = ("traders", "coders", "analysts", "creative")
_TEAM_RU = {
    "трейдер": "traders",
    "трейдеры": "traders",
    "трейдеров": "traders",
    "кодер": "coders",
    "кодеры": "coders",
    "кодеров": "coders",
    "программист": "coders",
    "аналитик": "analysts",
    "аналитики": "analysts",
    "аналитиков": "analysts",
    "креатив": "creative",
    "креативы": "creative",
    "креативщик": "creative",
}


@dataclass
class CommandIntent:
    """Resolved natural-language → !command intent."""

    command: str  # e.g. "!swarm", "!status"
    subcommand: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    original_text: str = ""
    rendered: str = ""  # canonical "!swarm analysts loop 2 BTC"
    destructive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "subcommand": self.subcommand,
            "args": self.args,
            "confidence": self.confidence,
            "rendered": self.rendered,
            "destructive": self.destructive,
        }


# ============================================================
# Regex pre-pass
# ============================================================

_EXPLICIT_CMD_RE = re.compile(r"^\s*(!\w[\w-]*)\b(.*)$", re.DOTALL)


def _extract_explicit(text: str) -> CommandIntent | None:
    m = _EXPLICIT_CMD_RE.match(text)
    if not m:
        return None
    cmd = m.group(1).lower()
    rest = m.group(2).strip()
    parts = rest.split() if rest else []
    sub = parts[0] if parts and not parts[0].startswith("-") else None
    return CommandIntent(
        command=cmd,
        subcommand=sub,
        args={"raw": rest},
        confidence=1.0,
        original_text=text,
        rendered=text.strip(),
    )


# ============================================================
# Keyword templates
# ============================================================


def _detect_team(text: str) -> str | None:
    lower = text.lower()
    for team in _TEAMS:
        if team in lower:
            return team
    for ru, eng in _TEAM_RU.items():
        if ru in lower:
            return eng
    return None


_NUM_WORDS_RU = {
    "один": 1,
    "одна": 1,
    "одного": 1,
    "одну": 1,
    "два": 2,
    "две": 2,
    "двух": 2,
    "три": 3,
    "трёх": 3,
    "трех": 3,
    "четыре": 4,
    "четырёх": 4,
    "четырех": 4,
    "пять": 5,
    "пяти": 5,
}


def _detect_count(text: str) -> int | None:
    """Extract round/loop count: 'за 2 раунда', 'loop 3', 'три раза'."""
    lower = text.lower()
    # numeric: "за 2", "loop 3", "2 раунд", "2 round"
    m = re.search(r"\b(\d{1,3})\s*(?:раунд|раз|круг|round|loop|iter)", lower)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(?:за|loop|раунд[ао]в?)\s+(\d{1,3})\b", lower)
    if m:
        return int(m.group(1))
    for word, num in _NUM_WORDS_RU.items():
        if re.search(rf"\b{word}\s+(?:раунд|раз|круг)", lower):
            return num
    return None


def _detect_topic(text: str, *, team: str | None) -> str | None:
    """Guess topic — что после 'на тему', 'по', 'about'."""
    patterns = [
        r"на\s+тему\s+(.+?)(?:\s+за\s+\d|\s+на\s+\d|$)",
        r"по\s+теме\s+(.+?)(?:\s+за\s+\d|\s+на\s+\d|$)",
        r"\babout\s+(.+?)(?:\s+for\s+\d|$)",
        r"\bпо\s+([A-ZА-Я][\w/-]+)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            topic = m.group(1).strip().rstrip(".,;:!?")
            # strip trailing "за N раундов"
            topic = re.sub(r"\s+за\s+\d+\s*\w*$", "", topic, flags=re.IGNORECASE)
            if topic and len(topic) < 200:
                return topic
    # ALL-CAPS ticker fallback (BTC, ETH, NVDA)
    m = re.search(r"\b([A-Z]{2,6})\b", text)
    if m:
        return m.group(1)
    return None


def _try_swarm(text: str) -> CommandIntent | None:
    lower = text.lower()
    swarm_kw = any(
        kw in lower
        for kw in (
            "запусти",
            "запустить",
            "позови",
            "созови",
            "собери",
            "swarm",
            "team",
            "команд",
            "ройник",
        )
    )
    team = _detect_team(text)
    if not (swarm_kw or team):
        return None
    if not team:
        return None
    count = _detect_count(text) or 1
    topic = _detect_topic(text, team=team) or ""
    rendered = f"!swarm {team} loop {count}"
    if topic:
        rendered += f" {topic}"
    confidence = 0.9 if (team and topic) else 0.75 if team else 0.5
    return CommandIntent(
        command="!swarm",
        subcommand=team,
        args={"team": team, "count": count, "topic": topic},
        confidence=confidence,
        original_text=text,
        rendered=rendered.strip(),
    )


def _try_status(text: str) -> CommandIntent | None:
    lower = text.lower()
    pats = (
        r"\bпровер[ьи]\s+статус\b",
        r"\bкак\s+(?:дела|здоровье)\s+(?:бот|систем)",
        r"\bstatus\s*(?:check|please)?\b",
        r"\bкак\s+там\s+(?:бот|краб)\b",
        r"\bпокажи\s+статус\b",
        r"\bstate\s+of\s+(?:the\s+)?(?:bot|system)\b",
    )
    for p in pats:
        if re.search(p, lower):
            return CommandIntent(
                command="!status",
                args={},
                confidence=0.85,
                original_text=text,
                rendered="!status",
            )
    return None


def _try_quota(text: str) -> CommandIntent | None:
    lower = text.lower()
    if re.search(r"квот|\bquota\b|\bлимит|расход моделей|\busage\b", lower):
        return CommandIntent(
            command="!quota",
            args={},
            confidence=0.85,
            original_text=text,
            rendered="!quota",
        )
    return None


def _try_proactive(text: str) -> CommandIntent | None:
    lower = text.lower()
    m = re.search(r"\bproactive\b|\bпроактивн", lower)
    if not m:
        return None
    state = None
    if re.search(r"\b(?:on|вкл)|включ", lower):
        state = "on"
    elif re.search(r"\boff\b|выключ|откл|\bвыкл", lower):
        state = "off"
    elif re.search(r"\b(?:status|статус)", lower):
        state = "status"
    if not state:
        return None
    return CommandIntent(
        command="!proactive",
        subcommand=state,
        args={"state": state},
        confidence=0.85,
        original_text=text,
        rendered=f"!proactive {state}",
    )


def _try_memory_recall(text: str) -> CommandIntent | None:
    lower = text.lower()
    m = re.match(
        r"^(?:вспомни|recall|найди\s+в\s+памяти|search\s+memory)[:\s]+(.+)$",
        lower,
    )
    if m:
        q = m.group(1).strip()
        return CommandIntent(
            command="!memory",
            subcommand="recall",
            args={"query": q},
            confidence=0.8,
            original_text=text,
            rendered=f"!memory recall {q}",
        )
    return None


def _try_cron(text: str) -> CommandIntent | None:
    lower = text.lower()
    if re.search(r"\bcron\b|\bрасписан|по расписан|каждый день|каждое утро", lower):
        # Coarse extraction — caller may refine via LLM.
        return CommandIntent(
            command="!cron",
            subcommand="schedule",
            args={"raw": text},
            confidence=0.55,
            original_text=text,
            rendered="!cron schedule",
        )
    return None


_TEMPLATE_FUNCS = (
    _try_swarm,
    _try_status,
    _try_quota,
    _try_proactive,
    _try_memory_recall,
    _try_cron,
)


# ============================================================
# Destructive guard
# ============================================================


def _is_destructive(text: str) -> bool:
    lower = text.lower()
    return any(m in lower for m in _DESTRUCTIVE_MARKERS)


# ============================================================
# LLM fallback (LM Studio structured JSON)
# ============================================================


_LLM_SYS = """Ты — экстрактор Krab-команд. Преобразуй фразу владельца в JSON.
Доступные команды: !swarm, !cron, !proactive, !status, !quota, !memory.
Если фраза не похожа на команду — верни confidence < 0.4.
Ответ СТРОГО JSON: {"command": "!xxx", "subcommand": "...", "args": {...}, "confidence": 0..1, "rendered": "!xxx ..."}"""


async def _llm_extract(text: str, *, timeout: float = LM_STUDIO_TIMEOUT) -> CommandIntent | None:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                LM_STUDIO_URL,
                json={
                    "model": "auto",
                    "messages": [
                        {"role": "system", "content": _LLM_SYS},
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 200,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                parts = content.split("```")
                if len(parts) >= 2:
                    content = parts[1]
                    if content.startswith("json"):
                        content = content[4:]
                    content = content.strip()
            parsed = json.loads(content)
            cmd = str(parsed.get("command") or "").strip()
            if not cmd or not cmd.startswith("!"):
                return None
            conf = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
            return CommandIntent(
                command=cmd,
                subcommand=parsed.get("subcommand"),
                args=parsed.get("args") or {},
                confidence=conf,
                original_text=text,
                rendered=str(parsed.get("rendered") or cmd),
            )
    except Exception as exc:
        logger.debug("command_intent_llm_fallback_failed", error=str(exc))
        return None


# ============================================================
# Public API
# ============================================================


async def extract_command_intent(
    text: str,
    *,
    owner_only: bool = True,
    is_owner: bool = True,
    use_llm: bool = False,
    min_confidence: float = 0.4,
) -> CommandIntent | None:
    """Extract command intent from natural-language text.

    Args:
        text: raw user message
        owner_only: if True, require is_owner=True; else returns None
        is_owner: caller must pass actual owner-check result
        use_llm: enable LM Studio fallback (default False, keeps tests pure)
        min_confidence: drop intents below this floor

    Returns:
        CommandIntent or None.
    """
    if not text or not text.strip():
        return None
    if owner_only and not is_owner:
        return None

    # 1. Explicit `!cmd`
    explicit = _extract_explicit(text)
    if explicit:
        explicit.destructive = _is_destructive(text)
        return explicit

    # 2. Keyword templates
    for fn in _TEMPLATE_FUNCS:
        intent = fn(text)
        if intent:
            intent.destructive = _is_destructive(text)
            if intent.destructive and intent.confidence >= 0.8:
                # destructive guard — cap to require confirmation
                intent.confidence = 0.7
            if intent.confidence >= min_confidence:
                return intent

    # 3. LLM fallback
    if use_llm:
        llm_intent = await _llm_extract(text)
        if llm_intent and llm_intent.confidence >= min_confidence:
            llm_intent.destructive = _is_destructive(text)
            if llm_intent.destructive and llm_intent.confidence >= 0.8:
                llm_intent.confidence = 0.7
            return llm_intent

    return None
