#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Регистрирует claude-proxy провайдер в OpenClaw models.json.

Запускается ОДИН раз после настройки сессии:
  python scripts/setup_claude_proxy_openclaw.py

Что делает:
  - Добавляет провайдер "claude-proxy" в ~/.openclaw/agents/main/agent/models.json
  - Точка входа: http://localhost:18791/v1
  - Модели: claude-proxy/claude-opus-4-6, claude-proxy/claude-sonnet-4-6, ...

После запуска: перезапустите OpenClaw gateway для применения конфига.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

MODELS_JSON = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "models.json"
PROXY_PROVIDER: dict = {
    "baseUrl": "http://localhost:17191/v1",
    "apiKey": "claude-proxy-local",
    "auth": "api-key",
    "api": "openai-completions",
    "models": [
        {
            "id": "claude-proxy/claude-opus-4-6",
            "name": "Claude Opus 4.6 (Pro via proxy)",
            "reasoning": False,
            "input": ["text"],
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": 200000,
            "maxTokens": 32000,
        },
        {
            "id": "claude-proxy/claude-sonnet-4-6",
            "name": "Claude Sonnet 4.6 (Pro via proxy)",
            "reasoning": False,
            "input": ["text"],
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": 200000,
            "maxTokens": 32000,
        },
        {
            "id": "claude-proxy/claude-haiku-4-5",
            "name": "Claude Haiku 4.5 (Pro via proxy)",
            "reasoning": False,
            "input": ["text"],
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": 200000,
            "maxTokens": 32000,
        },
        {
            "id": "claude-proxy/claude-3-7-sonnet",
            "name": "Claude 3.7 Sonnet (Pro via proxy)",
            "reasoning": True,
            "input": ["text"],
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": 200000,
            "maxTokens": 64000,
        },
    ],
}


def main() -> None:
    if not MODELS_JSON.exists():
        print(f"Error: {MODELS_JSON} not found. Is OpenClaw installed?")
        return

    # Backup
    backup = MODELS_JSON.with_suffix(
        f".json.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    shutil.copy2(MODELS_JSON, backup)
    print(f"Backup: {backup}")

    data = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
    providers: dict = data.setdefault("providers", {})

    if "claude-proxy" in providers:
        print("claude-proxy already present in models.json — updating models list.")
        providers["claude-proxy"]["models"] = PROXY_PROVIDER["models"]
    else:
        providers["claude-proxy"] = PROXY_PROVIDER
        print("Added claude-proxy provider.")

    MODELS_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Updated: {MODELS_JSON}")
    print()
    print("Next steps:")
    print("  1. Start proxy:  python scripts/claude_proxy_server.py")
    print("  2. Restart OpenClaw gateway to pick up new provider.")
    print("  3. Test: openclaw models status --json | grep claude-proxy")


if __name__ == "__main__":
    main()
