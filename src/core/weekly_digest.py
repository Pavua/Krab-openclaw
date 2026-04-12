# -*- coding: utf-8 -*-
"""
weekly_digest.py — еженедельная сводка активности Krab.

Собирает данные за последние 7 дней:
- swarm rounds по командам + top artifacts;
- cost за неделю;
- inbox errors / attention items.

Записывает digest-item в inbox_service (аналогично ErrorDigest).
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from ..core.cost_analytics import cost_analytics
from .inbox_service import inbox_service
from .logger import get_logger
from .swarm_artifact_store import swarm_artifact_store

logger = get_logger(__name__)

# Интервал Weekly Digest в секундах (7 дней)
WEEKLY_DIGEST_INTERVAL_SEC: int = 604800
# Максимум attention items в сводке
WEEKLY_DIGEST_MAX_ATTENTION: int = 10
# Максимум top artifacts на команду
WEEKLY_DIGEST_TOP_ARTIFACTS: int = 3


def _now_utc_iso() -> str:
    """Возвращает UTC timestamp для digest."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _week_ago_ts() -> float:
    """Unix timestamp ровно 7 дней назад."""
    return time.time() - 7 * 24 * 3600


class WeeklyDigestService:
    """
    Еженедельная сводка активности Krab.

    Паттерн идентичен ProactiveWatchService.run_error_digest:
    собирает данные тихо, пишет digest в inbox, деградирует без исключений.
    """

    # Публичный интервал для удобства подключения в proactive_watch
    INTERVAL_SEC: int = WEEKLY_DIGEST_INTERVAL_SEC

    async def generate_digest(self) -> dict[str, Any]:
        """
        Собирает данные за 7 дней и записывает weekly digest в inbox.

        Не бросает исключений — деградирует тихо при любых сбоях.
        Возвращает словарь с результатом: ok, swarm_rounds, cost_usd, attention_count.
        """
        ts_now = _now_utc_iso()
        week_start = _week_ago_ts()

        # --- Swarm: rounds по командам и top artifacts ---
        swarm_data = self._collect_swarm_data(week_start)

        # --- Cost за неделю ---
        cost_data = self._collect_cost_data(week_start)

        # --- Inbox: attention items и ошибки ---
        inbox_data = self._collect_inbox_data()

        # --- Формируем markdown-отчёт ---
        body = self._render_digest(
            ts_now=ts_now,
            swarm_data=swarm_data,
            cost_data=cost_data,
            inbox_data=inbox_data,
        )

        total_rounds = swarm_data.get("total_rounds", 0)
        attention_count = inbox_data.get("attention_count", 0)
        cost_usd = cost_data.get("cost_week_usd", 0.0)

        try:
            # dedupe_key по неделе: уникален по дате начала недели (понедельник ISO)
            week_label = datetime.fromtimestamp(week_start, tz=timezone.utc).strftime("%Y-W%V")
            inbox_service.upsert_item(
                dedupe_key=f"weekly_digest:{week_label}",
                kind="proactive_action",
                source="krab-internal",
                title=f"Weekly Digest: {total_rounds} swarm rounds, ${cost_usd:.3f}",
                body=body,
                severity="info",
                status="open",
                identity=inbox_service.build_identity(
                    channel_id="system",
                    team_id="owner",
                    trace_id="weekly_digest",
                    approval_scope="owner",
                ),
                metadata={
                    "action_type": "weekly_digest",
                    "total_rounds": total_rounds,
                    "cost_week_usd": cost_usd,
                    "attention_count": attention_count,
                    "digest_ts": ts_now,
                    "swarm_by_team": swarm_data.get("by_team", {}),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("weekly_digest_upsert_failed", error=str(exc))
            return {"ok": False, "error": str(exc)}

        logger.info(
            "weekly_digest_written",
            total_rounds=total_rounds,
            cost_usd=cost_usd,
            attention_count=attention_count,
        )
        return {
            "ok": True,
            "total_rounds": total_rounds,
            "cost_week_usd": cost_usd,
            "attention_count": attention_count,
            "digest_ts": ts_now,
        }

    def _collect_swarm_data(self, week_start_ts: float) -> dict[str, Any]:
        """
        Возвращает статистику swarm rounds за последние 7 дней.

        rounds по командам + top artifacts (тема + timestamp).
        Деградирует тихо при ошибке swarm_artifact_store.
        """
        try:
            # Берём с запасом (200 артефактов), фильтруем по времени
            all_artifacts = swarm_artifact_store.list_artifacts(limit=200)
        except Exception as exc:  # noqa: BLE001
            logger.warning("weekly_digest_swarm_list_failed", error=str(exc))
            return {"total_rounds": 0, "by_team": {}, "top_artifacts": []}

        # Фильтрация по неделе
        week_artifacts = [a for a in all_artifacts if float(a.get("timestamp", 0)) >= week_start_ts]

        by_team: dict[str, int] = defaultdict(int)
        for a in week_artifacts:
            team = str(a.get("team") or "unknown").lower()
            by_team[team] += 1

        # Top artifacts: берём последние WEEKLY_DIGEST_TOP_ARTIFACTS штук (список новые→старые)
        top_artifacts = [
            {
                "team": str(a.get("team") or ""),
                "topic": str(a.get("topic") or "")[:80],
                "timestamp_iso": str(a.get("timestamp_iso") or ""),
            }
            for a in week_artifacts[:WEEKLY_DIGEST_TOP_ARTIFACTS]
        ]

        return {
            "total_rounds": len(week_artifacts),
            "by_team": dict(by_team),
            "top_artifacts": top_artifacts,
        }

    def _collect_cost_data(self, week_start_ts: float) -> dict[str, Any]:
        """
        Возвращает стоимость вызовов модели за последние 7 дней.

        Использует cost_analytics._calls (in-memory). При ошибке — возвращает 0.
        """
        try:
            week_calls = [r for r in cost_analytics._calls if r.timestamp >= week_start_ts]
            cost_usd = sum(r.cost_usd for r in week_calls)
            total_tokens = sum(r.input_tokens + r.output_tokens for r in week_calls)
            calls_count = len(week_calls)
        except Exception as exc:  # noqa: BLE001
            logger.warning("weekly_digest_cost_failed", error=str(exc))
            return {"cost_week_usd": 0.0, "calls_count": 0, "total_tokens": 0}

        return {
            "cost_week_usd": round(cost_usd, 4),
            "calls_count": calls_count,
            "total_tokens": total_tokens,
        }

    def _collect_inbox_data(self) -> dict[str, Any]:
        """
        Собирает открытые warning/error items из inbox (attention items).

        Ограничивается WEEKLY_DIGEST_MAX_ATTENTION последними.
        """
        try:
            open_items = inbox_service.list_items(status="open", limit=200)
        except Exception as exc:  # noqa: BLE001
            logger.warning("weekly_digest_inbox_list_failed", error=str(exc))
            return {"attention_count": 0, "error_count": 0, "warning_count": 0, "items": []}

        attention = [it for it in open_items if it.get("severity") in ("error", "warning")]
        error_count = sum(1 for it in attention if it.get("severity") == "error")
        warning_count = sum(1 for it in attention if it.get("severity") == "warning")

        recent = attention[-WEEKLY_DIGEST_MAX_ATTENTION:]
        items = [
            {
                "severity": str(it.get("severity") or ""),
                "title": str(it.get("title") or "")[:80],
                "created_at_utc": str(it.get("created_at_utc") or "")[:19],
            }
            for it in recent
        ]

        return {
            "attention_count": len(attention),
            "error_count": error_count,
            "warning_count": warning_count,
            "items": items,
        }

    @staticmethod
    def _render_digest(
        *,
        ts_now: str,
        swarm_data: dict[str, Any],
        cost_data: dict[str, Any],
        inbox_data: dict[str, Any],
    ) -> str:
        """Формирует markdown-тело Weekly Digest."""
        lines = [
            f"**🦀 Weekly Digest** — {ts_now}",
            "",
            "## Swarm",
        ]

        total_rounds = swarm_data.get("total_rounds", 0)
        by_team = swarm_data.get("by_team") or {}
        top_artifacts = swarm_data.get("top_artifacts") or []

        lines.append(f"- Всего раундов за неделю: **{total_rounds}**")
        if by_team:
            team_str = ", ".join(f"{t}: {n}" for t, n in sorted(by_team.items()))
            lines.append(f"- По командам: {team_str}")
        else:
            lines.append("- По командам: нет данных")

        if top_artifacts:
            lines.append("- Последние раунды:")
            for a in top_artifacts:
                ts_str = a.get("timestamp_iso") or ""
                lines.append(f"  - [{a['team']}] {a['topic']} ({ts_str[:16]})")

        lines += [
            "",
            "## Cost",
        ]
        cost_usd = cost_data.get("cost_week_usd", 0.0)
        calls_count = cost_data.get("calls_count", 0)
        total_tokens = cost_data.get("total_tokens", 0)
        lines.append(f"- Стоимость за неделю: **${cost_usd:.4f}**")
        lines.append(f"- Вызовов модели: {calls_count}")
        lines.append(f"- Токенов суммарно: {total_tokens:,}")

        lines += [
            "",
            "## Inbox / Attention",
        ]
        attention_count = inbox_data.get("attention_count", 0)
        error_count = inbox_data.get("error_count", 0)
        warning_count = inbox_data.get("warning_count", 0)
        items = inbox_data.get("items") or []

        lines.append(
            f"- Открытых issues: **{attention_count}** (error: {error_count}, warning: {warning_count})"
        )
        if items:
            lines.append("- Последние:")
            for it in items:
                sev = it.get("severity", "?")
                title = it.get("title", "—")
                created = it.get("created_at_utc", "")
                lines.append(f"  - [{sev}] {title} ({created})")
        else:
            lines.append("- Нет открытых ошибок/предупреждений.")

        return "\n".join(lines)

    async def _weekly_digest_loop(self) -> None:
        """Бесконечный цикл: каждые 7 дней запускает generate_digest."""
        while True:
            await asyncio.sleep(self.INTERVAL_SEC)
            try:
                await self.generate_digest()
            except Exception as exc:  # noqa: BLE001
                logger.warning("weekly_digest_loop_error", error=str(exc))

    def start_weekly_digest_loop(self) -> "asyncio.Task[None]":
        """Запускает фоновую задачу Weekly Digest и возвращает Task."""
        loop = asyncio.get_event_loop()
        task = loop.create_task(self._weekly_digest_loop(), name="krab_weekly_digest")
        logger.info("weekly_digest_loop_started", interval_sec=self.INTERVAL_SEC)
        return task


# Синглтон
weekly_digest = WeeklyDigestService()

__all__ = ["WeeklyDigestService", "weekly_digest", "WEEKLY_DIGEST_INTERVAL_SEC"]
