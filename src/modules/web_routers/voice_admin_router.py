# -*- coding: utf-8 -*-
"""
Voice admin router — Wave 183 (Session 48).

Owner-side панель ``/admin/voice`` + JSON API для voice-подсистемы Krab:
  • TTS state — текущий голос (VOICE_REPLY_VOICE/_SPEED/_DELIVERY/_BLOCKED_CHATS),
    дневной счётчик символов (Prometheus krab_tts_chars_total если есть),
    статус edge-tts кэша (voice_cache size).
  • STT state — Voice Gateway :8090 health (curl /health), Wave 138 метрики
    krab_voice_stt_total / krab_voice_stt_duration_seconds /
    krab_voice_stt_cost_eur_total, текущий Whisper provider.
  • Krab Ear status — backend process alive (через krab_ear_health_probe
    snapshot, Wave 79+180), IPC socket существование, consecutive_failures.
  • Voice metrics (Prometheus) — Wave 177 typing indicator
    (krab_typing_indicator_started_total etc) + Wave 138 STT.

Action buttons (write-access через ctx.assert_write_access):
- POST /api/admin/voice/restart_gateway → `launchctl kickstart -k
  gui/$UID/ai.krab.voice-gateway`.
- POST /api/admin/voice/restart_ear → `launchctl kickstart -k
  gui/$UID/ai.krab.ear.rest`.
- POST /api/admin/voice/test_tts {text, voice?} → синтез через
  voice_engine.text_to_speech, возвращает path + duration. Файл хранится
  в voice_cache/ — TTS smoke test без отправки в Telegram.

Match style of ``cron_admin_router.py`` + ``network_admin_router.py``.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from src.core.logger import get_logger
from src.core.subprocess_env import clean_subprocess_env

from ._context import RouterContext

_logger = get_logger(__name__)

# ── Конфигурация ────────────────────────────────────────────────────────────

# Voice Gateway local URL (Wave 138-ish). VOICE_GATEWAY_URL переопределяет.
_DEFAULT_VOICE_GATEWAY_URL = "http://127.0.0.1:8090"
_VOICE_GATEWAY_TIMEOUT_SEC: float = 3.0

# Default Krab Ear unix socket — same as krab_ear_health_probe.
_DEFAULT_KE_SOCKET = "~/Library/Application Support/KrabEar/krabear.sock"

# launchctl restart targets — whitelisted, owner может restart только эти.
_RESTART_LABELS: dict[str, str] = {
    "gateway": "ai.krab.voice-gateway",
    "ear": "ai.krab.ear.rest",
}

# Safe TTS test bounds — короткие сэмплы, чтобы не флудить edge-tts.
_TTS_TEST_MAX_CHARS = 200
# Voice ID validation: только разрешённые символы (edge-tts формат).
_VOICE_ID_PATTERN = re.compile(r"^[A-Za-z]{2}-[A-Za-z]{2}-[A-Za-z]+$")

# Каталог TTS-кэша (`<repo>/voice_cache/` — относительно voice_engine.py).
_VOICE_CACHE_DIR = Path(__file__).resolve().parents[3] / "voice_cache"


# ── Helpers ─────────────────────────────────────────────────────────────────


def _safe_getattr(obj: Any, attr: str, default: Any = None) -> Any:
    """Получает атрибут без падений на любых ошибках."""
    try:
        return getattr(obj, attr, default)
    except Exception:  # noqa: BLE001
        return default


def _iso_or_none(ts: float | None) -> str | None:
    """ISO-формат UTC из unix-timestamp; None если не задан."""
    if ts is None or ts <= 0:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(ts)))
    except (TypeError, ValueError):
        return None


def _age_sec(ts: float | None) -> float | None:
    """Возраст ts в секундах относительно now."""
    if ts is None or ts <= 0:
        return None
    try:
        age = time.time() - float(ts)
        return round(max(age, 0.0), 1)
    except (TypeError, ValueError):
        return None


def _run_launchctl(args: list[str], *, timeout: float = 10.0) -> dict[str, Any]:
    """Запуск launchctl без shell, с чистым env (как cron_admin)."""
    cmd = ["/bin/launchctl", *args]
    try:
        proc = subprocess.run(
            cmd,
            env=clean_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": "launchctl_timeout"}
    except FileNotFoundError:
        return {"ok": False, "returncode": -2, "stdout": "", "stderr": "launchctl_not_found"}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


def _validate_voice_id(voice: str) -> str:
    """Sanity для edge-tts voice id (формат ll-LL-NameNeural)."""
    voice = (voice or "").strip()
    if not voice or not _VOICE_ID_PATTERN.match(voice):
        raise HTTPException(status_code=400, detail="voice_invalid_id")
    return voice


# ── TTS state ───────────────────────────────────────────────────────────────


def _collect_tts_state() -> dict[str, Any]:
    """Сводит TTS-конфиг из src.config + voice_cache размер."""
    state: dict[str, Any] = {
        "voice": None,
        "speed": None,
        "delivery": None,
        "mode_default": None,
        "blocked_chats_count": 0,
        "blocked_chats_preview": [],
        "tts_max_chars": None,
        "voice_cache_files": 0,
        "voice_cache_size_bytes": 0,
    }
    try:
        # config — это инстанс класса Config, поля живут на классе/инстансе.
        # Берём instance, а если что — fallback на сам класс (env-default).
        from src.config import Config as _ConfigCls  # noqa: PLC0415
        from src.config import config as _config_inst  # noqa: PLC0415

        sources = (_config_inst, _ConfigCls)

        def _pick(name: str, default: Any = None) -> Any:
            for source in sources:
                value = _safe_getattr(source, name, None)
                if value is not None:
                    return value
            return default

        state["voice"] = _pick("VOICE_REPLY_VOICE")
        state["speed"] = _pick("VOICE_REPLY_SPEED")
        state["delivery"] = _pick("VOICE_REPLY_DELIVERY")
        state["mode_default"] = _pick("VOICE_MODE_DEFAULT")
        blocked = _pick("VOICE_REPLY_BLOCKED_CHATS", []) or []
        if isinstance(blocked, list):
            state["blocked_chats_count"] = len(blocked)
            # Превью первых 10 чатов — для UI, чтобы не светить весь список.
            state["blocked_chats_preview"] = [str(c) for c in blocked[:10]]
    except Exception as exc:  # noqa: BLE001
        _logger.warning("voice_admin.tts_config_read_failed", error=str(exc))

    # TTS_MAX_CHARS — глобальный лимит edge-tts.
    try:
        state["tts_max_chars"] = int(os.getenv("TTS_MAX_CHARS", "1800"))
    except (TypeError, ValueError):
        state["tts_max_chars"] = None

    # Размер кэша voice_cache/ — proxy для активности TTS.
    try:
        if _VOICE_CACHE_DIR.exists():
            files = list(_VOICE_CACHE_DIR.glob("*.ogg")) + list(_VOICE_CACHE_DIR.glob("*.mp3"))
            state["voice_cache_files"] = len(files)
            state["voice_cache_size_bytes"] = sum(
                (f.stat().st_size for f in files if f.exists()), 0
            )
    except OSError as exc:
        _logger.debug("voice_admin.voice_cache_stat_failed", error=str(exc))

    return state


# ── STT / Voice Gateway state ───────────────────────────────────────────────


async def _probe_voice_gateway() -> dict[str, Any]:
    """Дёргает Voice Gateway /health endpoint, возвращает {alive, latency_ms,...}."""
    base_url = (os.getenv("VOICE_GATEWAY_URL") or _DEFAULT_VOICE_GATEWAY_URL).rstrip("/")
    info: dict[str, Any] = {
        "base_url": base_url,
        "alive": False,
        "latency_ms": None,
        "status_code": None,
        "payload": None,
        "error": None,
    }
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_VOICE_GATEWAY_TIMEOUT_SEC) as client:
            resp = await client.get(base_url + "/health")
        info["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
        info["status_code"] = resp.status_code
        if resp.status_code == 200:
            info["alive"] = True
            try:
                info["payload"] = resp.json()
            except ValueError:
                info["payload"] = {"raw": resp.text[:200]}
        else:
            info["error"] = f"http_{resp.status_code}"
    except httpx.TimeoutException:
        info["error"] = "timeout"
        info["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
    except httpx.ConnectError:
        info["error"] = "connection_refused"
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def _collect_stt_metrics() -> dict[str, Any]:
    """Снимает Wave 138 STT-counter из Prometheus в виде сводки."""
    stats: dict[str, Any] = {
        "providers": {},  # provider → {ok, error, timeout, cost_eur}
        "duration_buckets": {},  # provider → {sum, count}
    }
    try:
        import src.core.prometheus_metrics as _pm  # noqa: PLC0415

        total = _safe_getattr(_pm, "krab_voice_stt_total", None)
        cost = _safe_getattr(_pm, "krab_voice_stt_cost_eur_total", None)
        duration = _safe_getattr(_pm, "krab_voice_stt_duration_seconds", None)

        if total is not None:
            # prometheus_client Counter — collect() → MetricFamily → samples.
            try:
                for family in total.collect():
                    for sample in family.samples:
                        if not sample.name.endswith("_total"):
                            continue
                        provider = sample.labels.get("provider", "unknown")
                        outcome = sample.labels.get("outcome", "unknown")
                        bucket = stats["providers"].setdefault(
                            provider, {"ok": 0.0, "error": 0.0, "timeout": 0.0}
                        )
                        bucket[outcome] = float(sample.value)
            except Exception as exc:  # noqa: BLE001
                _logger.debug("voice_admin.stt_total_collect_failed", error=str(exc))

        if cost is not None:
            try:
                for family in cost.collect():
                    for sample in family.samples:
                        if not sample.name.endswith("_total"):
                            continue
                        provider = sample.labels.get("provider", "unknown")
                        bucket = stats["providers"].setdefault(
                            provider, {"ok": 0.0, "error": 0.0, "timeout": 0.0}
                        )
                        bucket["cost_eur"] = float(sample.value)
            except Exception as exc:  # noqa: BLE001
                _logger.debug("voice_admin.stt_cost_collect_failed", error=str(exc))

        if duration is not None:
            try:
                for family in duration.collect():
                    for sample in family.samples:
                        provider = sample.labels.get("provider", "unknown")
                        slot = stats["duration_buckets"].setdefault(
                            provider, {"sum": 0.0, "count": 0}
                        )
                        if sample.name.endswith("_sum"):
                            slot["sum"] = float(sample.value)
                        elif sample.name.endswith("_count"):
                            slot["count"] = int(sample.value)
            except Exception as exc:  # noqa: BLE001
                _logger.debug("voice_admin.stt_duration_collect_failed", error=str(exc))
    except Exception as exc:  # noqa: BLE001
        _logger.warning("voice_admin.stt_metrics_failed", error=str(exc))
    return stats


def _collect_typing_indicator_metrics() -> dict[str, Any]:
    """Wave 177: typing indicator counters/histogram."""
    out: dict[str, Any] = {
        "started_total": 0.0,
        "cancelled_total": 0.0,
        "floodwait_total": 0.0,
        "duration_sum_sec": 0.0,
        "duration_count": 0,
    }
    try:
        import src.core.prometheus_metrics as _pm  # noqa: PLC0415

        for attr, key in (
            ("krab_typing_indicator_started_total", "started_total"),
            ("krab_typing_indicator_cancelled_total", "cancelled_total"),
            ("krab_typing_indicator_floodwait_total", "floodwait_total"),
        ):
            metric = _safe_getattr(_pm, attr, None)
            if metric is None:
                continue
            try:
                for family in metric.collect():
                    for sample in family.samples:
                        if sample.name.endswith("_total"):
                            out[key] = out.get(key, 0.0) + float(sample.value)
            except Exception:  # noqa: BLE001
                continue

        duration = _safe_getattr(_pm, "krab_typing_indicator_duration_seconds", None)
        if duration is not None:
            try:
                for family in duration.collect():
                    for sample in family.samples:
                        if sample.name.endswith("_sum"):
                            out["duration_sum_sec"] += float(sample.value)
                        elif sample.name.endswith("_count"):
                            out["duration_count"] += int(sample.value)
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        _logger.debug("voice_admin.typing_indicator_metrics_failed", error=str(exc))
    return out


# ── Krab Ear state (Wave 79 + 180 probe) ─────────────────────────────────────


def _collect_krab_ear_state() -> dict[str, Any]:
    """KE probe snapshot + IPC socket existence + backend process check."""
    state: dict[str, Any] = {
        "installed": None,
        "ipc_socket_path": None,
        "ipc_socket_exists": False,
        "last_probe_ts": None,
        "last_probe_iso": None,
        "last_probe_age_sec": None,
        "last_probe_ok": None,
        "last_success_ts": None,
        "last_success_iso": None,
        "last_success_age_sec": None,
        "consecutive_failures": 0,
        "total_failures": 0,
        "failures_by_reason": {},
        "backend_process_count": 0,
    }

    # Probe snapshot (Wave 180).
    try:
        from src.core.krab_ear_health_probe import get_snapshot  # noqa: PLC0415

        snap = get_snapshot() or {}
        state["installed"] = bool(snap.get("installed", True))
        state["last_probe_ts"] = float(snap.get("last_probe_ts") or 0.0) or None
        state["last_probe_iso"] = _iso_or_none(state["last_probe_ts"])
        state["last_probe_age_sec"] = _age_sec(state["last_probe_ts"])
        state["last_probe_ok"] = bool(snap.get("last_probe_ok", False))
        state["last_success_ts"] = float(snap.get("last_success_ts") or 0.0) or None
        state["last_success_iso"] = _iso_or_none(state["last_success_ts"])
        state["last_success_age_sec"] = _age_sec(state["last_success_ts"])
        state["consecutive_failures"] = int(snap.get("consecutive_failures") or 0)
        state["total_failures"] = int(snap.get("total_failures") or 0)
        state["failures_by_reason"] = dict(snap.get("failures_by_reason") or {})
    except Exception as exc:  # noqa: BLE001
        _logger.debug("voice_admin.ear_probe_snapshot_failed", error=str(exc))

    # IPC socket — основной канал KrabEar (Wave 180).
    socket_path = Path(os.path.expanduser(_DEFAULT_KE_SOCKET))
    state["ipc_socket_path"] = str(socket_path)
    try:
        state["ipc_socket_exists"] = socket_path.exists()
    except OSError:
        state["ipc_socket_exists"] = False

    # Backend process count — pgrep KrabEar (best-effort).
    try:
        proc = subprocess.run(
            ["/usr/bin/pgrep", "-fc", "KrabEar"],
            env=clean_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        if proc.returncode in (0, 1):  # 1 = ничего не найдено, всё ок
            try:
                state["backend_process_count"] = int((proc.stdout or "0").strip() or 0)
            except ValueError:
                state["backend_process_count"] = 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        _logger.debug("voice_admin.pgrep_krabear_failed", error=str(exc))

    return state


# ── Health roll-up ──────────────────────────────────────────────────────────


def _compute_overall_health(
    *,
    tts: dict[str, Any],
    voice_gateway: dict[str, Any],
    krab_ear: dict[str, Any],
) -> str:
    """Сводное состояние подсистемы для UI badge."""
    gw_ok = bool(voice_gateway.get("alive"))
    ear_installed = krab_ear.get("installed") is not False
    ear_ok = bool(krab_ear.get("last_probe_ok")) or krab_ear.get("backend_process_count", 0) > 0
    tts_ok = bool(tts.get("voice"))
    if gw_ok and ear_ok and tts_ok:
        return "ok"
    # Если KE не установлен — это not_installed, не degraded.
    if not ear_installed and gw_ok and tts_ok:
        return "ok"
    if not gw_ok and not ear_ok:
        return "down"
    return "degraded"


# ── Router factory ──────────────────────────────────────────────────────────


def build_voice_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: APIRouter с endpoints для voice subsystem admin."""
    router = APIRouter(tags=["voice-admin"])

    # ── GET /api/admin/voice/status ─────────────────────────────────────────

    @router.get("/api/admin/voice/status")
    async def voice_status() -> dict:
        """JSON snapshot: TTS + STT + Voice Gateway + Krab Ear + metrics.

        Все probes fail-safe — частичные данные лучше чем 500.
        """
        try:
            tts = _collect_tts_state()
        except Exception as exc:  # noqa: BLE001
            _logger.error("voice_admin.tts_state_failed", error=str(exc))
            tts = {"error": str(exc)}

        try:
            voice_gateway = await _probe_voice_gateway()
        except Exception as exc:  # noqa: BLE001
            _logger.error("voice_admin.gateway_probe_failed", error=str(exc))
            voice_gateway = {"alive": False, "error": str(exc)}

        try:
            # AGE-17 fix: _collect_krab_ear_state делает pgrep subprocess.run —
            # выносим в threadpool чтобы не блокировать event loop (Wave 193).
            krab_ear = await asyncio.to_thread(_collect_krab_ear_state)
        except Exception as exc:  # noqa: BLE001
            _logger.error("voice_admin.ear_state_failed", error=str(exc))
            krab_ear = {"installed": None, "error": str(exc)}

        try:
            stt_metrics = _collect_stt_metrics()
        except Exception as exc:  # noqa: BLE001
            _logger.error("voice_admin.stt_metrics_failed", error=str(exc))
            stt_metrics = {"error": str(exc), "providers": {}, "duration_buckets": {}}

        try:
            typing_metrics = _collect_typing_indicator_metrics()
        except Exception as exc:  # noqa: BLE001
            _logger.error("voice_admin.typing_metrics_failed", error=str(exc))
            typing_metrics = {"error": str(exc)}

        health = _compute_overall_health(tts=tts, voice_gateway=voice_gateway, krab_ear=krab_ear)
        return {
            "ok": True,
            "ts": time.time(),
            "health": health,
            "tts": tts,
            "voice_gateway": voice_gateway,
            "krab_ear": krab_ear,
            "stt_metrics": stt_metrics,
            "typing_indicator_metrics": typing_metrics,
        }

    # ── POST /api/admin/voice/restart_gateway ───────────────────────────────

    @router.post("/api/admin/voice/restart_gateway")
    async def restart_gateway(
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """`launchctl kickstart -k gui/$UID/ai.krab.voice-gateway`."""
        ctx.assert_write_access_fn(x_krab_web_key, token)
        label = _RESTART_LABELS["gateway"]
        uid = os.getuid()
        # AGE-17 fix: launchctl subprocess.run в threadpool (Wave 193).
        result = await asyncio.to_thread(_run_launchctl, ["kickstart", "-k", f"gui/{uid}/{label}"])
        _logger.info(
            "voice_admin.restart_gateway",
            label=label,
            returncode=result.get("returncode"),
        )
        if not result.get("ok"):
            raise HTTPException(
                status_code=500,
                detail=f"voice_restart_gateway_failed: {result.get('stderr') or 'unknown'}",
            )
        return {"ok": True, "label": label, "result": result}

    # ── POST /api/admin/voice/restart_ear ───────────────────────────────────

    @router.post("/api/admin/voice/restart_ear")
    async def restart_ear(
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """`launchctl kickstart -k gui/$UID/ai.krab.ear.rest`."""
        ctx.assert_write_access_fn(x_krab_web_key, token)
        label = _RESTART_LABELS["ear"]
        uid = os.getuid()
        # AGE-17 fix: launchctl subprocess.run в threadpool (Wave 193).
        result = await asyncio.to_thread(_run_launchctl, ["kickstart", "-k", f"gui/{uid}/{label}"])
        _logger.info(
            "voice_admin.restart_ear",
            label=label,
            returncode=result.get("returncode"),
        )
        if not result.get("ok"):
            raise HTTPException(
                status_code=500,
                detail=f"voice_restart_ear_failed: {result.get('stderr') or 'unknown'}",
            )
        return {"ok": True, "label": label, "result": result}

    # ── POST /api/admin/voice/test_tts ──────────────────────────────────────

    @router.post("/api/admin/voice/test_tts")
    async def test_tts(
        payload: dict[str, Any],
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """TTS smoke test через voice_engine.text_to_speech.

        Body: {"text": str, "voice"?: str}. Файл сохраняется в voice_cache/,
        в Telegram НЕ отправляется. Лимит длины — 200 chars (см.
        ``_TTS_TEST_MAX_CHARS``).
        """
        ctx.assert_write_access_fn(x_krab_web_key, token)

        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="voice_test_tts_body_invalid")
        text = str(payload.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="voice_test_tts_text_required")
        if len(text) > _TTS_TEST_MAX_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"voice_test_tts_text_too_long_max_{_TTS_TEST_MAX_CHARS}",
            )

        voice_id = payload.get("voice")
        if voice_id:
            voice_id = _validate_voice_id(str(voice_id))

        try:
            from src.voice_engine import text_to_speech  # noqa: PLC0415
        except ImportError as exc:
            raise HTTPException(status_code=500, detail=f"voice_engine_unavailable: {exc}") from exc

        filename = f"admin_test_{int(time.time())}.ogg"
        started = time.monotonic()
        try:
            output_path = await asyncio.wait_for(
                text_to_speech(text, filename=filename, voice=voice_id),
                timeout=20.0,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(status_code=504, detail="voice_test_tts_timeout") from exc
        except Exception as exc:  # noqa: BLE001
            _logger.error("voice_admin.test_tts_failed", error=str(exc))
            raise HTTPException(status_code=500, detail=f"voice_test_tts_failed: {exc}") from exc
        duration_ms = round((time.monotonic() - started) * 1000, 1)

        size_bytes = 0
        try:
            size_bytes = Path(output_path).stat().st_size
        except OSError:
            pass

        _logger.info(
            "voice_admin.test_tts_ok",
            chars=len(text),
            voice=voice_id,
            duration_ms=duration_ms,
            output=output_path,
        )
        return {
            "ok": True,
            "path": str(output_path),
            "chars": len(text),
            "voice": voice_id,
            "duration_ms": duration_ms,
            "size_bytes": size_bytes,
        }

    # ── GET /admin/voice — HTML page ────────────────────────────────────────

    @router.get("/admin/voice", response_class=HTMLResponse)
    async def voice_admin_page() -> HTMLResponse:
        """HTML страница со снимком voice subsystem (polling 15s)."""
        return HTMLResponse(_VOICE_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/voice ───────────────────────────────────────────────
# Стиль и DOM-construction идентичны network_admin_router (XSS-safe).

_VOICE_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Voice Admin</title>
    <style>
        :root {
            --bg: #0d0d0d;
            --card-bg: #121212;
            --border: #2a2a2a;
            --text: #e0e0e0;
            --text-muted: #888888;
            --accent: #7dd3fc;
            --ok: #22c55e;
            --warn: #facc15;
            --err: #ef4444;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: system-ui, -apple-system, BlinkMacSystemFont,
                "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg);
            color: var(--text);
            line-height: 1.4;
        }
        .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace; }
        header {
            display: flex; justify-content: space-between; align-items: center;
            padding: 12px 24px;
            background: #000; border-bottom: 1px solid var(--border);
        }
        header h1 { margin: 0; font-size: 1.4rem; }
        header .meta { color: var(--text-muted); font-size: 0.85rem; }
        main { padding: 16px 24px; display: grid; gap: 14px; }
        .row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
            gap: 14px;
        }
        .panel {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 12px 14px;
        }
        .panel h2 {
            margin: 0 0 10px 0;
            font-size: 0.95rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        .kv { display: grid; grid-template-columns: 180px 1fr; row-gap: 6px;
              column-gap: 12px; font-size: 0.9rem; }
        .kv dt { color: var(--text-muted); font-weight: 500; }
        .kv dd { margin: 0; word-break: break-word; }
        .badge {
            display: inline-block; padding: 2px 8px;
            border-radius: 4px; font-size: 0.75rem; font-weight: 500;
        }
        .badge-ok { background: rgba(34,197,94,0.15); color: var(--ok); }
        .badge-warn { background: rgba(250,204,21,0.15); color: var(--warn); }
        .badge-err { background: rgba(239,68,68,0.15); color: var(--err); }
        .badge-muted { background: rgba(255,255,255,0.06); color: var(--text-muted); }
        button {
            background: rgba(125,211,252,0.1);
            border: 1px solid var(--accent);
            color: var(--accent);
            padding: 5px 12px;
            font-size: 0.8rem;
            border-radius: 4px;
            cursor: pointer;
            margin-right: 6px;
            font-family: inherit;
        }
        button:hover { background: rgba(125,211,252,0.2); }
        button:disabled { opacity: 0.5; cursor: not-allowed; }
        button.danger { border-color: var(--err); color: var(--err); background: rgba(239,68,68,0.08); }
        button.danger:hover { background: rgba(239,68,68,0.18); }
        .actions { margin-bottom: 4px; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
        .err-banner {
            color: var(--err);
            padding: 10px 12px;
            background: rgba(239,68,68,0.08);
            border-radius: 4px;
            font-size: 0.85rem;
        }
        .info-banner {
            color: var(--accent);
            padding: 10px 12px;
            background: rgba(125,211,252,0.06);
            border-radius: 4px;
            font-size: 0.85rem;
        }
        input[type=text] {
            background: #1a1a1a; color: var(--text);
            border: 1px solid var(--border); border-radius: 4px;
            padding: 5px 8px; font-size: 0.85rem; font-family: inherit;
            min-width: 220px;
        }
        table.kvtab { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
        table.kvtab th, table.kvtab td { text-align: left; padding: 5px 8px;
            border-bottom: 1px solid var(--border); }
        table.kvtab th { color: var(--text-muted); text-transform: uppercase;
            font-size: 0.72rem; letter-spacing: 0.04em; }
    </style>
</head>
<body>
    <header>
        <h1>🎙️ Krab · Voice Admin</h1>
        <div class="meta">Polling каждые 15 сек · <span id="last-update">—</span></div>
    </header>
    <main>
        <div class="actions">
            <button id="btn-test-tts">🔊 Test TTS</button>
            <input id="inp-tts-text" type="text" placeholder="Текст для TTS (до 200 chars)" />
            <input id="inp-tts-voice" type="text" placeholder="voice (опц.)" style="min-width:140px;" />
            <button id="btn-restart-gw" class="danger">↻ Restart Gateway</button>
            <button id="btn-restart-ear" class="danger">↻ Restart Krab Ear</button>
            <span id="health-badge"></span>
        </div>
        <div id="err-banner"></div>
        <div id="action-result"></div>

        <div class="row">
            <div class="panel">
                <h2>TTS state</h2>
                <dl class="kv" id="kv-tts"></dl>
            </div>
            <div class="panel">
                <h2>Voice Gateway (:8090)</h2>
                <dl class="kv" id="kv-gateway"></dl>
            </div>
            <div class="panel">
                <h2>Krab Ear (Wave 180 IPC)</h2>
                <dl class="kv" id="kv-ear"></dl>
            </div>
        </div>
        <div class="row">
            <div class="panel">
                <h2>STT metrics (Wave 138)</h2>
                <div id="stt-table"></div>
            </div>
            <div class="panel">
                <h2>Typing indicator (Wave 177)</h2>
                <dl class="kv" id="kv-typing"></dl>
            </div>
        </div>
    </main>
    <script>
        function fmtAge(sec) {
            if (sec === null || sec === undefined) return '—';
            if (sec < 60) return sec.toFixed(1) + 's';
            if (sec < 3600) return (sec / 60).toFixed(1) + 'm';
            if (sec < 86400) return (sec / 3600).toFixed(1) + 'h';
            return (sec / 86400).toFixed(1) + 'd';
        }
        function fmtBytes(n) {
            if (n === null || n === undefined) return '—';
            if (n < 1024) return n + ' B';
            if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
            return (n / 1048576).toFixed(1) + ' MB';
        }
        function mkBadge(text, cls) {
            const span = document.createElement('span');
            span.className = 'badge ' + cls;
            span.textContent = text;
            return span;
        }
        function setKV(dlId, entries) {
            const dl = document.getElementById(dlId);
            while (dl.firstChild) dl.removeChild(dl.firstChild);
            for (const [key, value] of entries) {
                const dt = document.createElement('dt');
                dt.textContent = key;
                const dd = document.createElement('dd');
                if (value instanceof Node) dd.appendChild(value);
                else dd.textContent = (value === null || value === undefined)
                    ? '—' : String(value);
                if (key === 'voice' || key === 'ipc_socket_path') dd.className = 'mono';
                dl.appendChild(dt);
                dl.appendChild(dd);
            }
        }
        function healthBadge(h) {
            if (h === 'ok') return mkBadge('OK', 'badge-ok');
            if (h === 'down') return mkBadge('DOWN', 'badge-err');
            return mkBadge('DEGRADED', 'badge-warn');
        }
        function renderTts(t) {
            const blocked = (t.blocked_chats_count || 0) + ' chats';
            setKV('kv-tts', [
                ['voice', t.voice || '—'],
                ['speed', t.speed],
                ['delivery', t.delivery],
                ['mode_default', t.mode_default ? 'voice+text' : 'text-only'],
                ['blocked_chats', blocked],
                ['tts_max_chars', t.tts_max_chars],
                ['voice_cache_files', t.voice_cache_files],
                ['voice_cache_size', fmtBytes(t.voice_cache_size_bytes)],
            ]);
        }
        function renderGateway(g) {
            const aliveBadge = g.alive
                ? mkBadge('alive', 'badge-ok')
                : mkBadge('down', 'badge-err');
            setKV('kv-gateway', [
                ['base_url', g.base_url || '—'],
                ['alive', aliveBadge],
                ['latency_ms', g.latency_ms !== null && g.latency_ms !== undefined
                    ? g.latency_ms + ' ms' : '—'],
                ['status_code', g.status_code],
                ['error', g.error || '—'],
            ]);
        }
        function renderEar(e) {
            let installedBadge;
            if (e.installed === false) installedBadge = mkBadge('not_installed', 'badge-muted');
            else if (e.installed === true) installedBadge = mkBadge('installed', 'badge-ok');
            else installedBadge = mkBadge('unknown', 'badge-muted');
            const socketBadge = e.ipc_socket_exists
                ? mkBadge('present', 'badge-ok')
                : mkBadge('missing', 'badge-warn');
            const probeBadge = e.last_probe_ok
                ? mkBadge('ok', 'badge-ok')
                : (e.last_probe_ok === false ? mkBadge('fail', 'badge-err') : mkBadge('—', 'badge-muted'));
            setKV('kv-ear', [
                ['installed', installedBadge],
                ['ipc_socket', socketBadge],
                ['ipc_socket_path', e.ipc_socket_path || '—'],
                ['backend_processes', e.backend_process_count],
                ['last_probe', e.last_probe_age_sec !== null
                    ? fmtAge(e.last_probe_age_sec) + ' ago' : '—'],
                ['last_probe_ok', probeBadge],
                ['last_success', e.last_success_age_sec !== null
                    ? fmtAge(e.last_success_age_sec) + ' ago' : '—'],
                ['consecutive_failures', e.consecutive_failures],
                ['total_failures', e.total_failures],
            ]);
        }
        function renderStt(stt) {
            const container = document.getElementById('stt-table');
            while (container.firstChild) container.removeChild(container.firstChild);
            const providers = stt.providers || {};
            const keys = Object.keys(providers);
            if (keys.length === 0) {
                const div = document.createElement('div');
                div.className = 'info-banner';
                div.textContent = 'Нет метрик STT пока (provider × outcome counter пуст).';
                container.appendChild(div);
                return;
            }
            const table = document.createElement('table');
            table.className = 'kvtab';
            const thead = document.createElement('thead');
            const trh = document.createElement('tr');
            for (const h of ['Provider', 'OK', 'Error', 'Timeout', 'Cost EUR', 'Avg dur s']) {
                const th = document.createElement('th');
                th.textContent = h;
                trh.appendChild(th);
            }
            thead.appendChild(trh);
            table.appendChild(thead);
            const tbody = document.createElement('tbody');
            const buckets = stt.duration_buckets || {};
            for (const p of keys.sort()) {
                const row = providers[p] || {};
                const tr = document.createElement('tr');
                const tdP = document.createElement('td');
                tdP.className = 'mono';
                tdP.textContent = p;
                tr.appendChild(tdP);
                for (const k of ['ok', 'error', 'timeout']) {
                    const td = document.createElement('td');
                    td.textContent = String(row[k] || 0);
                    tr.appendChild(td);
                }
                const tdCost = document.createElement('td');
                tdCost.textContent = (row.cost_eur || 0).toFixed(4);
                tr.appendChild(tdCost);
                const slot = buckets[p] || {sum: 0, count: 0};
                const avg = slot.count > 0 ? (slot.sum / slot.count) : null;
                const tdAvg = document.createElement('td');
                tdAvg.textContent = avg !== null ? avg.toFixed(2) : '—';
                tr.appendChild(tdAvg);
                tbody.appendChild(tr);
            }
            table.appendChild(tbody);
            container.appendChild(table);
        }
        function renderTyping(t) {
            const avg = (t.duration_count || 0) > 0
                ? ((t.duration_sum_sec || 0) / t.duration_count).toFixed(2) + ' s' : '—';
            setKV('kv-typing', [
                ['started_total', t.started_total || 0],
                ['cancelled_total', t.cancelled_total || 0],
                ['floodwait_total', t.floodwait_total || 0],
                ['duration_count', t.duration_count || 0],
                ['avg_duration', avg],
            ]);
        }
        function showActionResult(msg, isError) {
            const box = document.getElementById('action-result');
            while (box.firstChild) box.removeChild(box.firstChild);
            const div = document.createElement('div');
            div.className = isError ? 'err-banner' : 'info-banner';
            div.textContent = msg;
            box.appendChild(div);
        }
        async function postAdmin(url, body) {
            const opts = { method: 'POST' };
            if (body !== undefined) {
                opts.headers = { 'Content-Type': 'application/json' };
                opts.body = JSON.stringify(body);
            }
            const res = await fetch(url, opts);
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
            return data;
        }
        async function triggerTestTts() {
            const text = (document.getElementById('inp-tts-text').value || '').trim();
            const voice = (document.getElementById('inp-tts-voice').value || '').trim();
            if (!text) {
                showActionResult('Введите текст для TTS теста.', true);
                return;
            }
            const btn = document.getElementById('btn-test-tts');
            btn.disabled = true;
            try {
                const body = { text: text };
                if (voice) body.voice = voice;
                const data = await postAdmin('/api/admin/voice/test_tts', body);
                showActionResult('🔊 TTS OK: ' + data.path + ' · ' + data.duration_ms +
                    ' ms · ' + data.size_bytes + ' B', false);
            } catch (e) {
                showActionResult('TTS test ошибка: ' + e.message, true);
            } finally {
                btn.disabled = false;
            }
        }
        async function triggerRestart(target) {
            if (!confirm('Restart ' + target + '?')) return;
            const btn = document.getElementById('btn-restart-' + (target === 'gateway' ? 'gw' : 'ear'));
            btn.disabled = true;
            try {
                const data = await postAdmin('/api/admin/voice/restart_' + target);
                showActionResult('↻ ' + target + ' restarted: ' + (data.label || ''), false);
                setTimeout(fetchStatus, 2000);
            } catch (e) {
                showActionResult('Restart ошибка: ' + e.message, true);
            } finally {
                btn.disabled = false;
            }
        }
        async function fetchStatus() {
            const errBanner = document.getElementById('err-banner');
            errBanner.textContent = '';
            try {
                const res = await fetch('/api/admin/voice/status');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                const hb = document.getElementById('health-badge');
                while (hb.firstChild) hb.removeChild(hb.firstChild);
                hb.appendChild(healthBadge(data.health));
                renderTts(data.tts || {});
                renderGateway(data.voice_gateway || {});
                renderEar(data.krab_ear || {});
                renderStt(data.stt_metrics || {});
                renderTyping(data.typing_indicator_metrics || {});
                document.getElementById('last-update').textContent =
                    new Date().toLocaleTimeString('ru-RU', { hour12: false });
            } catch (e) {
                const div = document.createElement('div');
                div.className = 'err-banner';
                div.textContent = 'Ошибка загрузки: ' + e.message;
                errBanner.appendChild(div);
            }
        }
        document.getElementById('btn-test-tts').addEventListener('click', triggerTestTts);
        document.getElementById('btn-restart-gw').addEventListener('click',
            () => triggerRestart('gateway'));
        document.getElementById('btn-restart-ear').addEventListener('click',
            () => triggerRestart('ear'));
        fetchStatus();
        setInterval(fetchStatus, 15000);
    </script>
</body>
</html>
"""
