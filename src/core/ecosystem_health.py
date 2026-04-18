# -*- coding: utf-8 -*-
"""
Ecosystem Health Service — агрегатор статусов экосистемы Krab.

Назначение:
1) Давать единый health-срез по 3-проектной экосистеме:
   - Krab/OpenClaw (cloud brain),
   - локальный AI fallback (LM Studio/Ollama через router),
   - Krab Voice Gateway,
   - Krab Ear backend.
2) Вычислять уровень деградации цепочки `cloud -> local fallback`.
3) Использоваться из Web API и Telegram-команд без дублирования логики.

R20: каждый источник проверяется с индивидуальным timeout (per-source guard).
Если источник завис/упал — возвращаем частичный report с degraded=true для
этого источника, не роняем весь endpoint. Latency-диагностика добавлена в
поле _diagnostics.latency_summary для наблюдаемости без поломки UI-контракта.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import psutil

_PER_SOURCE_EXTRA_SEC = 0.0


class EcosystemHealthService:
    """Агрегатор health-статуса сервисов экосистемы Krab."""

    def __init__(
        self,
        router: Any,
        openclaw_client: Any | None = None,
        voice_gateway_client: Any | None = None,
        krab_ear_client: Any | None = None,
        krab_ear_backend_url: str | None = None,
        local_health_override: dict[str, Any] | None = None,
        timeout_sec: float = 2.5,
    ):
        self.router = router
        self.openclaw_client = openclaw_client
        self.voice_gateway_client = voice_gateway_client
        self.krab_ear_client = krab_ear_client
        self.krab_ear_backend_url = (
            (krab_ear_backend_url or os.getenv("KRAB_EAR_BACKEND_URL", "http://127.0.0.1:5005"))
            .strip()
            .rstrip("/")
        )
        # Позволяет верхнему слою переиспользовать уже собранный local runtime truth,
        # чтобы deep health не создавал ещё один лишний probe в LM Studio.
        self.local_health_override = (
            dict(local_health_override or {}) if local_health_override else None
        )
        # [R20] Гарантируем минимально вменяемый таймаут
        self.timeout_sec = max(0.5, float(timeout_sec))

    async def collect(self) -> dict[str, Any]:
        """
        Возвращает unified health snapshot с деградацией и рисками.

        [R20] Поведение при частичном сбое:
        - каждый источник обёрнут в _safe_run() с индивидуальным asyncio.wait_for;
        - timeout/error → degraded=True, ok=False, статус описан;
        - весь endpoint не падает даже если N из 4 источников зависли;
        - поле _diagnostics.latency_summary содержит latency по всем источникам.
        """

        async def _safe_run(coro, name: str) -> dict[str, Any]:
            """
            [R20] Per-source guard: таймаут строго на каждый источник.
            Возвращает словарь с ok=False, degraded=True и latency_ms при сбое.
            """
            started = time.monotonic()
            try:
                # [R20] Индивидуальный таймаут — каждый источник независим
                result = await asyncio.wait_for(coro, timeout=self.timeout_sec)
                # Проставляем degraded=False если источник ответил успешно
                if isinstance(result, dict) and "degraded" not in result:
                    result["degraded"] = False
                return result
            except asyncio.TimeoutError:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                return {
                    "ok": False,
                    "status": "timeout",
                    "degraded": True,  # [R20] явная пометка деградации
                    "latency_ms": elapsed_ms,
                    "source": name,
                }
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                return {
                    "ok": False,
                    "status": f"error: {exc}",
                    "degraded": True,  # [R20] явная пометка деградации
                    "latency_ms": elapsed_ms,
                    "source": name,
                }

        # [R20] Все источники проверяются параллельно; gather не бросает исключений
        # благодаря return_exceptions=True + _safe_run уже ловит всё сам.
        collect_started = time.monotonic()

        async def _return_local_override() -> dict[str, Any]:
            return dict(self.local_health_override or {})

        if self.local_health_override is not None:
            local_task = _return_local_override()
        else:
            local_task = _safe_run(self._check_local_health(), "local_lm")

        results = await asyncio.gather(
            _safe_run(self._check_client_health(self.openclaw_client, "openclaw"), "openclaw"),
            local_task,
            _safe_run(
                self._check_client_health(self.voice_gateway_client, "voice_gateway"),
                "voice_gateway",
            ),
            _safe_run(self._check_krab_ear_health(), "krab_ear"),
            return_exceptions=True,
        )
        total_collect_ms = int((time.monotonic() - collect_started) * 1000)

        def _get_res(idx: int, name: str) -> dict[str, Any]:
            """
            Защитный fallback: если gather всё же вернул Exception (крайний случай) —
            конвертируем в degraded-словарь.
            """
            r = results[idx]
            if isinstance(r, Exception):
                return {
                    "ok": False,
                    "status": f"error: {r.__class__.__name__}",
                    "degraded": True,
                    "latency_ms": 0,
                    "source": name,
                }
            return r

        openclaw_check = _get_res(0, "openclaw")
        local_check = _get_res(1, "local_lm")
        voice_check = _get_res(2, "voice_gateway")
        ear_check = _get_res(3, "krab_ear")

        resources = self._collect_resource_metrics()
        ca = getattr(self.router, "cost_analytics", None) or getattr(
            self.router, "cost_engine", None
        )
        budget = ca.get_budget_status() if ca and hasattr(ca, "get_budget_status") else {}

        queue_metrics = {}
        token_status = {"is_configured": False, "masked_key": None}

        if hasattr(self.router, "task_queue") and self.router.task_queue:
            queue_metrics = self.router.task_queue.get_metrics()

        oc = self.openclaw_client
        if oc and hasattr(oc, "get_token_info"):
            token_status = oc.get_token_info()

        cloud_ok = bool(openclaw_check["ok"])
        local_ok = bool(local_check["ok"])

        if cloud_ok:
            degradation = "normal"
            ai_channel = "cloud"
        elif local_ok:
            degradation = "degraded_to_local_fallback"
            ai_channel = "local_fallback"
        else:
            degradation = "critical_no_ai_backend"
            ai_channel = "none"

        # Важный контекст: voice-поток рабочий только при готовности Gateway + Ear.
        voice_assist_ready = bool(voice_check["ok"]) and bool(ear_check["ok"])

        risk_level = "low"
        if degradation == "critical_no_ai_backend":
            risk_level = "high"
        elif degradation == "degraded_to_local_fallback" or not voice_assist_ready:
            risk_level = "medium"

        recommendations: list[str] = []
        if degradation == "degraded_to_local_fallback":
            recommendations.append(
                "OpenClaw offline: временно вести non-critical задачи через локальные модели."
            )
        elif degradation == "critical_no_ai_backend":
            recommendations.append(
                "Нет доступного AI backend: проверить OpenClaw и LM Studio/Ollama."
            )
        if not voice_check["ok"]:
            recommendations.append("Voice Gateway недоступен: команды `!call*` будут ограничены.")
        if not ear_check["ok"]:
            recommendations.append(
                "Krab Ear backend недоступен: desktop call-assist поток неактивен."
            )

        # [R20] Рекомендации по деградированным источникам
        degraded_sources = [
            name
            for name, check in [
                ("openclaw", openclaw_check),
                ("local_lm", local_check),
                ("voice_gateway", voice_check),
                ("krab_ear", ear_check),
            ]
            if check.get("degraded") and check.get("status") == "timeout"
        ]
        if degraded_sources:
            recommendations.append(
                f"⏱ Источники с timeout: {', '.join(degraded_sources)} — проверь их доступность."
            )

        # [R12] Дополнительные рекомендации на основе бюджета
        if budget.get("is_economy_mode"):
            recommendations.append(
                f"💰 Активен РЕЖИМ ЭКОНОМИИ: бюджет превышен или близок к лимиту ({budget.get('usage_percent')}%)."
            )

        runway = budget.get("runway_days", 30)
        if runway < 7:
            recommendations.append(
                f"⚠️ КРИТИЧЕСКИЙ БЮДЖЕТ: средств хватит примерно на {runway} дн. Рекомендуется пополнить баланс."
            )

        if not recommendations:
            recommendations.append("Экосистема в норме: поддерживай текущий режим мониторинга.")

        # [R20] Latency-диагностика: сводка по источникам для наблюдаемости.
        # Ключ _diagnostics изолирован и не используется UI-кнопками/скриптами,
        # поэтому добавление его не ломает существующий API контракт.
        all_latencies = {
            "openclaw": openclaw_check.get("latency_ms", 0),
            "local_lm": local_check.get("latency_ms", 0),
            "voice_gateway": voice_check.get("latency_ms", 0),
            "krab_ear": ear_check.get("latency_ms", 0),
        }
        slowest_source = max(all_latencies, key=lambda k: all_latencies[k])
        diagnostics = {
            "latency_summary": all_latencies,
            "slowest_source": slowest_source,
            "slowest_latency_ms": all_latencies[slowest_source],
            "total_collect_ms": total_collect_ms,
            "timeout_budget_sec": self.timeout_sec,
        }

        # [Session 10] Собираем статистику по новым подсистемам
        session_10 = self._collect_session_10_stats()
        # [Session 12] Wave 16 Chado-inspired modules
        session_12 = self._collect_session_12_stats()

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": "ok"
            if degradation == "normal" and voice_assist_ready
            else ("critical" if risk_level == "high" else "degraded"),
            "risk_level": risk_level,
            "degradation": degradation,
            "checks": {
                "openclaw": {**openclaw_check, "token_status": token_status},
                "local_lm": local_check,
                "voice_gateway": voice_check,
                "krab_ear": ear_check,
            },
            "chain": {
                "active_ai_channel": ai_channel,
                "fallback_ready": local_ok,
                "voice_assist_ready": voice_assist_ready,
            },
            "resources": resources,
            "queue": queue_metrics,  # R15
            "budget": budget,
            "recommendations": recommendations[:8],  # Лимит рекомендаций
            "_diagnostics": diagnostics,  # [R20] Latency-диагностика
            "session_10": session_10,  # [Session 10] Новые подсистемы
            "session_12": session_12,  # [Session 12] Wave 16 modules
        }

    def _collect_session_10_stats(self) -> dict[str, Any]:
        """
        [Session 10] Агрегирует статистику по новым подсистемам.

        Собирает:
        - memory_validator — валидация инъекций в MEMORY.md/USER.md
        - memory_archive   — SQLite-архив сообщений (read-only query)
        - dedicated_chrome — выделенный Chrome instance для браузер-MCP
        - auto_restart     — авто-рестарт упавших сервисов
        - gemini_nonce     — cache-invalidation nonce для Gemini

        Все подмодули импортируются лениво в try/except — если модуль
        отсутствует, возвращаем пустой/дефолтный словарь, endpoint не падает.
        """
        return {
            "memory_validator": self._session_10_memory_validator(),
            "memory_archive": self._session_10_memory_archive(),
            "dedicated_chrome": self._session_10_dedicated_chrome(),
            "auto_restart": self._session_10_auto_restart(),
            "gemini_nonce": self._session_10_gemini_nonce(),
        }

    @staticmethod
    def _session_10_memory_validator() -> dict[str, Any]:
        """Стат по memory_validator (если модуль подключён)."""
        try:
            # Ленивый импорт — модуль может отсутствовать в ранних сессиях
            from src.core import memory_validator  # type: ignore
        except Exception:
            return {
                "available": False,
                "safe_total": 0,
                "injection_blocked_total": 0,
                "confirmed_total": 0,
                "confirm_failed_total": 0,
                "pending_count": 0,
            }

        try:
            stats = getattr(memory_validator, "stats", {}) or {}
            list_pending = getattr(memory_validator, "list_pending", None)
            pending = list_pending() if callable(list_pending) else []
            return {
                "available": True,
                "safe_total": int(stats.get("safe_total", 0)),
                "injection_blocked_total": int(stats.get("injection_blocked_total", 0)),
                "confirmed_total": int(stats.get("confirmed_total", 0)),
                "confirm_failed_total": int(stats.get("confirm_failed_total", 0)),
                "pending_count": len(pending) if pending is not None else 0,
            }
        except Exception as exc:
            return {
                "available": False,
                "error": str(exc),
                "safe_total": 0,
                "injection_blocked_total": 0,
                "confirmed_total": 0,
                "confirm_failed_total": 0,
                "pending_count": 0,
            }

    @staticmethod
    def _session_10_memory_archive() -> dict[str, Any]:
        """Стат по archive.db — размер, счётчики сообщений/чатов/чанков."""
        # Импорты внутри функции — не роняем endpoint, если путь недоступен
        import sqlite3 as _sqlite3
        from pathlib import Path as _Path

        db_path = _Path.home() / ".openclaw" / "krab_memory" / "archive.db"

        if not db_path.exists():
            return {
                "exists": False,
                "size_bytes": 0,
                "message_count": 0,
                "chats_count": 0,
                "chunks_count": 0,
            }

        try:
            size_bytes = db_path.stat().st_size
            # Read-only открытие чтобы не конфликтовать с live indexer'ом
            uri = f"file:{db_path}?mode=ro"
            conn = _sqlite3.connect(uri, uri=True, timeout=1.5)
            try:
                # Каждый COUNT обёрнут отдельно — если таблицы нет, 0 вместо краха
                def _count(table: str) -> int:
                    try:
                        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                        return int(row[0]) if row else 0
                    except Exception:
                        return 0

                return {
                    "exists": True,
                    "size_bytes": size_bytes,
                    "message_count": _count("messages"),
                    "chats_count": _count("chats"),
                    "chunks_count": _count("chunks"),
                }
            finally:
                conn.close()
        except Exception as exc:
            return {
                "exists": True,
                "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
                "message_count": 0,
                "chats_count": 0,
                "chunks_count": 0,
                "error": str(exc),
            }

    @staticmethod
    def _session_10_dedicated_chrome() -> dict[str, Any]:
        """Стат по dedicated Chrome (ENV-флаг + процесс + порт)."""
        enabled = os.getenv("DEDICATED_CHROME_ENABLED", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        port_raw = os.getenv("DEDICATED_CHROME_PORT", "9222").strip()
        try:
            port = int(port_raw) if port_raw else 9222
        except ValueError:
            port = 9222

        running = False
        try:
            # Ленивый импорт — модуль не обязателен
            from src.integrations import dedicated_chrome  # type: ignore

            checker = getattr(dedicated_chrome, "is_dedicated_chrome_running", None)
            if callable(checker):
                try:
                    running = bool(checker())
                except Exception:
                    running = False
        except Exception:
            running = False

        return {
            "enabled": enabled,
            "running": running,
            "port": port,
        }

    @staticmethod
    def _session_10_auto_restart() -> dict[str, Any]:
        """Стат по auto_restart_manager (ENV-флаг + tracked services)."""
        enabled = os.getenv("AUTO_RESTART_ENABLED", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        services: list[str] = []
        total_attempts = 0
        try:
            # Ленивый импорт — модуль не обязателен
            from src.core import auto_restart_manager  # type: ignore

            states = getattr(auto_restart_manager, "_states", None)
            if isinstance(states, dict):
                services = list(states.keys())
                for state in states.values():
                    attempts = getattr(state, "attempts", None)
                    if attempts is not None:
                        try:
                            total_attempts += len(attempts)
                        except Exception:
                            pass
        except Exception:
            pass

        return {
            "enabled": enabled,
            "services_tracked": services,
            "total_attempts_last_hour": total_attempts,
        }

    @staticmethod
    def _session_10_gemini_nonce() -> dict[str, Any]:
        """Стат по gemini_cache_nonce — количество отслеживаемых чатов."""
        tracked = 0
        try:
            # Ленивый импорт — модуль не обязателен
            from src.core import gemini_cache_nonce  # type: ignore

            nonce_map = getattr(gemini_cache_nonce, "_GEMINI_NONCE_MAP", None)
            if nonce_map is not None:
                try:
                    tracked = len(nonce_map)
                except Exception:
                    tracked = 0
        except Exception:
            tracked = 0

        return {"tracked_chats": tracked}

    # -------------------------------------------------------------------------
    # [Session 12] Wave 16 Chado-inspired modules
    # -------------------------------------------------------------------------

    @staticmethod
    def _collect_chat_windows() -> dict[str, Any]:
        """Per-chat ChatWindow LRU stats."""
        try:
            from .chat_window_manager import chat_window_manager  # type: ignore

            return {"available": True, **chat_window_manager.stats()}
        except (ImportError, Exception) as e:
            return {"available": False, "error": str(e)}

    @staticmethod
    def _collect_message_batcher() -> dict[str, Any]:
        """Message batcher pending counts."""
        try:
            from .message_batcher import message_batcher  # type: ignore

            return {"available": True, **message_batcher.stats()}
        except (ImportError, Exception) as e:
            return {"available": False, "error": str(e)}

    @staticmethod
    def _collect_chat_filter() -> dict[str, Any]:
        """Per-chat filter config stats."""
        try:
            from .chat_filter_config import chat_filter_config  # type: ignore

            return {"available": True, **chat_filter_config.stats()}
        except (ImportError, Exception) as e:
            return {"available": False, "error": str(e)}

    def _collect_session_12_stats(self) -> dict[str, Any]:
        """[Session 12] Агрегирует статистику Wave 16 Chado-inspired modules.

        Собирает:
        - chat_windows    — per-chat ChatWindow LRU stats
        - message_batcher — batcher pending counts
        - chat_filter     — per-chat filter config stats

        Все подмодули импортируются лениво в try/except — если модуль
        отсутствует, возвращаем {"available": False}, endpoint не падает.
        """
        return {
            "chat_windows": self._collect_chat_windows(),
            "message_batcher": self._collect_message_batcher(),
            "chat_filter": self._collect_chat_filter(),
        }

    def _collect_resource_metrics(self) -> dict[str, Any]:
        """[R11] Метрики потребления ресурсов macOS."""
        try:
            return {
                "cpu_percent": psutil.cpu_percent(),
                "ram_percent": psutil.virtual_memory().percent,
                "ram_available_gb": round(psutil.virtual_memory().available / (1024**3), 1),
                "load_avg": os.getloadavg() if hasattr(os, "getloadavg") else [0, 0, 0],
            }
        except Exception as e:
            return {"error": str(e)}

    async def _check_local_health(self) -> dict[str, Any]:
        """Проверка локального AI канала через ModelManager.health_check()."""
        started = time.monotonic()
        try:
            result = await self.router.health_check()
            ok = result.get("status") == "healthy" if isinstance(result, dict) else bool(result)
            latency_ms = int((time.monotonic() - started) * 1000)
            return {
                "ok": ok,
                "status": "ok" if ok else "unavailable",
                "latency_ms": latency_ms,
                "source": "model_manager.health_check",
            }
        except Exception as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            return {
                "ok": False,
                "status": f"error: {exc}",
                "latency_ms": latency_ms,
                "source": "model_manager.health_check",
            }

    async def _check_client_health(self, client: Any | None, source_name: str) -> dict[str, Any]:
        """Проверка health внешнего клиента (OpenClaw/Voice Gateway)."""
        if not client or not hasattr(client, "health_check"):
            return {
                "ok": False,
                "status": "not_configured",
                "latency_ms": 0,
                "source": source_name,
            }

        started = time.monotonic()
        try:
            result = await client.health_check()
            latency_ms = int((time.monotonic() - started) * 1000)
            ok = bool(result)
            return {
                "ok": ok,
                "status": "ok" if ok else "unavailable",
                "latency_ms": latency_ms,
                "source": source_name,
            }
        except Exception as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            return {
                "ok": False,
                "status": f"error: {exc}",
                "latency_ms": latency_ms,
                "source": source_name,
            }

    async def _check_krab_ear_health(self) -> dict[str, Any]:
        """Проверка Krab Ear backend через HTTP /health."""
        if self.krab_ear_client and hasattr(self.krab_ear_client, "health_check"):
            return await self._check_client_health(self.krab_ear_client, "krab_ear_client")

        url = f"{self.krab_ear_backend_url}/health"
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                response = await client.get(url)
                status = response.status_code
            latency_ms = int((time.monotonic() - started) * 1000)
            ok = status == 200
            return {
                "ok": ok,
                "status": "ok" if ok else f"http_{status}",
                "latency_ms": latency_ms,
                "source": url,
            }
        except Exception as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            return {
                "ok": False,
                "status": f"error: {exc}",
                "latency_ms": latency_ms,
                "source": url,
            }
