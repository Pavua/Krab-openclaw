# -*- coding: utf-8 -*-
"""
Жизненный цикл приложения: health checks, старт/остановка userbot + web panel (Фаза 4/6.2).
"""

from __future__ import annotations

import asyncio
import os
import signal
import sqlite3
import sys

import structlog

from ..config import config
from ..core.access_control import get_effective_owner_label
from ..model_manager import model_manager
from ..openclaw_client import openclaw_client
from ..userbot_bridge import KraabUserbot, _telegram_send_queue
from .db_corruption_guard import (
    is_corruption_error,
    preflight_known_dbs,
    report_corruption_to_sentry,
)

# Exit code, который launchd конвенционально считает "не пытайся респавнить
# немедленно" — даёт человеку шанс заметить и пере-авторизовать сессию.
# (KeepAlive=true всё равно поднимет процесс, но throttle interval растёт.)
DB_CORRUPTION_EXIT_CODE = 78

logger = structlog.get_logger(__name__)


def _build_perceptor() -> object | None:
    """
    Поднимает локальный Perceptor для voice/STT контура.

    Почему отдельный helper:
    - userbot и web panel должны видеть один и тот же экземпляр;
    - если STT-модуль сломан, runtime не должен падать целиком, а должен честно
      деградировать с warning и без voice-ingress.
    """
    try:
        from ..modules.perceptor import Perceptor

        perceptor = Perceptor(config={})
        logger.info(
            "perceptor_ready",
            whisper_model=str(getattr(perceptor, "whisper_model", "") or ""),
            stt_isolated_worker=bool(getattr(perceptor, "stt_isolated_worker", False)),
        )
        return perceptor
    except Exception as exc:  # noqa: BLE001
        logger.warning("perceptor_init_failed", error=str(exc))
        return None


async def _start_web_panel(
    *,
    kraab_userbot: KraabUserbot | None = None,
    perceptor: object | None = None,
) -> object | None:
    """Starts the web panel on WEB_PORT (default 8080). Returns the WebApp instance or None."""
    try:
        from ..core.ecosystem_health import EcosystemHealthService
        from ..core.provisioning_service import ProvisioningService
        from ..integrations.krab_ear_client import KrabEarClient
        from ..integrations.voice_gateway_client import VoiceGatewayClient
        from ..modules.web_app import WebApp
        from ..modules.web_router_compat import WebRouterCompat

        router_compat = WebRouterCompat(model_manager, openclaw_client)
        voice_gateway_client = VoiceGatewayClient()
        krab_ear_client = KrabEarClient()

        from ..core.reaction_engine import reaction_engine  # noqa: PLC0415

        deps = {
            "router": router_compat,
            "openclaw_client": openclaw_client,
            "black_box": None,
            "health_service": EcosystemHealthService(
                router=router_compat,
                openclaw_client=openclaw_client,
                voice_gateway_client=voice_gateway_client,
                krab_ear_client=krab_ear_client,
            ),
            "provisioning_service": ProvisioningService(),
            "ai_runtime": None,
            "reaction_engine": reaction_engine,
            "voice_gateway_client": voice_gateway_client,
            "krab_ear_client": krab_ear_client,
            "perceptor": perceptor,
            "watchdog": None,
            "queue": None,
            "kraab_userbot": kraab_userbot,
        }

        port = int(os.getenv("WEB_PORT", "8080"))
        host = os.getenv("WEB_HOST", "127.0.0.1")
        web = WebApp(deps, port=port, host=host)
        await web.start()
        logger.info("web_panel_started", url=f"http://{host}:{port}")
        return web
    except Exception as e:
        logger.warning("web_panel_start_failed", error=str(e))
        return None


async def _warmup_memory_embeddings() -> None:
    """
    Background embedder warmup: pre-warm Model2Vec + optional ``embed_all_unindexed()``.

    Почему отдельный background-task:
    - bootstrap не должен блокироваться на тяжёлой операции эмбеддинга;
    - 72k chunks уже эмбеддены (repaired W20), bootstrap возвращает ~100ms
      идемпотентно; но новые chunks появляются между рестартами — incremental.
    - graceful: любое исключение логируется, task никогда не поднимает.

    Две фазы:
      1. Pre-warm модели (всегда) — mmap StaticModel + dummy encode. Не зависит
         от ``KRAB_RAG_PHASE2_ENABLED``: модель нужна и для MMR rerank в FTS
         режиме, а toggle flag=1 должен давать мгновенный hybrid query
         (<100ms), а не холодные 1.8s из-за lazy mmap.
      2. Embed unindexed chunks — только если ``KRAB_RAG_PHASE2_ENABLED=1``.
    """
    try:
        # Дать Krab полностью подняться перед heavy operation (userbot+web
        # уже должны обслуживать трафик, чтобы HF/Model2Vec загрузка не била
        # по первой волне запросов).
        await asyncio.sleep(30.0)

        # 1. PRE-WARM (всегда) — mmap модели в RAM.
        try:
            from ..core.memory_embedder import MemoryEmbedder  # noqa: PLC0415

            embedder = MemoryEmbedder()
            # Форсим lazy load модели + dummy encode — прогрев mmap реальный.
            embedder._ensure_model_loaded()
            if embedder._model is not None:
                embedder._model.encode(["warmup"])
            logger.info(
                "memory_model_prewarmed",
                dim=getattr(embedder, "_dim", None),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "memory_model_prewarm_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return

        # 2. EMBED UNINDEXED (только при включённом Phase 2).
        if os.getenv("KRAB_RAG_PHASE2_ENABLED", "0") != "1":
            logger.debug("memory_bootstrap_embed_skip", reason="phase2_disabled")
            return

        # Timeout 10 минут — 72k chunks уже эмбеддены (~1s reality), запас
        # на случай появления большого incremental после простоя.
        stats = await asyncio.wait_for(
            asyncio.to_thread(embedder.embed_all_unindexed),
            timeout=600.0,
        )
        logger.info(
            "memory_bootstrap_embed_done",
            processed=getattr(stats, "chunks_processed", None),
            skipped=getattr(stats, "chunks_skipped", None),
        )
    except asyncio.TimeoutError:
        logger.warning("memory_bootstrap_embed_timeout", timeout_s=600)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "memory_bootstrap_embed_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )


async def _warmup_runtime_route_truth() -> None:
    """
    Подтверждает живой route-truth вскоре после старта runtime.

    Зачем отдельный background-task:
    - не держим bootstrap на лишние секунды, пока userbot уже стартует;
    - после рестарта web/UI быстрее перестаёт показывать ложный broken primary;
    - если current primary реально не отвечает, route сохранит фактический fallback/error.
    """
    await asyncio.sleep(1.5)
    try:
        # Ночной smoke 2026-04-19 показал, что production-route warmup может
        # зависнуть на транспортном слое Codex/OpenClaw и косвенно оставить
        # owner-panel в состоянии `Syncing...`. Верхний timeout здесь важнее
        # идеальной стартовой route-truth: runtime должен оставаться живым,
        # даже если первичная модель или gateway-stream сейчас деградировали.
        report = await asyncio.wait_for(
            openclaw_client.warmup_runtime_route(),
            timeout=float(os.getenv("KRAB_RUNTIME_ROUTE_WARMUP_TIMEOUT_SEC", "20")),
        )
        logger.info(
            "runtime_route_warmup_finished",
            ok=bool(report.get("ok")),
            skipped=bool(report.get("skipped")),
            reason=str(report.get("reason") or ""),
            route=report.get("route") or {},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("runtime_route_warmup_task_failed", error=str(exc))


async def run_app() -> None:
    """
    Запускает приложение: баннер, проверки здоровья, web panel, userbot start → wait → stop.
    Вызывать после validate_config().
    """
    print(f"""
    🦀 KRAB USERBOT STARTED 🦀
    Owner: {get_effective_owner_label()}
    Mode: {config.LOG_LEVEL}
    RAM Limit: {config.MAX_RAM_GB}GB
    """)

    # DB corruption circuit breaker (Session 26): integrity_check на known DB
    # перед запуском userbot. Если session corrupt — quarantine + exit, чтобы
    # launchd KeepAlive=true НЕ зацикливал нас на битой базе (incident
    # 26.04.2026: 322 fatal_error events за 24h из-за corrupt kraab.session).
    try:
        preflight_reports = preflight_known_dbs()
    except Exception as exc:  # noqa: BLE001 — guard не должен ронять boot
        logger.warning("db_preflight_failed", error=str(exc))
        preflight_reports = []
    critical_quarantined = [
        r for r in preflight_reports if r.get("quarantined") and r.get("critical")
    ]
    for r in preflight_reports:
        if r.get("quarantined"):
            logger.error(
                "db_corruption_detected",
                path=r["path"],
                kind=r["kind"],
                detail=r["detail"],
                quarantine_path=r["quarantine_path"],
                critical=r["critical"],
            )
    if critical_quarantined:
        # Critical session corrupt → НЕ продолжаем boot. Owner должен
        # пере-авторизоваться. Exit graceful — main._run_with_retry поймает
        # SystemExit как clean exit (не ConnectionError → не retry-loop).
        logger.error(
            "boot_aborted_session_corrupt",
            quarantined=[r["path"] for r in critical_quarantined],
            exit_code=DB_CORRUPTION_EXIT_CODE,
        )
        sys.exit(DB_CORRUPTION_EXIT_CODE)

    lm_health = await model_manager.health_check()
    claw_health = await openclaw_client.health_check()
    logger.info("system_check", lm_studio=lm_health, openclaw=claw_health)

    if not claw_health:
        logger.warning("openclaw_unreachable", url=config.OPENCLAW_URL)

    # W32: explicit reset send-queue singleton — защита от foreign-loop state,
    # оставшегося от предыдущего процесса (например, при рестарте внутри
    # одного Python-интерпретатора через retry-loop).
    _telegram_send_queue.reset()

    perceptor = _build_perceptor()
    kraab = KraabUserbot(perceptor=perceptor)
    await _start_web_panel(kraab_userbot=kraab, perceptor=perceptor)
    stop_event = asyncio.Event()
    warmup_task: asyncio.Task | None = None
    embed_bootstrap_task: asyncio.Task | None = None

    def _request_stop(reason: str) -> None:
        """Запрашивает штатную остановку приложения без форс-килла."""
        if not stop_event.is_set():
            logger.info("stop_requested", reason=reason)
            stop_event.set()

    loop = asyncio.get_running_loop()
    for sig, reason in ((signal.SIGTERM, "sigterm"), (signal.SIGINT, "sigint")):
        try:
            loop.add_signal_handler(sig, lambda r=reason: _request_stop(r))
        except NotImplementedError:
            # На некоторых окружениях add_signal_handler недоступен (например, ограниченный runtime).
            pass

    # Признак штатной остановки: SIGTERM/SIGINT ставит stop_event → чистый выход.
    # Любое другое исключение (ConnectionError/OSError/TimeoutError/CancelledError
    # на сетевом drop) должно приводить к реinit Pyrofork сессии верхним retry-loop'ом,
    # а не тихо завершать процесс (см. Track B stability RCA, 2026-04-08).
    network_failure: BaseException | None = None
    try:
        await kraab.start()
        kraab_state = kraab.get_runtime_state()
        if str(kraab_state.get("startup_state")) == "running":
            logger.info("kraab_running")
        else:
            logger.warning("kraab_degraded_mode", **kraab_state)
        warmup_task = asyncio.create_task(_warmup_runtime_route_truth())
        # Memory Phase 2 — warmup background task (idempotent, feature-flagged).
        embed_bootstrap_task = asyncio.create_task(
            _warmup_memory_embeddings(), name="krab_memory_bootstrap_embed"
        )
        await stop_event.wait()
    except asyncio.CancelledError:
        if stop_event.is_set():
            logger.info("stopping_signal_received")
        else:
            logger.warning("run_app_cancelled_unexpectedly")
            network_failure = asyncio.CancelledError("pyrofork loop cancelled")
    except (ConnectionError, OSError, TimeoutError, asyncio.TimeoutError) as exc:
        logger.error(
            "run_app_network_error",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        network_failure = exc
    except sqlite3.DatabaseError as exc:
        # Late corruption detection: PRAGMA integrity_check мог пропустить
        # повреждение (например, locked во время preflight) — и SQLite
        # возразил уже при kraab.start(). Reactive quarantine + exit.
        if is_corruption_error(exc):
            logger.error(
                "db_corruption_detected_runtime",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            try:
                # Heuristic quarantine: попробуем найти path в exception args.
                # Если не нашли — повторный preflight уже сейчас quarantine`нет.
                preflight_known_dbs()
            except Exception:  # noqa: BLE001
                pass
            report_corruption_to_sentry(
                path="runtime",
                kind="late",
                detail=str(exc),
                quarantine_path="",
            )
            sys.exit(DB_CORRUPTION_EXIT_CODE)
        logger.error("fatal_error", error=str(exc), error_type=type(exc).__name__)
    except Exception as e:
        logger.error("fatal_error", error=str(e), error_type=type(e).__name__)
    finally:
        if warmup_task is not None and not warmup_task.done():
            warmup_task.cancel()
            try:
                await warmup_task
            except asyncio.CancelledError:
                pass
        if embed_bootstrap_task is not None and not embed_bootstrap_task.done():
            embed_bootstrap_task.cancel()
            try:
                await embed_bootstrap_task
            except asyncio.CancelledError:
                pass
        try:
            await kraab.stop()
        except Exception as stop_exc:  # noqa: BLE001
            logger.warning("kraab_stop_failed", error=str(stop_exc))
        logger.info("kraab_stopped")
    if network_failure is not None:
        raise network_failure
