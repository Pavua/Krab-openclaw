# -*- coding: utf-8 -*-
"""
Costs router — Phase 2 Wave YY extraction (Session 26).

RouterContext-based extraction для FinOps/costs endpoints.

Endpoints:
- GET /api/costs/report       — сводный отчёт по расходам (Gemini fields)
- GET /api/costs/budget       — состояние бюджета: лимит, потрачено, осталось
- GET /api/costs/history      — история вызовов модели (фильтр по channel)
- GET /api/costs/hourly       — почасовая разбивка за 24ч (sparkline)
- GET /api/costs/by_chat      — топ чатов по стоимости
- GET /api/costs/codex-quota  — квота Codex CLI (cached or live)
- GET /api/costs/by-tier      — агрегация расходов по тирам моделей

Контракт ответов сохранён 1:1 с inline definition из web_app.py
(см. `tests/fixtures/api_endpoints_baseline.json` snapshot).

Все endpoints — read-only (GET), не требуют ctx.assert_write_access.
Используют прямые модульные импорты (`cost_analytics`, `codex_quota`,
`model_tier_tracker`), поэтому ctx.deps не нужны.
"""

from __future__ import annotations

from fastapi import APIRouter

from ._context import RouterContext


def build_costs_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с FinOps/costs endpoints."""
    router = APIRouter(tags=["costs"])

    @router.get("/api/costs/report")
    async def get_costs_report() -> dict:
        """Отчёт по расходам для /costs dashboard (Gemini field names)."""
        try:
            import datetime as _dt

            from ...core.cost_analytics import cost_analytics as _ca

            raw = _ca.build_usage_report_dict()
            total_cost = float(raw.get("cost_session_usd") or 0)
            budget = float(raw.get("monthly_budget_usd") or 0) or 50.0
            total_calls = sum(m.get("calls", 0) for m in (raw.get("by_model") or {}).values())
            report = {
                "total_cost_usd": total_cost,
                "total_calls": total_calls,
                "budget_monthly_usd": budget,
                "budget_remaining_usd": budget - total_cost,
                "budget_used_pct": round(total_cost / budget * 100, 2) if budget else 0,
                "by_model": raw.get("by_model", {}),
                "period_start": "2026-04-01T00:00:00Z",
                "period_end": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "input_tokens": raw.get("input_tokens", 0),
                "output_tokens": raw.get("output_tokens", 0),
                "total_tool_calls": raw.get("total_tool_calls", 0),
                "total_fallbacks": raw.get("total_fallbacks", 0),
                "total_context_tokens": raw.get("total_context_tokens", 0),
                "avg_context_tokens": raw.get("avg_context_tokens", 0),
                "by_channel": raw.get("by_channel", {}),
            }
            return {"ok": True, "report": report}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/api/costs/budget")
    async def get_costs_budget() -> dict:
        """Состояние бюджета: лимит, потрачено, осталось, статус."""
        try:
            from ...core.cost_analytics import cost_analytics as _ca

            budget = _ca.get_monthly_budget_usd()
            spent = _ca.get_monthly_cost_usd()
            remaining = _ca.get_remaining_budget_usd()
            return {
                "ok": True,
                "budget": {
                    "monthly_limit_usd": budget if budget > 0 else None,
                    "spent_usd": round(spent, 6),
                    "remaining_usd": round(remaining, 6) if remaining is not None else None,
                    "budget_ok": _ca.check_budget_ok(),
                    "used_pct": round(spent / budget * 100, 2) if budget > 0 else None,
                    "forecast_calls": _ca.monthly_calls_forecast(),
                },
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/api/costs/history")
    async def get_costs_history(limit: int = 20, channel: str = "") -> dict:
        """История вызовов модели: последние N записей, опционально фильтр по channel."""
        try:
            from ...core.cost_analytics import cost_analytics as _ca

            calls = list(_ca._calls)
            if channel:
                calls = [r for r in calls if r.channel == channel]
            calls = calls[-limit:][::-1]
            return {
                "ok": True,
                "total_records": len(_ca._calls),
                "returned": len(calls),
                "history": [
                    {
                        "model_id": r.model_id,
                        "input_tokens": r.input_tokens,
                        "output_tokens": r.output_tokens,
                        "cost_usd": round(r.cost_usd, 6),
                        "timestamp": r.timestamp,
                        "channel": r.channel,
                        "is_fallback": r.is_fallback,
                        "tool_calls_count": r.tool_calls_count,
                    }
                    for r in calls
                ],
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/api/costs/hourly")
    async def get_costs_hourly() -> dict:
        """Почасовые расходы за последние 24 часа для sparkline-диаграммы."""
        try:
            import datetime
            import time as _time

            from ...core.cost_analytics import cost_analytics as _ca

            now = _time.time()
            cutoff = now - 86400

            buckets: list[float] = [0.0] * 24
            bucket_calls: list[int] = [0] * 24

            for r in _ca._calls:
                if r.timestamp < cutoff:
                    continue
                age_sec = now - r.timestamp
                hour_idx = 23 - int(age_sec // 3600)
                if 0 <= hour_idx < 24:
                    buckets[hour_idx] += r.cost_usd
                    bucket_calls[hour_idx] += 1

            labels = []
            for i in range(24):
                ts = now - (23 - i) * 3600
                dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                labels.append(dt.strftime("%H:00"))

            today_start = (
                datetime.datetime.now(datetime.timezone.utc)
                .replace(hour=0, minute=0, second=0, microsecond=0)
                .timestamp()
            )
            yesterday_start = today_start - 86400

            today_cost = sum(r.cost_usd for r in _ca._calls if r.timestamp >= today_start)
            yesterday_cost = sum(
                r.cost_usd for r in _ca._calls if yesterday_start <= r.timestamp < today_start
            )

            week_start = today_start - 6 * 86400
            week_cost = sum(r.cost_usd for r in _ca._calls if r.timestamp >= week_start)
            week_avg = week_cost / 7.0

            return {
                "ok": True,
                "buckets": [round(v, 6) for v in buckets],
                "bucket_calls": bucket_calls,
                "labels": labels,
                "today_cost": round(today_cost, 6),
                "yesterday_cost": round(yesterday_cost, 6),
                "week_avg_daily": round(week_avg, 6),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/api/costs/by_chat")
    async def get_costs_by_chat(limit: int = 10) -> dict:
        """Топ-N чатов по стоимости (из channel-меток в истории вызовов)."""
        try:
            from collections import defaultdict

            from ...core.cost_analytics import cost_analytics as _ca

            by_channel: dict[str, dict] = defaultdict(lambda: {"cost": 0.0, "calls": 0})
            for r in _ca._calls:
                ch = r.channel or "unknown"
                by_channel[ch]["cost"] += r.cost_usd
                by_channel[ch]["calls"] += 1

            top = sorted(by_channel.items(), key=lambda x: -x[1]["cost"])[:limit]
            result = []
            for ch, data in top:
                calls = data["calls"]
                cost = data["cost"]
                result.append(
                    {
                        "chat_title": ch,
                        "cost_usd": round(cost, 6),
                        "calls": calls,
                        "avg_cost_per_call": round(cost / calls, 6) if calls else 0.0,
                    }
                )

            return {"ok": True, "top_chats": result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/api/costs/codex-quota")
    async def get_codex_quota(refresh: bool = False) -> dict:
        """Квота Codex CLI: план, подписка, usage из OpenAI API / JWT."""
        try:
            from ...core.codex_quota import fetch_quota, get_cached_quota

            if not refresh:
                cached = get_cached_quota()
                if cached:
                    return {"ok": True, "quota": cached, "cached": True}

            quota = await fetch_quota()
            return {"ok": True, "quota": quota, "cached": False}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/api/costs/by-tier")
    async def get_costs_by_tier(hours: float = 24.0) -> dict:
        """Агрегация расходов по тирам моделей (opus/sonnet/haiku/gpt5/gemini/…)."""
        try:
            from ...core.cost_analytics import cost_analytics as _ca
            from ...core.model_tier_tracker import get_tier_summary

            summary = get_tier_summary(list(_ca._calls), since_hours=hours)
            return {"ok": True, "summary": summary}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ctx используется через RouterContext для совместимости с factory pattern,
    # даже если все endpoints этого роутера — stateless read-only.
    _ = ctx
    return router
