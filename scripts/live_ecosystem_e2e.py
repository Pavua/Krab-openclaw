#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live E2E проверка 3-проектной экосистемы Krab.

Что делает:
1) Проверяет health основных узлов:
   - OpenClaw
   - Local LM (LM Studio API)
   - Voice Gateway
   - Krab Ear backend
2) Если доступен Voice Gateway — выполняет live lifecycle:
   create session -> patch -> diagnostics -> stop -> verify removed.
3) Сохраняет JSON-отчет в artifacts/ops и печатает краткий итог.

Зачем:
- Быстро подтвердить, что межпроектная интеграция реально работает в текущем окружении.
- Давать единый reproducible запуск для финального acceptance.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

# Добавляем корень проекта в sys.path для прямого запуска скрипта.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.voice_gateway_client import VoiceGatewayClient


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_lm_models_url(raw_url: str) -> str:
    base = (raw_url or "http://127.0.0.1:1234").strip().rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/models"
    return f"{base}/v1/models"


async def _http_ok(url: str, timeout_sec: float = 3.0, headers: dict[str, str] | None = None) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=max(0.5, timeout_sec))
    started = datetime.now(timezone.utc)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url) as response:
                status = int(response.status)
                text = await response.text()
        latency_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return {
            "ok": status == 200,
            "status_code": status,
            "latency_ms": latency_ms,
            "body_preview": (text or "")[:240],
            "url": url,
        }
    except Exception as exc:
        latency_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return {
            "ok": False,
            "status_code": 0,
            "latency_ms": latency_ms,
            "error": str(exc),
            "url": url,
        }


async def _voice_lifecycle(client: VoiceGatewayClient) -> dict[str, Any]:
    """
    Сквозная проверка жизненного цикла voice-сессии.
    Возвращает пошаговый лог и итоговый флаг ok.
    """
    result: dict[str, Any] = {
        "ok": False,
        "steps": [],
    }
    session_id = ""
    try:
        created = await client.start_session(source="live_e2e", tts_mode="hybrid", notify_mode="auto_on")
        create_ok = bool(created.get("ok"))
        session_id = str(created.get("result", {}).get("id", "")).strip()
        result["steps"].append(
            {
                "step": "create_session",
                "ok": create_ok and bool(session_id),
                "session_id": session_id,
                "response": created,
            }
        )
        if not create_ok or not session_id:
            return result

        patched = await client.set_translation_mode(session_id, "auto_to_ru")
        result["steps"].append({"step": "patch_translation_mode", "ok": bool(patched.get("ok")), "response": patched})

        diag = await client.get_diagnostics(session_id)
        result["steps"].append({"step": "get_diagnostics", "ok": bool(diag.get("ok")), "response": diag})

        stopped = await client.stop_session(session_id)
        result["steps"].append({"step": "stop_session", "ok": bool(stopped.get("ok")), "response": stopped})

        removed = await client.get_session(session_id)
        removed_ok = (not bool(removed.get("ok"))) and str(removed.get("error", "")).startswith("http_404")
        result["steps"].append({"step": "verify_removed", "ok": removed_ok, "response": removed})

        result["ok"] = all(bool(step.get("ok")) for step in result["steps"])
        return result
    except Exception as exc:
        result["steps"].append({"step": "exception", "ok": False, "error": str(exc)})
        return result
    finally:
        if session_id:
            # Защитная очистка: если сессия еще жива — пробуем остановить.
            try:
                await client.stop_session(session_id)
            except Exception:
                pass


async def main() -> int:
    openclaw_base = os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18789").strip().rstrip("/")
    lm_studio_url = os.getenv("LM_STUDIO_URL", "http://127.0.0.1:1234").strip()
    voice_base = os.getenv("VOICE_GATEWAY_URL", "http://127.0.0.1:8090").strip().rstrip("/")
    voice_api_key = os.getenv("VOICE_GATEWAY_API_KEY", "").strip()
    krab_ear_base = os.getenv("KRAB_EAR_BACKEND_URL", "http://127.0.0.1:8765").strip().rstrip("/")

    checks = {
        "openclaw": await _http_ok(f"{openclaw_base}/health"),
        "local_lm": await _http_ok(_normalize_lm_models_url(lm_studio_url)),
        "voice_gateway": await _http_ok(f"{voice_base}/health"),
        "krab_ear": await _http_ok(f"{krab_ear_base}/health"),
    }

    lifecycle: dict[str, Any] = {
        "skipped": True,
        "reason": "voice_gateway_unavailable",
        "ok": False,
        "steps": [],
    }
    if checks["voice_gateway"]["ok"]:
        vg_client = VoiceGatewayClient(base_url=voice_base, api_key=voice_api_key or None)
        lifecycle = await _voice_lifecycle(vg_client)
        lifecycle["skipped"] = False
        lifecycle["reason"] = ""

    # Деградация AI цепочки: cloud -> local fallback.
    cloud_ok = bool(checks["openclaw"]["ok"])
    local_ok = bool(checks["local_lm"]["ok"])
    if cloud_ok:
        degradation = "normal"
    elif local_ok:
        degradation = "degraded_to_local_fallback"
    else:
        degradation = "critical_no_ai_backend"

    # Блок "успеха" live e2e: AI backend есть + voice lifecycle успешен (если gateway доступен).
    ai_backend_ok = cloud_ok or local_ok
    voice_ok = bool(checks["voice_gateway"]["ok"])
    lifecycle_ok = bool(lifecycle.get("ok"))
    overall_ok = ai_backend_ok and ((not voice_ok) or lifecycle_ok)

    payload = {
        "generated_at": _now_iso(),
        "overall_ok": overall_ok,
        "degradation": degradation,
        "checks": checks,
        "voice_lifecycle": lifecycle,
    }

    ops_dir = Path("artifacts/ops")
    ops_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    out_path = ops_dir / f"live_ecosystem_e2e_{stamp}.json"
    with out_path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nreport_file={out_path}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
