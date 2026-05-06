# -*- coding: utf-8 -*-
"""
weekly_digest.py — еженедельная сводка активности Krab.

Собирает данные за последние 7 дней:
- swarm rounds по командам + top artifacts;
- cost за неделю + cost trend vs предыдущая неделя;
- top-3 моделей по вызовам (из cost_analytics + bypass_perf.jsonl);
- inbox errors / attention items;
- memory pressure (из coexistence_monitor.log).

Записывает digest-item в inbox_service (аналогично ErrorDigest).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.cost_analytics import cost_analytics
from .inbox_service import inbox_service
from .logger import get_logger
from .swarm_artifact_store import swarm_artifact_store

# Путь к JSONL-файлу с bypass latency записями
_BYPASS_PERF_LOG = Path.home() / ".openclaw/krab_runtime_state/bypass_perf.jsonl"
# Путь к логу мониторинга памяти
_COEXISTENCE_LOG = Path.home() / ".openclaw/krab_runtime_state/coexistence_monitor.log"

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
    # Задержка перед первым fire после старта (5 минут) — идемпотентно по week-dedupe
    FIRST_RUN_DELAY_SEC: int = 300

    # Callback для отправки digest в Telegram (устанавливается из userbot_bridge)
    _telegram_callback: Callable[[str], Awaitable[None]] | None = None

    def set_telegram_callback(self, cb: Callable[[str], Awaitable[None]]) -> None:
        """Устанавливает async callback для Telegram delivery."""
        self._telegram_callback = cb

    @staticmethod
    def _collect_bypass_perf(window_days: int = 7) -> dict[str, Any]:
        """
        Читает bypass_perf.jsonl и возвращает top-3 kind/model по количеству вызовов.

        Деградирует тихо при любых сбоях.
        """
        empty: dict[str, Any] = {
            "total_calls": 0,
            "total_failures": 0,
            "top_kinds": [],
            "top_models": [],
        }
        if not _BYPASS_PERF_LOG.exists():
            return empty

        cutoff = time.time() - window_days * 86400
        by_kind: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "fail": 0, "durations": []}
        )
        by_model: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "fail": 0, "durations": []}
        )
        total_calls = 0
        total_failures = 0

        try:
            with _BYPASS_PERF_LOG.open() as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    if r.get("ts", 0) < cutoff:
                        continue
                    total_calls += 1
                    kind = str(r.get("kind") or "unknown")
                    model = str(r.get("model") or "unknown")
                    dur = r.get("duration_sec", 0.0)
                    ok = r.get("success", True)
                    if not ok:
                        total_failures += 1
                    by_kind[kind]["count"] += 1
                    by_kind[kind]["fail"] += 0 if ok else 1
                    by_kind[kind]["durations"].append(dur)
                    by_model[model]["count"] += 1
                    by_model[model]["fail"] += 0 if ok else 1
                    by_model[model]["durations"].append(dur)
        except Exception as exc:  # noqa: BLE001
            logger.warning("weekly_digest_bypass_read_failed", error=str(exc))
            return empty

        def _p95(durs: list[float]) -> float:
            """Перцентиль p95 из списка длительностей."""
            if not durs:
                return 0.0
            s = sorted(durs)
            return round(s[min(int(len(s) * 0.95), len(s) - 1)], 1)

        def _mean(durs: list[float]) -> float:
            """Среднее значение длительностей."""
            return round(sum(durs) / len(durs), 1) if durs else 0.0

        # Top-3 по количеству вызовов
        top_kinds = sorted(by_kind.items(), key=lambda x: -x[1]["count"])[:3]
        top_kinds_out = [
            {
                "name": k,
                "count": v["count"],
                "mean_sec": _mean(v["durations"]),
                "p95_sec": _p95(v["durations"]),
                "fail": v["fail"],
            }
            for k, v in top_kinds
        ]
        top_models = sorted(by_model.items(), key=lambda x: -x[1]["count"])[:3]
        top_models_out = [
            {
                "name": m,
                "count": v["count"],
                "mean_sec": _mean(v["durations"]),
                "p95_sec": _p95(v["durations"]),
                "fail": v["fail"],
            }
            for m, v in top_models
        ]
        return {
            "total_calls": total_calls,
            "total_failures": total_failures,
            "top_kinds": top_kinds_out,
            "top_models": top_models_out,
        }

    @staticmethod
    def _collect_cost_trend(week_start_ts: float) -> dict[str, Any]:
        """
        Возвращает cost этой и прошлой недели + delta% + top-3 модели по стоимости.

        Использует cost_analytics._calls (in-memory). Деградирует тихо.
        """
        try:
            prev_week_start = week_start_ts - 7 * 24 * 3600
            this_calls = [r for r in cost_analytics._calls if r.timestamp >= week_start_ts]
            prev_calls = [
                r for r in cost_analytics._calls if prev_week_start <= r.timestamp < week_start_ts
            ]
            this_cost = sum(r.cost_usd for r in this_calls)
            prev_cost = sum(r.cost_usd for r in prev_calls)

            # delta в процентах (None если прошлой недели нет)
            delta_pct: float | None = None
            if prev_cost > 0:
                delta_pct = round((this_cost - prev_cost) / prev_cost * 100, 1)

            # Top-3 модели этой недели по стоимости
            by_model: dict[str, dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "calls": 0.0})
            for r in this_calls:
                by_model[r.model_id]["cost"] += r.cost_usd
                by_model[r.model_id]["calls"] += 1  # type: ignore[assignment]
            top_models = sorted(by_model.items(), key=lambda x: -x[1]["cost"])[:3]
            top_models_out = [
                {"model": m, "cost_usd": round(v["cost"], 4), "calls": int(v["calls"])}
                for m, v in top_models
            ]

            return {
                "this_week_usd": round(this_cost, 4),
                "prev_week_usd": round(prev_cost, 4),
                "delta_pct": delta_pct,
                "top_models": top_models_out,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("weekly_digest_cost_trend_failed", error=str(exc))
            return {
                "this_week_usd": 0.0,
                "prev_week_usd": 0.0,
                "delta_pct": None,
                "top_models": [],
            }

    @staticmethod
    def _collect_memory_pressure(window_days: int = 7) -> dict[str, Any]:
        """
        Читает coexistence_monitor.log и возвращает max swap + количество alerts.

        Деградирует тихо при любых сбоях.
        """
        empty: dict[str, Any] = {"alerts_count": 0, "max_swap_gb": 0.0, "max_combined_rss_gb": 0.0}
        if not _COEXISTENCE_LOG.exists():
            return empty

        cutoff = time.time() - window_days * 86400
        alerts_count = 0
        max_swap = 0.0
        max_rss = 0.0

        try:
            with _COEXISTENCE_LOG.open() as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    if d.get("timestamp", 0) < cutoff:
                        continue
                    alerts_list = d.get("alerts") or []
                    alerts_count += len(alerts_list)
                    swap = d.get("swap_used_gb", 0.0)
                    rss = d.get("combined_rss_gb", 0.0)
                    if swap > max_swap:
                        max_swap = swap
                    if rss > max_rss:
                        max_rss = rss
        except Exception as exc:  # noqa: BLE001
            logger.warning("weekly_digest_memory_pressure_failed", error=str(exc))
            return empty

        return {
            "alerts_count": alerts_count,
            "max_swap_gb": round(max_swap, 1),
            "max_combined_rss_gb": round(max_rss, 1),
        }

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

        # --- Cost trend: эта vs прошлая неделя + top-3 модели ---
        cost_trend = self._collect_cost_trend(week_start)

        # --- Bypass perf: top-3 kind/model за неделю ---
        bypass_data = self._collect_bypass_perf(window_days=7)

        # --- Memory pressure: swap max + alerts ---
        memory_data = self._collect_memory_pressure(window_days=7)

        # --- Inbox: attention items и ошибки ---
        inbox_data = self._collect_inbox_data()

        # --- Формируем markdown-отчёт ---
        body = self._render_digest(
            ts_now=ts_now,
            swarm_data=swarm_data,
            cost_data=cost_data,
            cost_trend=cost_trend,
            bypass_data=bypass_data,
            memory_data=memory_data,
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

        # Telegram delivery через callback из userbot_bridge
        if self._telegram_callback:
            try:
                await self._telegram_callback(body)
                logger.info("weekly_digest_telegram_delivered")
            except Exception as exc:  # noqa: BLE001
                logger.warning("weekly_digest_telegram_failed", error=str(exc))

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
        cost_trend: dict[str, Any] | None = None,
        bypass_data: dict[str, Any] | None = None,
        memory_data: dict[str, Any] | None = None,
        inbox_data: dict[str, Any],
    ) -> str:
        """Формирует markdown-тело Weekly Digest с расширенными секциями."""
        lines = [
            f"**🦀 Weekly Digest** — {ts_now}",
            "",
            "## 🤖 Swarm",
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

        # --- Секция Cost ---
        lines += [
            "",
            "## 💸 Cost",
        ]
        cost_usd = cost_data.get("cost_week_usd", 0.0)
        calls_count = cost_data.get("calls_count", 0)
        total_tokens = cost_data.get("total_tokens", 0)
        lines.append(f"- Стоимость за неделю: **${cost_usd:.4f}**")
        lines.append(f"- Вызовов модели: {calls_count}")
        lines.append(f"- Токенов суммарно: {total_tokens:,}")

        # Cost trend: delta vs прошлая неделя + top-3 модели
        if cost_trend:
            prev_cost = cost_trend.get("prev_week_usd", 0.0)
            delta_pct = cost_trend.get("delta_pct")
            if delta_pct is not None:
                sign = "+" if delta_pct >= 0 else ""
                trend_str = f"{sign}{delta_pct:.1f}% vs предыдущая неделя (${prev_cost:.4f})"
                lines.append(f"- Динамика: {trend_str}")
            top_cost_models = cost_trend.get("top_models") or []
            if top_cost_models:
                lines.append("- Top модели по стоимости:")
                for m in top_cost_models:
                    lines.append(f"  - {m['model']}: ${m['cost_usd']:.4f} ({m['calls']} вызовов)")

        # --- Секция Bypass Calls ---
        if bypass_data and bypass_data.get("total_calls", 0) > 0:
            lines += [
                "",
                "## 🔌 Bypass Calls",
            ]
            total_bp = bypass_data.get("total_calls", 0)
            total_bp_fail = bypass_data.get("total_failures", 0)
            lines.append(f"- Всего bypass вызовов: **{total_bp}** (сбоев: {total_bp_fail})")
            top_kinds = bypass_data.get("top_kinds") or []
            if top_kinds:
                lines.append("- Top по kind:")
                for k in top_kinds:
                    lines.append(
                        f"  - {k['name']}: {k['count']} "
                        f"(mean {k['mean_sec']}s, p95 {k['p95_sec']}s)"
                    )
            top_bp_models = bypass_data.get("top_models") or []
            if top_bp_models:
                lines.append("- Top модели:")
                for m in top_bp_models:
                    lines.append(f"  - {m['name']}: {m['count']} вызовов (mean {m['mean_sec']}s)")

        # --- Секция Memory Pressure ---
        if memory_data:
            max_swap = memory_data.get("max_swap_gb", 0.0)
            alerts_cnt = memory_data.get("alerts_count", 0)
            max_rss = memory_data.get("max_combined_rss_gb", 0.0)
            # Показываем секцию только если есть что сообщить
            if max_swap > 0 or alerts_cnt > 0:
                lines += [
                    "",
                    "## 🖥️ Memory",
                ]
                lines.append(f"- Max swap за неделю: **{max_swap} GB**")
                if max_rss > 0:
                    lines.append(f"- Max RSS (combined): {max_rss} GB")
                if alerts_cnt > 0:
                    lines.append(f"- ⚠️ Memory alerts: **{alerts_cnt}**")
                else:
                    lines.append("- Критических alerts не было")

        # --- Секция Inbox / Attention ---
        lines += [
            "",
            "## 📥 Inbox / Attention",
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
        """
        Бесконечный цикл: раз в неделю запускает generate_digest.

        ВАЖНО: первый fire — через FIRST_RUN_DELAY_SEC после старта (не через 7 дней),
        иначе при частых рестартах loop никогда не достигает 7-day sleep.
        Идемпотентность обеспечивается dedupe_key `weekly_digest:YYYY-WVV` в inbox_service.
        """
        # Короткая задержка перед первым fire — даём userbot полностью подняться
        await asyncio.sleep(self.FIRST_RUN_DELAY_SEC)
        while True:
            try:
                await self.generate_digest()
            except Exception as exc:  # noqa: BLE001
                logger.warning("weekly_digest_loop_error", error=str(exc))
            await asyncio.sleep(self.INTERVAL_SEC)

    def start_weekly_digest_loop(self) -> "asyncio.Task[None]":
        """Запускает фоновую задачу Weekly Digest и возвращает Task."""
        loop = asyncio.get_event_loop()
        task = loop.create_task(self._weekly_digest_loop(), name="krab_weekly_digest")
        logger.info("weekly_digest_loop_started", interval_sec=self.INTERVAL_SEC)
        return task


# Синглтон
weekly_digest = WeeklyDigestService()

__all__ = ["WeeklyDigestService", "weekly_digest", "WEEKLY_DIGEST_INTERVAL_SEC"]
