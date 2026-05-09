# -*- coding: utf-8 -*-
"""
Wave 52-C-audit-analyzer: aggregator + suspicious-pattern detector.

Read-only анализатор двух audit-логов:

- ``/tmp/krab_bash_audit.log`` — verdict-журнал bash_guard.sh (Wave 44-S):
  ``{"ts","verdict","reason","cmd","ppid"}`` где ``verdict`` ∈
  ``{ALLOW, NEEDS_CONFIRM, BLOCK}``.
- ``~/.openclaw/krab_runtime_state/agent_audit.jsonl`` — multi-channel
  agent actions: ``{"ts","channel","recipient","action","ok",...}``.

Класс :class:`AuditAnalyzer` собирает агрегаты за окно (минуты) и
эвристически детектирует подозрительные паттерны:

- **high_block_rate** — >10 BLOCK за час.
- **money_keyword_burst** — >3 NEEDS_CONFIRM с money-keywords за 10 минут.
- **first_time_burst** — >5 first_time_blocks за час.
- **late_night_activity** — события между 02:00–06:00 owner local time.

Запись в логи **не выполняется** — анализатор только читает.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from datetime import time as _time
from pathlib import Path
from typing import Any

# ── Пути по умолчанию (можно подменить через env / конструктор) ──────────────

DEFAULT_BASH_LOG = Path(os.environ.get("KRAB_BASH_AUDIT_LOG", "/tmp/krab_bash_audit.log"))
DEFAULT_AGENT_LOG = Path(
    os.environ.get(
        "KRAB_AGENT_AUDIT_LOG",
        os.path.expanduser("~/.openclaw/krab_runtime_state/agent_audit.jsonl"),
    )
)

# Money-keywords для денежных эвристик (RU + EN). Совпадение по reason/cmd.
_MONEY_KEYWORDS = (
    "money",
    "payment",
    "pay",
    "transfer",
    "wire",
    "iban",
    "card",
    "credit",
    "btc",
    "eth",
    "crypto",
    "wallet",
    "деньг",
    "оплат",
    "перевод",
    "карта",
    "кошел",
    "крипт",
)

# Late-night окно (owner local time): 02:00–06:00.
_LATE_NIGHT_START = _time(2, 0)
_LATE_NIGHT_END = _time(6, 0)


# ── ts parser ────────────────────────────────────────────────────────────────


def _parse_ts(raw: str) -> datetime | None:
    """Парсит ISO-8601 ts в обоих диалектах (Z-suffix и +HHMM offset).

    bash_guard.sh пишет ``2026-05-09T19:37:25Z``. multi_channel_helpers
    пишет ``2026-05-09T21:49:01+0200``. Возвращает aware-datetime в UTC,
    либо ``None`` для malformed строк.
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    # Нормализуем Z → +00:00 для fromisoformat.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Pyrogram-стиль "+0200" → "+02:00" (fromisoformat в Python 3.11+ поддерживает,
    # но мы добавим colon для совместимости).
    m = re.search(r"([+-])(\d{2})(\d{2})$", s)
    if m and s[-5] in ("+", "-"):
        s = s[:-5] + f"{m.group(1)}{m.group(2)}:{m.group(3)}"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_money_text(text: str) -> bool:
    """True, если строка содержит money-keyword (case-insensitive)."""
    if not text:
        return False
    low = text.lower()
    return any(kw in low for kw in _MONEY_KEYWORDS)


# ── data classes ─────────────────────────────────────────────────────────────


@dataclass
class _BashEvent:
    ts: datetime
    verdict: str
    reason: str
    cmd: str


@dataclass
class _AgentEvent:
    ts: datetime
    channel: str
    recipient: str
    action: str
    ok: bool
    reason: str


# ── analyzer ─────────────────────────────────────────────────────────────────


class AuditAnalyzer:
    """Read-only анализатор audit-логов Krab.

    Args:
        bash_log_path: путь к ``krab_bash_audit.log`` (JSONL).
        agent_log_path: путь к ``agent_audit.jsonl``.
        owner_local_tz: timezone владельца для late-night detection. По умолчанию
            используется локальная TZ системы.
    """

    def __init__(
        self,
        bash_log_path: Path | str | None = None,
        agent_log_path: Path | str | None = None,
        owner_local_tz: timezone | None = None,
    ) -> None:
        self.bash_log_path = Path(bash_log_path) if bash_log_path else DEFAULT_BASH_LOG
        self.agent_log_path = Path(agent_log_path) if agent_log_path else DEFAULT_AGENT_LOG
        # Если не передано — используем локальную TZ через astimezone().
        self._owner_tz = owner_local_tz

    # ── log readers ──────────────────────────────────────────────────────────

    def _read_bash_events(self, since_utc: datetime) -> list[_BashEvent]:
        """Читает bash audit log, фильтрует по окну. Malformed → skip."""
        out: list[_BashEvent] = []
        if not self.bash_log_path.exists():
            return out
        try:
            raw_lines = self.bash_log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        for raw in raw_lines:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if not isinstance(rec, dict):
                    continue
            except (ValueError, TypeError):
                continue
            ts = _parse_ts(str(rec.get("ts", "")))
            if ts is None or ts < since_utc:
                continue
            verdict = str(rec.get("verdict", "")).upper()
            if verdict not in {"ALLOW", "NEEDS_CONFIRM", "BLOCK"}:
                continue
            out.append(
                _BashEvent(
                    ts=ts,
                    verdict=verdict,
                    reason=str(rec.get("reason", "")),
                    cmd=str(rec.get("cmd", "")),
                )
            )
        return out

    def _read_agent_events(self, since_utc: datetime) -> list[_AgentEvent]:
        """Читает agent audit log, фильтрует по окну. Malformed → skip."""
        out: list[_AgentEvent] = []
        if not self.agent_log_path.exists():
            return out
        try:
            raw_lines = self.agent_log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        for raw in raw_lines:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if not isinstance(rec, dict):
                    continue
            except (ValueError, TypeError):
                continue
            ts = _parse_ts(str(rec.get("ts", "")))
            if ts is None or ts < since_utc:
                continue
            out.append(
                _AgentEvent(
                    ts=ts,
                    channel=str(rec.get("channel", "")),
                    recipient=str(rec.get("recipient", "")),
                    action=str(rec.get("action", "")),
                    ok=bool(rec.get("ok", False)),
                    reason=str(rec.get("reason", "")),
                )
            )
        return out

    # ── public API ───────────────────────────────────────────────────────────

    def analyze_recent(self, window_minutes: int = 60) -> dict[str, Any]:
        """Собирает агрегаты + alerts за последние ``window_minutes`` минут.

        Возвращает dict с ключами:

        - ``ok`` (bool): всегда True (graceful — пустые логи → нули).
        - ``window_minutes`` (int): эхо-параметр.
        - ``bash_audit`` (dict): агрегаты по ALLOW/NEEDS_CONFIRM/BLOCK.
        - ``agent_audit`` (dict): агрегаты по channel + action.
        - ``alerts`` (list[dict]): список detected suspicious-patterns.
        """
        if window_minutes <= 0:
            window_minutes = 60
        now_utc = datetime.now(tz=timezone.utc)
        since_utc = now_utc.replace(microsecond=0) - _td(minutes=window_minutes)

        bash_events = self._read_bash_events(since_utc)
        agent_events = self._read_agent_events(since_utc)

        # ── bash aggregates ──────────────────────────────────────────────────
        verdict_counter: Counter[str] = Counter(ev.verdict for ev in bash_events)
        block_reasons = Counter(ev.reason for ev in bash_events if ev.verdict == "BLOCK")
        confirm_reasons = Counter(ev.reason for ev in bash_events if ev.verdict == "NEEDS_CONFIRM")
        bash_summary = {
            "total_events": len(bash_events),
            "allow": int(verdict_counter.get("ALLOW", 0)),
            "needs_confirm": int(verdict_counter.get("NEEDS_CONFIRM", 0)),
            "block": int(verdict_counter.get("BLOCK", 0)),
            "top_blocked_patterns": [
                {"reason": r, "count": c} for r, c in block_reasons.most_common(5)
            ],
            "top_confirmed_patterns": [
                {"reason": r, "count": c} for r, c in confirm_reasons.most_common(5)
            ],
        }

        # ── agent aggregates ────────────────────────────────────────────────
        by_channel: Counter[str] = Counter(ev.channel for ev in agent_events)
        by_action: Counter[str] = Counter(ev.action for ev in agent_events)
        first_time_blocks = sum(1 for ev in agent_events if ev.action == "first_time_blocked")
        agent_summary = {
            "total_events": len(agent_events),
            "by_channel": dict(by_channel),
            "by_action": dict(by_action),
            "first_time_blocks": first_time_blocks,
        }

        # ── suspicious patterns ─────────────────────────────────────────────
        alerts = self._detect_suspicious(bash_events, agent_events, window_minutes, now_utc)

        return {
            "ok": True,
            "window_minutes": window_minutes,
            "bash_audit": bash_summary,
            "agent_audit": agent_summary,
            "alerts": alerts,
        }

    def detect_suspicious_patterns(self, window_minutes: int = 60) -> list[dict[str, Any]]:
        """Возвращает только список alerts (без агрегатов)."""
        return list(self.analyze_recent(window_minutes).get("alerts", []))

    # ── heuristics ───────────────────────────────────────────────────────────

    def _detect_suspicious(
        self,
        bash_events: list[_BashEvent],
        agent_events: list[_AgentEvent],
        window_minutes: int,
        now_utc: datetime,
    ) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []

        # 1) high_block_rate: >10 BLOCK за час (нормализуем порог под окно).
        block_count = sum(1 for ev in bash_events if ev.verdict == "BLOCK")
        block_threshold = max(1, int(round(10 * window_minutes / 60.0)))
        if block_count > block_threshold:
            alerts.append(
                {
                    "severity": "warning",
                    "kind": "high_block_rate",
                    "details": (
                        f"{block_count} BLOCK verdicts in last {window_minutes}m "
                        f"(threshold {block_threshold})"
                    ),
                }
            )

        # 2) money_keyword_burst: >3 NEEDS_CONFIRM с money keywords за 10 мин.
        ten_min_ago = now_utc - _td(minutes=10)
        money_confirms = [
            ev
            for ev in bash_events
            if ev.verdict == "NEEDS_CONFIRM"
            and ev.ts >= ten_min_ago
            and (_is_money_text(ev.reason) or _is_money_text(ev.cmd))
        ]
        if len(money_confirms) > 3:
            alerts.append(
                {
                    "severity": "warning",
                    "kind": "money_keyword_burst",
                    "details": (f"{len(money_confirms)} money-related NEEDS_CONFIRM in last 10m"),
                }
            )

        # 3) first_time_burst: >5 first_time_blocks за час (нормализуем под окно).
        ftb_count = sum(1 for ev in agent_events if ev.action == "first_time_blocked")
        ftb_threshold = max(1, int(round(5 * window_minutes / 60.0)))
        if ftb_count > ftb_threshold:
            alerts.append(
                {
                    "severity": "info",
                    "kind": "first_time_burst",
                    "details": (
                        f"{ftb_count} first_time_blocked recipients in last "
                        f"{window_minutes}m (threshold {ftb_threshold})"
                    ),
                }
            )

        # 4) late_night_activity: события между 02:00-06:00 owner local time.
        late_count = 0
        for ev in bash_events:
            if self._is_late_night(ev.ts):
                late_count += 1
        for ev in agent_events:
            if self._is_late_night(ev.ts):
                late_count += 1
        if late_count > 0:
            alerts.append(
                {
                    "severity": "info",
                    "kind": "late_night_activity",
                    "details": f"{late_count} events between 02:00-06:00 local time",
                }
            )

        return alerts

    def _is_late_night(self, ts_utc: datetime) -> bool:
        """True если ts (в owner local TZ) попадает в [02:00, 06:00)."""
        if self._owner_tz is not None:
            local = ts_utc.astimezone(self._owner_tz)
        else:
            # astimezone() без аргумента → локальная TZ системы.
            local = ts_utc.astimezone()
        t = local.timetz().replace(tzinfo=None)
        return _LATE_NIGHT_START <= t < _LATE_NIGHT_END


# ── helper: timedelta без import на module-level (минимизируем surface) ─────


def _td(*, minutes: float):
    """Локальный обёртка над ``datetime.timedelta`` (читабельность аргументов)."""
    from datetime import timedelta

    return timedelta(minutes=minutes)
