#!/usr/bin/env python3
"""Wave 58-A: one-off probe для проверки доступности моделей в fallback chain.

Использует CLI subprocess bypass (src/integrations/cli_subprocess_bypass.py)
или прямой OpenClaw gateway — тот же путь что и реальный Krab.

Вывод: JSON массив результатов.
Exit code: 0 если хотя бы одна модель работает, 1 если все упали.

Использование:
    python scripts/krab_model_probe.py
    python scripts/krab_model_probe.py --models google/gemini-3-pro-preview gemini-2.5-flash
    python scripts/krab_model_probe.py --gateway http://127.0.0.1:18789 --timeout 15
    python scripts/krab_model_probe.py --json-only   # только JSON без прогресса
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Добавляем корень репозитория в sys.path чтобы src/* был доступен
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env", override=False)
except ImportError:
    pass

# Fallback chain по умолчанию — синхронизирована с Wave 47 production chain
DEFAULT_FALLBACK_CHAIN: list[str] = [
    "google/gemini-3-pro-preview",
    "google/gemini-3-flash-preview",
    "google/gemini-2.5-pro-preview",
    "google/gemini-2.5-flash",
    "google/gemini-flash-latest",
]

DEFAULT_GATEWAY = "http://127.0.0.1:18789"
DEFAULT_TIMEOUT = 20.0

PROBE_PROMPT = "Reply with exactly: ok"


# ---------------------------------------------------------------------------
# Probe через OpenClaw gateway (v1/chat/completions)
# ---------------------------------------------------------------------------


async def _probe_via_gateway(
    model: str,
    *,
    gateway_url: str,
    timeout_sec: float,
) -> dict[str, Any]:
    """Отправляет минимальный запрос через OpenClaw gateway."""
    try:
        import httpx
    except ImportError:
        return {
            "model": model,
            "ok": False,
            "latency_ms": 0,
            "error": "httpx не установлен (pip install httpx)",
            "channel": "gateway",
        }

    url = f"{gateway_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROBE_PROMPT}],
        "max_tokens": 8,
        "stream": False,
    }
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.post(url, json=payload)
        latency_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            return {
                "model": model,
                "ok": bool(text.strip()),
                "latency_ms": latency_ms,
                "error": None,
                "channel": "gateway",
                "response_snippet": text.strip()[:60],
            }
        return {
            "model": model,
            "ok": False,
            "latency_ms": latency_ms,
            "error": f"HTTP {resp.status_code}: {resp.text[:120]}",
            "channel": "gateway",
        }
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {
            "model": model,
            "ok": False,
            "latency_ms": latency_ms,
            "error": str(exc)[:200],
            "channel": "gateway",
        }


# ---------------------------------------------------------------------------
# Probe через google_genai_direct (Wave 18-B bypass)
# ---------------------------------------------------------------------------


async def _probe_via_direct(
    model: str,
    *,
    timeout_sec: float,
) -> dict[str, Any]:
    """Отправляет запрос через google_genai_direct bypass (тот же path что Krab)."""
    t0 = time.monotonic()
    try:
        from src.integrations.google_genai_direct import complete_direct, is_google_model

        if not is_google_model(model):
            return {
                "model": model,
                "ok": False,
                "latency_ms": 0,
                "error": "not_google_model",
                "channel": "direct_skip",
            }

        text = await complete_direct(
            model=model,
            messages=[{"role": "user", "content": PROBE_PROMPT}],
            timeout_sec=timeout_sec,
            max_output_tokens=8,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {
            "model": model,
            "ok": bool(text and text.strip()),
            "latency_ms": latency_ms,
            "error": None,
            "channel": "google_direct",
            "response_snippet": text.strip()[:60] if text else "",
        }
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {
            "model": model,
            "ok": False,
            "latency_ms": latency_ms,
            "error": str(exc)[:200],
            "channel": "google_direct",
        }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run_probe(
    models: list[str],
    *,
    gateway_url: str,
    timeout_sec: float,
    json_only: bool,
    use_direct: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    # Проверяем флаг — если paid key заблокирован, предупреждаем
    paid_enabled = os.environ.get("GEMINI_PAID_KEY_ENABLED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    paid_key_set = bool(os.environ.get("GEMINI_API_KEY_PAID", "").strip())

    if not json_only:
        print(
            f"[probe] paid_key_enabled={paid_enabled}, paid_key_configured={paid_key_set}",
            file=sys.stderr,
        )
        if paid_key_set and not paid_enabled:
            print(
                "[probe] WARN: GEMINI_API_KEY_PAID настроен, но GEMINI_PAID_KEY_ENABLED=0 — "
                "paid key заблокирован (Wave 58-A guard активен)",
                file=sys.stderr,
            )

    for model in models:
        if not json_only:
            print(f"[probe] {model} ...", end=" ", flush=True, file=sys.stderr)

        if use_direct:
            result = await _probe_via_direct(model, timeout_sec=timeout_sec)
        else:
            result = await _probe_via_gateway(
                model, gateway_url=gateway_url, timeout_sec=timeout_sec
            )

        results.append(result)

        if not json_only:
            status = "OK" if result["ok"] else f"FAIL ({result['error']})"
            print(f"{status} [{result['latency_ms']}ms]", file=sys.stderr)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Krab model probe (Wave 58-A)")
    parser.add_argument(
        "--models",
        nargs="+",
        default=None,
        help="Список моделей для probe (по умолчанию: DEFAULT_FALLBACK_CHAIN)",
    )
    parser.add_argument(
        "--gateway",
        default=DEFAULT_GATEWAY,
        help=f"OpenClaw gateway URL (default: {DEFAULT_GATEWAY})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout в секундах на один probe (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        default=False,
        help="Использовать google_genai_direct bypass вместо gateway",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        default=False,
        help="Только JSON output, без прогресса в stderr",
    )
    args = parser.parse_args()

    models = args.models or DEFAULT_FALLBACK_CHAIN

    results = asyncio.run(
        run_probe(
            models,
            gateway_url=args.gateway,
            timeout_sec=args.timeout,
            json_only=args.json_only,
            use_direct=args.direct,
        )
    )

    print(json.dumps(results, ensure_ascii=False, indent=2))

    any_ok = any(r["ok"] for r in results)
    sys.exit(0 if any_ok else 1)


if __name__ == "__main__":
    main()
