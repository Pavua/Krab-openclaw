# -*- coding: utf-8 -*-
"""
Web App Module (Phase 15+).
Сервер для Dashboard и web-управления экосистемой Krab.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import time
import json
from datetime import datetime, timezone

import structlog
import uvicorn
from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse

from src.core.ecosystem_health import EcosystemHealthService

logger = structlog.get_logger("WebApp")


class WebApp:
    """Web-панель Krab с API статуса экосистемы."""

    def __init__(self, deps: dict, port: int = 8000, host: str = "0.0.0.0"):
        self.app = FastAPI(title="Krab Web Panel", version="v8")
        self.deps = deps
        self.port = int(port)
        self.host = host
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task | None = None
        self._index_path = Path(__file__).resolve().parents[1] / "web" / "index.html"
        self._assistant_rate_state: dict[str, list[float]] = {}
        self._idempotency_state: dict[str, tuple[float, dict]] = {}
        self._setup_routes()

    def _public_base_url(self) -> str:
        """Возвращает внешний base URL панели."""
        explicit = os.getenv("WEB_PUBLIC_BASE_URL", "").strip().rstrip("/")
        if explicit:
            return explicit
        display_host = os.getenv("WEB_HOST", "127.0.0.1").strip() or "127.0.0.1"
        return f"http://{display_host}:{self.port}"

    def _web_api_key(self) -> str:
        """Возвращает API-ключ web write-endpoints (может быть пустым)."""
        return os.getenv("WEB_API_KEY", "").strip()

    def _assert_write_access(self, header_key: str, token: str) -> None:
        """Проверяет доступ к write-эндпоинтам web API."""
        expected = self._web_api_key()
        if not expected:
            return

        provided = (header_key or "").strip() or (token or "").strip()
        if provided != expected:
            raise HTTPException(status_code=403, detail="forbidden: invalid WEB_API_KEY")

    def _assistant_rate_limit_per_min(self) -> int:
        """Возвращает лимит запросов assistant API в минуту на одного клиента."""
        raw = os.getenv("WEB_ASSISTANT_RATE_LIMIT_PER_MIN", "30").strip()
        try:
            value = int(raw)
        except Exception:
            value = 30
        return max(1, value)

    def _enforce_assistant_rate_limit(self, client_key: str) -> None:
        """Простой in-memory rate-limit для web-native assistant."""
        now = time.time()
        window_sec = 60.0
        limit = self._assistant_rate_limit_per_min()
        key = client_key or "anonymous"
        bucket = self._assistant_rate_state.setdefault(key, [])
        # Оставляем только события за последнюю минуту.
        bucket[:] = [ts for ts in bucket if (now - ts) <= window_sec]
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"assistant_rate_limited: limit={limit}/min for client={key}",
            )
        bucket.append(now)

    def _idempotency_ttl_sec(self) -> int:
        """TTL кэша idempotency в секундах."""
        raw = os.getenv("WEB_IDEMPOTENCY_TTL_SEC", "300").strip()
        try:
            value = int(raw)
        except Exception:
            value = 300
        return max(30, value)

    def _idempotency_get(self, namespace: str, key: str) -> dict | None:
        """Возвращает кэшированный ответ по idempotency key, если не истек TTL."""
        if not key:
            return None
        now = time.time()
        ttl = self._idempotency_ttl_sec()
        lookup_key = f"{namespace}:{key}"
        entry = self._idempotency_state.get(lookup_key)
        if not entry:
            return None
        ts, payload = entry
        if (now - ts) > ttl:
            self._idempotency_state.pop(lookup_key, None)
            return None
        data = dict(payload)
        data["idempotent_replay"] = True
        return data

    def _idempotency_set(self, namespace: str, key: str, payload: dict) -> None:
        """Сохраняет ответ по idempotency key."""
        if not key:
            return
        lookup_key = f"{namespace}:{key}"
        self._idempotency_state[lookup_key] = (time.time(), dict(payload))

    def _setup_routes(self):
        @self.app.get("/", response_class=HTMLResponse)
        async def index():
            if self._index_path.exists():
                return FileResponse(self._index_path)
            return HTMLResponse("<h1>Krab Web Panel</h1><p>index.html не найден</p>")

        @self.app.get("/api/stats")
        async def get_stats():
            router = self.deps["router"]
            black_box = self.deps["black_box"]
            rag = router.rag
            return {
                "router": router.get_model_info(),
                "black_box": black_box.get_stats(),
                "rag": rag.get_stats() if rag else {"enabled": False, "count": 0},
            }

        @self.app.get("/api/health")
        async def get_health():
            """Единый health статусов для web-панели."""
            router = self.deps["router"]
            openclaw = self.deps.get("openclaw_client")
            voice_gateway = self.deps.get("voice_gateway_client")
            krab_ear = self.deps.get("krab_ear_client")
            ecosystem = EcosystemHealthService(
                router=router,
                openclaw_client=openclaw,
                voice_gateway_client=voice_gateway,
                krab_ear_client=krab_ear,
            )
            report = await ecosystem.collect()
            return {
                "status": "ok",
                "checks": {
                    "openclaw": bool(report["checks"]["openclaw"]["ok"]),
                    "local_lm": bool(report["checks"]["local_lm"]["ok"]),
                    "voice_gateway": bool(report["checks"]["voice_gateway"]["ok"]),
                    "krab_ear": bool(report["checks"]["krab_ear"]["ok"]),
                },
                "degradation": str(report["degradation"]),
                "risk_level": str(report["risk_level"]),
                "chain": report["chain"],
            }

        @self.app.get("/api/policy")
        async def get_policy():
            """Возвращает runtime-политику AI (queue/guardrails/reactions)."""
            ai_runtime = self.deps.get("ai_runtime")
            if not ai_runtime:
                return {"ok": False, "error": "ai_runtime_not_configured"}
            return {"ok": True, "policy": ai_runtime.get_policy_snapshot()}

        @self.app.get("/api/queue")
        async def get_queue():
            """Возвращает состояние per-chat очередей автообработки."""
            ai_runtime = self.deps.get("ai_runtime")
            if not ai_runtime or not hasattr(ai_runtime, "queue_manager"):
                return {"ok": False, "error": "queue_not_configured"}
            return {"ok": True, "queue": ai_runtime.queue_manager.get_stats()}

        @self.app.get("/api/reactions/stats")
        async def get_reactions_stats(chat_id: int | None = Query(default=None)):
            """Сводка по реакциям (общая или по чату)."""
            reaction_engine = self.deps.get("reaction_engine")
            if not reaction_engine:
                return {"ok": False, "error": "reaction_engine_not_configured"}
            return {"ok": True, "stats": reaction_engine.get_reaction_stats(chat_id=chat_id)}

        @self.app.get("/api/mood/{chat_id}")
        async def get_chat_mood(chat_id: int):
            """Возвращает mood-профиль конкретного чата."""
            reaction_engine = self.deps.get("reaction_engine")
            if not reaction_engine:
                return {"ok": False, "error": "reaction_engine_not_configured"}
            return {"ok": True, "mood": reaction_engine.get_chat_mood(chat_id)}

        @self.app.get("/api/links")
        async def get_links():
            """Ссылки по экосистеме в одном месте."""
            base = self._public_base_url()
            return {
                "dashboard": base,
                "stats_api": f"{base}/api/stats",
                "health_api": f"{base}/api/health",
                "ecosystem_health_api": f"{base}/api/ecosystem/health",
                "links_api": f"{base}/api/links",
                "voice_gateway": os.getenv("VOICE_GATEWAY_URL", "http://127.0.0.1:8090"),
                "openclaw": os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18789"),
            }

        @self.app.get("/api/ecosystem/health")
        async def ecosystem_health():
            """Расширенный health-отчет 3-проектной экосистемы."""
            router = self.deps["router"]
            openclaw = self.deps.get("openclaw_client")
            voice_gateway = self.deps.get("voice_gateway_client")
            krab_ear = self.deps.get("krab_ear_client")
            report = await EcosystemHealthService(
                router=router,
                openclaw_client=openclaw,
                voice_gateway_client=voice_gateway,
                krab_ear_client=krab_ear,
            ).collect()
            return {"ok": True, "report": report}

        @self.app.get("/api/ecosystem/health/export")
        async def ecosystem_health_export():
            """Экспортирует расширенный ecosystem health report в JSON-файл."""
            router = self.deps["router"]
            openclaw = self.deps.get("openclaw_client")
            voice_gateway = self.deps.get("voice_gateway_client")
            krab_ear = self.deps.get("krab_ear_client")
            payload = await EcosystemHealthService(
                router=router,
                openclaw_client=openclaw,
                voice_gateway_client=voice_gateway,
                krab_ear_client=krab_ear,
            ).collect()
            ops_dir = Path("artifacts/ops")
            ops_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
            out_path = ops_dir / f"ecosystem_health_web_{stamp}.json"
            with out_path.open("w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
            return FileResponse(
                str(out_path),
                media_type="application/json",
                filename=out_path.name,
            )

        @self.app.get("/api/model/recommend")
        async def model_recommend(profile: str = Query(default="chat", description="Профиль задачи")):
            router = self.deps["router"]
            return router.get_profile_recommendation(profile)

        @self.app.post("/api/model/preflight")
        async def model_preflight(payload: dict = Body(...)):
            """
            Возвращает preflight-план задачи до выполнения:
            профиль, канал/модель, confirm-step, риски и cost hint.
            """
            router = self.deps["router"]
            if not hasattr(router, "get_task_preflight"):
                return {"ok": False, "error": "task_preflight_not_supported"}

            prompt = str(payload.get("prompt", "")).strip()
            if not prompt:
                raise HTTPException(status_code=400, detail="prompt_required")

            task_type = str(payload.get("task_type", "chat")).strip().lower() or "chat"
            preferred_model = payload.get("preferred_model")
            preferred_model_str = str(preferred_model).strip() if preferred_model else None
            confirm_expensive = bool(payload.get("confirm_expensive", False))

            preflight = router.get_task_preflight(
                prompt=prompt,
                task_type=task_type,
                preferred_model=preferred_model_str,
                confirm_expensive=confirm_expensive,
            )
            return {"ok": True, "preflight": preflight}

        @self.app.get("/api/model/feedback")
        async def model_feedback_summary(
            profile: str | None = Query(default=None),
            top: int = Query(default=5, ge=1, le=20),
        ):
            """Сводка оценок качества роутинга моделей."""
            router = self.deps["router"]
            if not hasattr(router, "get_feedback_summary"):
                return {"ok": False, "error": "feedback_summary_not_supported"}
            normalized_profile = str(profile).strip().lower() if profile is not None else None
            return {
                "ok": True,
                "feedback": router.get_feedback_summary(profile=normalized_profile, top=top),
            }

        @self.app.post("/api/model/feedback")
        async def model_feedback_submit(
            payload: dict = Body(...),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            x_idempotency_key: str = Header(default="", alias="X-Idempotency-Key"),
            token: str = Query(default=""),
        ):
            """Принимает оценку качества ответа (1-5) для самообучающегося роутинга."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]
            if not hasattr(router, "submit_feedback"):
                return {"ok": False, "error": "feedback_submit_not_supported"}

            idem_key = (x_idempotency_key or "").strip()
            cached = self._idempotency_get("model_feedback_submit", idem_key)
            if cached:
                return cached

            score = payload.get("score")
            profile = payload.get("profile")
            model_name = payload.get("model")
            channel = payload.get("channel")
            note = payload.get("note", "")

            try:
                result = router.submit_feedback(
                    score=int(score),
                    profile=str(profile).strip().lower() if profile is not None else None,
                    model_name=str(model_name).strip() if model_name is not None else None,
                    channel=str(channel).strip().lower() if channel is not None else None,
                    note=str(note).strip(),
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"feedback_submit_failed: {exc}") from exc

            response_payload = {"ok": True, "result": result}
            self._idempotency_set("model_feedback_submit", idem_key, response_payload)
            return response_payload

        @self.app.get("/api/ops/usage")
        async def ops_usage():
            """Агрегированный usage-срез роутера моделей."""
            router = self.deps["router"]
            if hasattr(router, "get_usage_summary"):
                return {"ok": True, "usage": router.get_usage_summary()}
            return {"ok": False, "error": "usage_summary_not_supported"}

        @self.app.get("/api/ops/cost-report")
        async def ops_cost_report(monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000)):
            """Оценочный отчет по затратам local/cloud маршрутизации."""
            router = self.deps["router"]
            if hasattr(router, "get_cost_report"):
                return {"ok": True, "report": router.get_cost_report(monthly_calls_forecast=monthly_calls_forecast)}
            return {"ok": False, "error": "cost_report_not_supported"}

        @self.app.get("/api/ops/executive-summary")
        async def ops_executive_summary(monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000)):
            """Компактный ops executive summary: KPI + риски + рекомендации."""
            router = self.deps["router"]
            if hasattr(router, "get_ops_executive_summary"):
                return {"ok": True, "summary": router.get_ops_executive_summary(monthly_calls_forecast=monthly_calls_forecast)}
            return {"ok": False, "error": "ops_executive_summary_not_supported"}

        @self.app.get("/api/ops/report")
        async def ops_report(
            history_limit: int = Query(default=20, ge=1, le=200),
            monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
        ):
            """Единый ops отчет: usage + alerts + costs + history."""
            router = self.deps["router"]
            if hasattr(router, "get_ops_report"):
                return {
                    "ok": True,
                    "report": router.get_ops_report(
                        history_limit=history_limit,
                        monthly_calls_forecast=monthly_calls_forecast,
                    ),
                }
            return {"ok": False, "error": "ops_report_not_supported"}

        @self.app.get("/api/ops/report/export")
        async def ops_report_export(
            history_limit: int = Query(default=50, ge=1, le=200),
            monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
        ):
            """Экспортирует полный ops report в JSON-файл."""
            router = self.deps["router"]
            if not hasattr(router, "get_ops_report"):
                return {"ok": False, "error": "ops_report_not_supported"}
            report = router.get_ops_report(
                history_limit=history_limit,
                monthly_calls_forecast=monthly_calls_forecast,
            )
            ops_dir = Path("artifacts/ops")
            ops_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
            out_path = ops_dir / f"ops_report_web_{stamp}.json"
            with out_path.open("w", encoding="utf-8") as fp:
                json.dump(report, fp, ensure_ascii=False, indent=2)
            return FileResponse(
                str(out_path),
                media_type="application/json",
                filename=out_path.name,
            )

        @self.app.get("/api/ops/bundle")
        async def ops_bundle(
            history_limit: int = Query(default=50, ge=1, le=200),
            monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
        ):
            """Единый bundle: ops report + health snapshot."""
            router = self.deps["router"]
            if not hasattr(router, "get_ops_report"):
                return {"ok": False, "error": "ops_report_not_supported"}
            openclaw = self.deps.get("openclaw_client")
            voice_gateway = self.deps.get("voice_gateway_client")
            local_ok = await router.check_local_health()
            openclaw_ok = await openclaw.health_check() if openclaw else False
            voice_ok = await voice_gateway.health_check() if voice_gateway else False
            return {
                "ok": True,
                "bundle": {
                    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "ops_report": router.get_ops_report(
                        history_limit=history_limit,
                        monthly_calls_forecast=monthly_calls_forecast,
                    ),
                    "health": {
                        "openclaw": openclaw_ok,
                        "local_lm": local_ok,
                        "voice_gateway": voice_ok,
                    },
                },
            }

        @self.app.get("/api/ops/bundle/export")
        async def ops_bundle_export(
            history_limit: int = Query(default=50, ge=1, le=200),
            monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
        ):
            """Экспортирует единый ops bundle в JSON-файл."""
            router = self.deps["router"]
            if not hasattr(router, "get_ops_report"):
                return {"ok": False, "error": "ops_report_not_supported"}
            openclaw = self.deps.get("openclaw_client")
            voice_gateway = self.deps.get("voice_gateway_client")
            local_ok = await router.check_local_health()
            openclaw_ok = await openclaw.health_check() if openclaw else False
            voice_ok = await voice_gateway.health_check() if voice_gateway else False

            payload = {
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "ops_report": router.get_ops_report(
                    history_limit=history_limit,
                    monthly_calls_forecast=monthly_calls_forecast,
                ),
                "health": {
                    "openclaw": openclaw_ok,
                    "local_lm": local_ok,
                    "voice_gateway": voice_ok,
                },
            }
            ops_dir = Path("artifacts/ops")
            ops_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
            out_path = ops_dir / f"ops_bundle_web_{stamp}.json"
            with out_path.open("w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
            return FileResponse(
                str(out_path),
                media_type="application/json",
                filename=out_path.name,
            )

        @self.app.get("/api/ops/alerts")
        async def ops_alerts():
            """Операционные алерты по расходам и маршрутизации."""
            router = self.deps["router"]
            if hasattr(router, "get_ops_alerts"):
                return {"ok": True, "alerts": router.get_ops_alerts()}
            return {"ok": False, "error": "ops_alerts_not_supported"}

        @self.app.get("/api/ops/history")
        async def ops_history(limit: int = Query(default=30, ge=1, le=200)):
            """История ops snapshot-ов (alerts/status over time)."""
            router = self.deps["router"]
            if hasattr(router, "get_ops_history"):
                return {"ok": True, "history": router.get_ops_history(limit=limit)}
            return {"ok": False, "error": "ops_history_not_supported"}

        @self.app.post("/api/ops/maintenance/prune")
        async def ops_prune(
            payload: dict = Body(default={}),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Очищает ops history по retention-параметрам."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]
            if not hasattr(router, "prune_ops_history"):
                return {"ok": False, "error": "ops_prune_not_supported"}
            max_age_days = int(payload.get("max_age_days", 30))
            keep_last = int(payload.get("keep_last", 100))
            try:
                result = router.prune_ops_history(max_age_days=max_age_days, keep_last=keep_last)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "result": result}

        @self.app.post("/api/ops/ack/{code}")
        async def ops_ack(
            code: str,
            payload: dict = Body(default={}),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Подтверждает alert код оператором."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]
            if not hasattr(router, "acknowledge_ops_alert"):
                return {"ok": False, "error": "ops_ack_not_supported"}
            actor = str(payload.get("actor", "web_api")).strip() or "web_api"
            note = str(payload.get("note", "")).strip()
            try:
                result = router.acknowledge_ops_alert(code=code, actor=actor, note=note)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "result": result}

        @self.app.delete("/api/ops/ack/{code}")
        async def ops_unack(
            code: str,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Снимает подтверждение alert кода."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]
            if not hasattr(router, "clear_ops_alert_ack"):
                return {"ok": False, "error": "ops_unack_not_supported"}
            try:
                result = router.clear_ops_alert_ack(code=code)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "result": result}

        @self.app.get("/api/assistant/capabilities")
        async def assistant_capabilities():
            """Возвращает возможности web-native assistant режима."""
            return {
                "mode": "web_native",
                "endpoint": "/api/assistant/query",
                "preflight_endpoint": "/api/model/preflight",
                "feedback_endpoint": "/api/model/feedback",
                "auth": "X-Krab-Web-Key header or token query (if WEB_API_KEY configured)",
                "task_types": ["chat", "coding", "reasoning", "creative", "moderation", "security", "infra", "review"],
                "notes": [
                    "Работает без Telegram-интерфейса.",
                    "Использует тот же роутер моделей и policy, что и Telegram-бот.",
                    "Для критичных задач можно передать `confirm_expensive=true`.",
                    "Оценки качества 1-5 можно отправлять через /api/model/feedback.",
                ],
            }

        @self.app.post("/api/assistant/query")
        async def assistant_query(
            request: Request,
            payload: dict = Body(...),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            x_krab_client: str = Header(default="", alias="X-Krab-Client"),
            x_idempotency_key: str = Header(default="", alias="X-Idempotency-Key"),
            token: str = Query(default=""),
        ):
            """
            Выполняет AI-запрос напрямую через web-панель (без Telegram чата).
            Это must-have для web-first сценариев управления Крабом.
            """
            self._assert_write_access(x_krab_web_key, token)
            client_ip = request.client.host if request.client else "unknown"
            client_key = (x_krab_client or "").strip() or client_ip
            idem_key = (x_idempotency_key or "").strip()
            cached = self._idempotency_get("assistant_query", idem_key)
            if cached:
                return cached
            self._enforce_assistant_rate_limit(client_key)
            router = self.deps.get("router")
            if not router:
                raise HTTPException(status_code=503, detail="router_not_configured")

            prompt = str(payload.get("prompt", "")).strip()
            if not prompt:
                raise HTTPException(status_code=400, detail="prompt_required")

            task_type = str(payload.get("task_type", "chat")).strip().lower() or "chat"
            use_rag = bool(payload.get("use_rag", False))
            preferred_model = payload.get("preferred_model")
            preferred_model_str = str(preferred_model).strip() if preferred_model else None
            confirm_expensive = bool(payload.get("confirm_expensive", False))

            try:
                reply = await router.route_query(
                    prompt=prompt,
                    task_type=task_type,
                    context=[],
                    chat_type="private",
                    is_owner=True,
                    use_rag=use_rag,
                    preferred_model=preferred_model_str,
                    confirm_expensive=confirm_expensive,
                )
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"assistant_query_failed: {exc}") from exc

            profile = router.classify_task_profile(prompt, task_type) if hasattr(router, "classify_task_profile") else task_type
            recommendation = (
                router.get_profile_recommendation(profile)
                if hasattr(router, "get_profile_recommendation")
                else {"profile": profile}
            )
            last_route = (
                router.get_last_route()
                if hasattr(router, "get_last_route")
                else {}
            )
            black_box = self.deps.get("black_box")
            if black_box and hasattr(black_box, "log_event"):
                black_box.log_event(
                    "web_assistant_query",
                    f"task_type={task_type} profile={profile} prompt_len={len(prompt)} client={client_key}",
                )
            response_payload = {
                "ok": True,
                "mode": "web_native",
                "task_type": task_type,
                "profile": profile,
                "recommendation": recommendation,
                "last_route": last_route,
                "reply": reply,
            }
            self._idempotency_set("assistant_query", idem_key, response_payload)
            return response_payload

        @self.app.get("/api/openclaw/report")
        async def openclaw_report():
            """Агрегированный health-report OpenClaw."""
            openclaw = self.deps.get("openclaw_client")
            if not openclaw:
                return {"available": False, "error": "openclaw_client_not_configured"}
            report = await openclaw.get_health_report()
            return {"available": True, "report": report}

        @self.app.get("/api/openclaw/deep-check")
        async def openclaw_deep_check():
            """Расширенная проверка OpenClaw (включая tool smoke и remediation)."""
            openclaw = self.deps.get("openclaw_client")
            if not openclaw:
                return {"available": False, "error": "openclaw_client_not_configured"}
            report = await openclaw.get_deep_health_report()
            return {"available": True, "report": report}

        @self.app.get("/api/openclaw/remediation-plan")
        async def openclaw_remediation_plan():
            """Пошаговый план исправления OpenClaw контуров."""
            openclaw = self.deps.get("openclaw_client")
            if not openclaw:
                return {"available": False, "error": "openclaw_client_not_configured"}
            report = await openclaw.get_remediation_plan()
            return {"available": True, "report": report}

        @self.app.get("/api/openclaw/browser-smoke")
        async def openclaw_browser_smoke(url: str = Query(default="https://example.com")):
            """Browser smoke check OpenClaw (endpoint/tool fallback)."""
            openclaw = self.deps.get("openclaw_client")
            if not openclaw:
                return {"available": False, "error": "openclaw_client_not_configured"}
            report = await openclaw.get_browser_smoke_report(url=url)
            return {"available": True, "report": report}

        @self.app.get("/api/provisioning/templates")
        async def provisioning_templates(entity: str = Query(default="agent")):
            """Возвращает шаблоны для provisioning UI/API."""
            provisioning = self.deps.get("provisioning_service")
            if not provisioning:
                raise HTTPException(status_code=503, detail="provisioning_service_not_configured")
            return {"entity": entity, "templates": provisioning.list_templates(entity)}

        @self.app.get("/api/provisioning/drafts")
        async def provisioning_drafts(
            status: str | None = Query(default=None),
            limit: int = Query(default=20, ge=1, le=200),
        ):
            """Список provisioning draft'ов."""
            provisioning = self.deps.get("provisioning_service")
            if not provisioning:
                raise HTTPException(status_code=503, detail="provisioning_service_not_configured")
            return {"drafts": provisioning.list_drafts(limit=limit, status=status)}

        @self.app.post("/api/provisioning/drafts")
        async def provisioning_create_draft(
            payload: dict = Body(...),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            x_idempotency_key: str = Header(default="", alias="X-Idempotency-Key"),
            token: str = Query(default=""),
        ):
            """Создает provisioning draft (write endpoint)."""
            self._assert_write_access(x_krab_web_key, token)
            idem_key = (x_idempotency_key or "").strip()
            cached = self._idempotency_get("provisioning_create_draft", idem_key)
            if cached:
                return cached
            provisioning = self.deps.get("provisioning_service")
            if not provisioning:
                raise HTTPException(status_code=503, detail="provisioning_service_not_configured")

            try:
                draft = provisioning.create_draft(
                    entity_type=payload.get("entity_type", "agent"),
                    name=payload.get("name", ""),
                    role=payload.get("role", ""),
                    description=payload.get("description", ""),
                    requested_by=payload.get("requested_by", "web_api"),
                    settings=payload.get("settings", {}),
                )
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            black_box = self.deps.get("black_box")
            if black_box and hasattr(black_box, "log_event"):
                black_box.log_event(
                    "web_provisioning_draft_create",
                    f"entity={payload.get('entity_type', 'agent')} name={payload.get('name', '')}",
                )
            response_payload = {"ok": True, "draft": draft}
            self._idempotency_set("provisioning_create_draft", idem_key, response_payload)
            return response_payload

        @self.app.get("/api/provisioning/preview/{draft_id}")
        async def provisioning_preview(draft_id: str):
            """Показывает diff для draft перед apply."""
            provisioning = self.deps.get("provisioning_service")
            if not provisioning:
                raise HTTPException(status_code=503, detail="provisioning_service_not_configured")
            try:
                preview = provisioning.preview_diff(draft_id)
            except Exception as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return {"ok": True, "preview": preview}

        @self.app.post("/api/provisioning/apply/{draft_id}")
        async def provisioning_apply(
            draft_id: str,
            confirm: bool = Query(default=False),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            x_idempotency_key: str = Header(default="", alias="X-Idempotency-Key"),
            token: str = Query(default=""),
        ):
            """Применяет draft в catalog (write endpoint)."""
            self._assert_write_access(x_krab_web_key, token)
            idem_key = (x_idempotency_key or "").strip()
            cached = self._idempotency_get("provisioning_apply", f"{draft_id}:{idem_key}")
            if cached:
                return cached
            provisioning = self.deps.get("provisioning_service")
            if not provisioning:
                raise HTTPException(status_code=503, detail="provisioning_service_not_configured")
            try:
                result = provisioning.apply_draft(draft_id, confirmed=confirm)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            black_box = self.deps.get("black_box")
            if black_box and hasattr(black_box, "log_event"):
                black_box.log_event(
                    "web_provisioning_apply",
                    f"draft_id={draft_id} confirmed={confirm}",
                )
            response_payload = {"ok": True, "result": result}
            self._idempotency_set("provisioning_apply", f"{draft_id}:{idem_key}", response_payload)
            return response_payload

    async def start(self):
        """Запуск сервера в фоне."""
        if self._server_task and not self._server_task.done():
            return

        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="warning", loop="asyncio")
        # Prevent uvicorn from overriding signal handlers (managed by Pyrogram/Main)
        # Note: "server.serve()" will invoke "config.setup_event_loop()" which might still interfere unless configured correctly.
        # But setting explicit loop above helps.
        # Ideally we pass install_signal_handlers=False if supported by Config (it is not a direct arg usually, but passed to Server).
        # Actually Config() has no install_signal_handlers arg. It's on Server.run() usually?
        # No, it IS an argument to Config __init__ in newer versions, or handled via setup.
        # Let's check typical usage.
        # Standard Uvicorn Config has NO install_signal_handlers arg.
        # But uvicorn.Server(config).serve() installs them unless overridden.
        # We can try to prevent it by subclassing or checking if we can pass a flag.
        # Actually Config DOES have it in recent versions? Let's assume standard 0.20+ has it?
        # Let's try passing it. If it fails, we catch TypeError.
        try:
            config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="warning", loop="asyncio")
            # We must monkeypatch to prevent signal install? Or just hope it works?
            # Actually simplest way is to NOT use Server.serve() directly if we can avoid signal handlers?
            # But serve() calls install_signal_handlers().
            # Let's override the install_signal_handlers method of the server instance!
            self._server = uvicorn.Server(config)
            self._server.install_signal_handlers = lambda: None
        except Exception as e:
            logger.warning(f"Could not disable uvicorn signal handlers: {e}")
            self._server = uvicorn.Server(config)

        logger.info(f"🌐 Web App starting at {self._public_base_url()}")
        self._server_task = asyncio.create_task(self._server.serve())

    async def stop(self):
        """Аккуратно останавливает uvicorn сервер."""
        if self._server:
            self._server.should_exit = True
        if self._server_task:
            await asyncio.wait([self._server_task], timeout=3)
