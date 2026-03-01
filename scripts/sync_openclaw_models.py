#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Синхронизатор OpenClaw models.json из .env (явный и безопасный).

Что делает:
- Читает GEMINI_API_KEY_FREE/GEMINI_API_KEY_PAID из окружения.
- Обновляет providers.google.apiKey в ~/.openclaw/agents/main/agent/models.json
  приоритетно на free-ключ (если валиден AIza...), иначе paid.
- Не печатает секреты в открытом виде.

Зачем:
- Source-of-truth для runtime ключа в OpenClaw — models.json.
- Устраняет рассинхрон после рефакторинга и ручных правок.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv


def _mask(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"


def _is_aistudio(secret: str) -> bool:
    return bool(secret) and secret.startswith("AIza") and len(secret) >= 30


def _models_path() -> Path:
    return Path.home() / ".openclaw" / "agents" / "main" / "agent" / "models.json"


def main() -> int:
    load_dotenv()

    free_key = str(os.getenv("GEMINI_API_KEY_FREE", "") or "").strip()
    paid_key = str(os.getenv("GEMINI_API_KEY_PAID", "") or "").strip()

    path = _models_path()
    if not path.exists():
        print(json.dumps({"ok": False, "error": "models_json_not_found", "path": str(path)}, ensure_ascii=False))
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    providers = data.setdefault("providers", {})
    google = providers.setdefault("google", {})

    target_tier = ""
    target_key = ""
    if _is_aistudio(free_key):
        target_tier = "free"
        target_key = free_key
    elif _is_aistudio(paid_key):
        target_tier = "paid"
        target_key = paid_key

    if not target_key:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "no_valid_aistudio_key",
                    "free_masked": _mask(free_key),
                    "paid_masked": _mask(paid_key),
                    "hint": "Ожидается API key формата AIza...",
                },
                ensure_ascii=False,
            )
        )
        return 1

    prev_key = str(google.get("apiKey", "") or "")
    google["apiKey"] = target_key

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "path": str(path),
                "selected_tier": target_tier,
                "prev_key_masked": _mask(prev_key),
                "new_key_masked": _mask(target_key),
                "google_auth": str(google.get("auth", "api-key")),
                "google_api": str(google.get("api", "google-generative-ai")),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
