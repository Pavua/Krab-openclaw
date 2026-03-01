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


async def main_async() -> dict[str, Any]:
    load_dotenv()

    free_key = str(os.getenv("GEMINI_API_KEY_FREE", "") or "").strip()
    paid_key = str(os.getenv("GEMINI_API_KEY_PAID", "") or "").strip()
    openclaw_base = str(os.getenv("OPENCLAW_BASE_URL", os.getenv("OPENCLAW_URL", "http://127.0.0.1:18789")) or "").strip()
    openclaw_token = str(os.getenv("OPENCLAW_API_KEY", os.getenv("OPENCLAW_TOKEN", "")) or "").strip()

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
    openclaw_probe = await _probe_openclaw_chat(openclaw_base, openclaw_token)

    runtime_google_key = get_google_api_key_from_models(
        Path.home() / ".openclaw" / "agents" / "main" / "agent" / "models.json"
    )

    ready = bool(openclaw_probe.get("ok"))
    if not ready and free_probe.provider_status == "ok" and paid_probe.provider_status == "ok":
        # Даже при валидных ключах runtime может быть broken (например no models loaded).
        ready = False

    summary = {
        "status": "READY" if ready else "DEGRADED",
        "free": free_probe.to_dict(),
        "paid": paid_probe.to_dict(),
        "openclaw_chat": openclaw_probe,
        "runtime_google_key_masked": mask_secret(runtime_google_key),
        "env_free_key_masked": mask_secret(free_key),
        "env_paid_key_masked": mask_secret(paid_key),
    }
    return summary


def main() -> int:
    result = asyncio.run(main_async())
    print("=== Cloud Chain Check ===")
    print(f"Итог: {result['status']}")
    print(f"Free key status: {result['free']['provider_status']}")
    print(f"Paid key status: {result['paid']['provider_status']}")
    print(f"OpenClaw chat ok: {result['openclaw_chat'].get('ok')}")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
