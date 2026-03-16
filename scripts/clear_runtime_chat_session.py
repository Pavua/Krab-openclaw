#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Очищает runtime chat-session через owner web API.

Что делает:
- отправляет owner-only POST на `/api/runtime/chat-session/clear`;
- использует тот же живой runtime, что и owner panel на `:8080`;
- возвращает компактный JSON-ответ для ops/handoff.

Зачем:
- это безопасная альтернатива ручной Telegram-команде `!clear`, когда нужно
  flush-нуть память конкретного чата из терминала или `.command` launcher;
- helper не создаёт новый `openclaw_client`, а работает через уже запущенный runtime,
  поэтому действительно чистит и in-memory session живого процесса.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _resolve_base_url() -> str:
    """Возвращает базовый URL owner runtime с env-friendly fallback."""
    override = str(os.getenv("KRAB_WEB_BASE_URL", "") or "").strip()
    if override:
        return override.rstrip("/")
    host = str(os.getenv("WEB_HOST", "127.0.0.1") or "127.0.0.1").strip()
    port = str(os.getenv("WEB_PORT", "8080") or "8080").strip()
    return f"http://{host}:{port}"


def _post_json(url: str, payload: dict, *, web_api_key: str = "", timeout: float = 15.0) -> dict:
    """Отправляет JSON в локальный owner endpoint и возвращает structured result."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if web_api_key:
        headers["X-Krab-Web-Key"] = web_api_key
    request = Request(url, data=body, headers=headers, method="POST")  # noqa: S310 - локальный endpoint.
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - локальный endpoint.
            raw = response.read().decode("utf-8", errors="replace")
            return {
                "ok": True,
                "status": int(getattr(response, "status", 200) or 200),
                "json": json.loads(raw),
            }
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        detail = raw
        try:
            payload = json.loads(raw)
            detail = payload.get("detail") or raw
        except Exception:  # noqa: BLE001
            pass
        return {
            "ok": False,
            "status": int(exc.code or 500),
            "error": str(detail),
        }
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "status": None,
            "error": str(exc),
        }


def main() -> int:
    """CLI entrypoint для очистки одного runtime chat-session."""
    parser = argparse.ArgumentParser(description="Очищает runtime chat-session через owner web API")
    parser.add_argument("--chat-id", required=True, help="chat_id, который нужно очистить")
    parser.add_argument("--note", default="", help="Короткая ops-заметка для следа в ответе")
    parser.add_argument("--base-url", default="", help="Явный base URL owner web runtime")
    parser.add_argument("--web-api-key", default="", help="Явный WEB_API_KEY; по умолчанию берётся из env")
    args = parser.parse_args()

    chat_id = str(args.chat_id or "").strip()
    if not chat_id:
        print("chat_id_required", file=sys.stderr)
        return 2

    base_url = str(args.base_url or "").strip() or _resolve_base_url()
    web_api_key = str(args.web_api_key or "").strip() or str(os.getenv("WEB_API_KEY", "") or "").strip()
    result = _post_json(
        f"{base_url}/api/runtime/chat-session/clear",
        {"chat_id": chat_id, "note": str(args.note or "").strip()},
        web_api_key=web_api_key,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
