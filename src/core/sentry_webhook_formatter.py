"""
Sentry webhook payload → Telegram message formatter.

Входящий формат — Sentry "Internal Integration" webhook alert rule action:
https://docs.sentry.io/organization/integrations/integration-platform/webhooks/issue-alerts/

Public:
- format_sentry_alert(payload) -> str | None
  Возвращает markdown-текст (<4096 симв) для отправки в Telegram,
  либо None если payload нерелевантный (например, resolved без контекста).

Principles:
- Лаконичность: заголовок + first-line + top-3 полей + link.
- PII-safety: обрезаем stacktrace (потенциальный leak через local var names).
- Deterministic: одинаковый payload → одинаковый output (для дедупликации).
"""

from __future__ import annotations

from typing import Any

# Максимум символов в Telegram-сообщении (оставляем буфер для markdown escapes).
_MAX_LEN = 3800


def _severity_emoji(level: str) -> str:
    """Маппинг level → emoji."""
    return {
        "fatal": "🔥",
        "error": "❌",
        "warning": "⚠️",
        "info": "ℹ️",
        "debug": "🐛",
    }.get(level.lower(), "⚡")


def _escape_md(text: str) -> str:
    """Minimal markdown-V1 escape (Telegram parse_mode=markdown)."""
    return text.replace("*", "").replace("_", "").replace("`", "")


def format_sentry_alert(payload: dict[str, Any]) -> str | None:
    """Формирует Telegram-сообщение из Sentry alert webhook payload.

    Поддерживает два типа payload:
    1. Issue alert (`action == "triggered"`) — есть `data.event` и `data.issue`.
    2. Metric alert — есть `data.metric_alert`.

    Args:
        payload: сырой JSON от Sentry.

    Returns:
        Отформатированный текст или None если payload не поддерживается.
    """
    if not isinstance(payload, dict):
        return None

    data = payload.get("data") or {}
    action = payload.get("action", "")

    # Issue alert ------------------------------------------------------------
    if "issue" in data or "event" in data:
        issue = data.get("issue") or {}
        event = data.get("event") or {}

        level = event.get("level") or issue.get("level") or "error"
        emoji = _severity_emoji(level)

        title = _escape_md((event.get("title") or issue.get("title") or "Unknown issue")[:200])
        culprit = _escape_md((event.get("culprit") or issue.get("culprit") or "")[:150])
        project = _escape_md(str(data.get("project") or issue.get("project") or "")[:50])
        env = _escape_md(str(event.get("environment") or "unknown")[:30])
        count = issue.get("count") or event.get("count") or 1
        users = issue.get("userCount") or 0

        link = issue.get("permalink") or issue.get("url") or ""

        lines = [
            f"{emoji} *Sentry alert* — {level.upper()}",
            f"[{project}] {title}",
        ]
        if culprit:
            lines.append(f"↳ `{culprit}`")
        lines.append(f"env: {env}  |  events: {count}  |  users: {users}")
        if action and action != "triggered":
            lines.append(f"action: {action}")
        if link:
            lines.append(link)

        result = "\n".join(lines)
        return result[:_MAX_LEN]

    # Metric alert -----------------------------------------------------------
    metric = data.get("metric_alert") or data.get("metricAlert")
    if metric:
        name = _escape_md(str(metric.get("title") or metric.get("name") or "")[:150])
        threshold = metric.get("thresholdType") or metric.get("threshold") or ""
        status = metric.get("status") or ""
        query = _escape_md(str(metric.get("aggregate") or "")[:100])
        value = metric.get("value") or metric.get("triggerValue") or "?"

        emoji = "🚨" if status == "critical" else "⚠️"
        lines = [
            f"{emoji} *Sentry metric* — {status.upper()}",
            f"{name}",
            f"value: {value}  |  threshold: {threshold}",
        ]
        if query:
            lines.append(f"query: `{query}`")

        return "\n".join(lines)[:_MAX_LEN]

    return None
