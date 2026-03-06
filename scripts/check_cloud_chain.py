#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
One-click диагностика cloud chain для Krab/OpenClaw.

Проверяет:
1) Формат и доступность free/paid ключей Gemini.
2) Текущий runtime key в OpenClaw models.json.
3) Ответ OpenClaw /v1/chat/completions на тестовый запрос
   (с детекцией ложного 200/"No models loaded").

Итог:
- Печатает читаемый отчёт + финальный JSON статус READY/DEGRADED.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.cloud_key_probe import (
    get_google_api_key_from_models,
    mask_secret,
    probe_gemini_key,
)


def _semantic_error(text: str) -> str | None:
    low = (text or "").lower()
    if "no models loaded" in low:
        return "model_not_loaded"
    if "quota" in low or "429" in low:
        return "quota_exceeded"
    if "api keys are not supported" in low:
        return "unsupported_key_type"
    if "unauthenticated" in low or "invalid api key" in low:
        return "auth_invalid"
    return None


def _read_runtime_openclaw_token() -> str:
    """
    Возвращает актуальный токен gateway из runtime-конфига OpenClaw.

    Почему так:
    - `.env` нередко содержит устаревший `OPENCLAW_TOKEN`/`OPENCLAW_API_KEY`;
    - реальный токен живёт в `~/.openclaw/openclaw.json`.
    """
    cfg_path = Path.home() / ".openclaw" / "openclaw.json"
    try:
        if not cfg_path.exists():
            return ""
        payload = json.loads(cfg_path.read_text(encoding="utf-8"))
        gateway = payload.get("gateway", {}) if isinstance(payload, dict) else {}
        auth = gateway.get("auth", {}) if isinstance(gateway, dict) else {}
        token = ""
        if isinstance(auth, dict):
            token = str(auth.get("token", "") or "").strip()
        if not token and isinstance(gateway, dict):
            token = str(gateway.get("token", "") or "").strip()
        return token
    except (OSError, ValueError, TypeError):
        return ""


async def _probe_openclaw_chat(base_url: str, token: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "google/gemini-2.5-flash",
        "messages": [{"role": "user", "content": "Ответь одним словом: ping"}],
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=25.0, headers=headers) as client:
            response = await client.post(f"{base_url.rstrip('/')}/v1/chat/completions", json=payload)
        body = response.text[:1200]
        if response.status_code != 200:
            return {
                "ok": False,
                "status": response.status_code,
                "error": "http_error",
                "body": body,
            }

        semantic = None
        content = ""
        try:
            data = response.json()
            content = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content", ""))
            semantic = _semantic_error(content)
        except Exception as exc:  # noqa: BLE001
            semantic = f"invalid_json:{exc}"

        return {
            "ok": semantic is None,
            "status": 200,
            "error": semantic,
            "assistant_content": content[:600],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status": 0,
            "error": "network_error",
            "body": str(exc),
        }


async def _probe_openclaw_models(base_url: str, token: str) -> dict[str, Any]:
    """Ненавязчивая проверка OpenClaw без запуска генерации."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            response = await client.get(f"{base_url.rstrip('/')}/v1/models")
        body = response.text[:1200]
        if response.status_code != 200:
            return {
                "ok": False,
                "status": response.status_code,
                "error": "http_error",
                "body": body,
                "has_gemini_25_flash": False,
            }
        has_gemini = False
        try:
            payload = response.json()
            models = payload.get("data", []) if isinstance(payload, dict) else []
            model_ids = [str(item.get("id", "") or "") for item in models if isinstance(item, dict)]
            has_gemini = "google/gemini-2.5-flash" in model_ids
        except Exception:  # noqa: BLE001
            pass
        return {
            "ok": True,
            "status": 200,
            "error": "ok",
            "has_gemini_25_flash": has_gemini,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status": 0,
            "error": "network_error",
            "body": str(exc),
            "has_gemini_25_flash": False,
        }


async def main_async(*, chat_probe: bool) -> dict[str, Any]:
    load_dotenv()

    free_key = str(os.getenv("GEMINI_API_KEY_FREE", "") or "").strip()
    paid_key = str(os.getenv("GEMINI_API_KEY_PAID", "") or "").strip()
    openclaw_base = str(os.getenv("OPENCLAW_BASE_URL", os.getenv("OPENCLAW_URL", "http://127.0.0.1:18789")) or "").strip()
    env_openclaw_token = str(os.getenv("OPENCLAW_API_KEY", os.getenv("OPENCLAW_TOKEN", "")) or "").strip()
    runtime_openclaw_token = _read_runtime_openclaw_token()
    openclaw_token = runtime_openclaw_token or env_openclaw_token

    free_probe = await probe_gemini_key(
        free_key,
        key_source="env:GEMINI_API_KEY_FREE",
        key_tier="free",
    )
    paid_probe = await probe_gemini_key(
        paid_key,
        key_source="env:GEMINI_API_KEY_PAID",
        key_tier="paid",
    )
    openclaw_models_probe = await _probe_openclaw_models(openclaw_base, openclaw_token)
    if chat_probe:
        openclaw_chat_probe = await _probe_openclaw_chat(openclaw_base, openclaw_token)
    else:
        openclaw_chat_probe = {
            "ok": None,
            "status": "skipped",
            "error": "skipped_non_invasive",
            "body": "",
        }

    runtime_google_key = get_google_api_key_from_models(
        Path.home() / ".openclaw" / "agents" / "main" / "agent" / "models.json"
    )

    # READY = ключи валидны + gateway отвечает на /v1/models.
    # Глубокий chat-probe (с реальной генерацией) запускаем только явным флагом,
    # чтобы не триггерить локальный fallback и не грузить LM Studio в фоне.
    ready = bool(
        free_probe.provider_status == "ok"
        and paid_probe.provider_status == "ok"
        and openclaw_models_probe.get("ok")
    )

    summary = {
        "status": "READY" if ready else "DEGRADED",
        "free": free_probe.to_dict(),
        "paid": paid_probe.to_dict(),
        "openclaw_models": openclaw_models_probe,
        "openclaw_chat": openclaw_chat_probe,
        "runtime_google_key_masked": mask_secret(runtime_google_key),
        "env_free_key_masked": mask_secret(free_key),
        "env_paid_key_masked": mask_secret(paid_key),
        "openclaw_runtime_token_masked": mask_secret(runtime_openclaw_token),
        "openclaw_env_token_masked": mask_secret(env_openclaw_token),
        "chat_probe_enabled": bool(chat_probe),
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка cloud chain для OpenClaw")
    parser.add_argument(
        "--chat-probe",
        action="store_true",
        help="Запустить глубокую проверку /v1/chat/completions (может триггерить local fallback).",
    )
    args = parser.parse_args()

    result = asyncio.run(main_async(chat_probe=bool(args.chat_probe)))
    print("=== Cloud Chain Check ===")
    print(f"Итог: {result['status']}")
    print(f"Free key status: {result['free']['provider_status']}")
    print(f"Paid key status: {result['paid']['provider_status']}")
    print(f"OpenClaw models ok: {result['openclaw_models'].get('ok')}")
    print(f"OpenClaw chat probe: {result['openclaw_chat'].get('error')}")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
