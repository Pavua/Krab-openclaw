#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
openclaw_account_bootstrap.py — безопасный bootstrap OpenClaw для новой macOS-учётки.

Что это:
- маленький idempotent helper для случая, когда в текущем `~/.openclaw`
  ещё нет базового runtime skeleton;
- использует официальный `openclaw onboard`, а не собирает `openclaw.json`
  вручную из воздуха;
- дополнительно создаёт пустые `models.json` и `auth-profiles.json`, чтобы
  проектные repair/readiness-контуры не падали на "файл не найден".

Зачем нужно:
- репозиторий у нас общий между учётками, а runtime/auth слой разнесён по HOME;
- на новой учётке launcher раньше упирался в отсутствие `~/.openclaw/openclaw.json`
  и не мог поднять gateway;
- этот helper делает старт новой учётки повторяемым и пригодным для handoff.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def _openclaw_root() -> Path:
    """Возвращает runtime-root OpenClaw для текущей учётки."""
    return Path.home() / ".openclaw"


def _workspace_dir() -> Path:
    """Канонический workspace для owner/userbot/memory контура проекта."""
    return _openclaw_root() / "workspace-main-messaging"


def _openclaw_config_path() -> Path:
    return _openclaw_root() / "openclaw.json"


def _models_path() -> Path:
    return _openclaw_root() / "agents" / "main" / "agent" / "models.json"


def _auth_profiles_path() -> Path:
    return _openclaw_root() / "agents" / "main" / "agent" / "auth-profiles.json"


def _empty_models_payload() -> dict[str, Any]:
    """
    Возвращает минимальный безопасный skeleton для models.json.

    Здесь принципиально не подставляем чужие секреты и не выдаём fake-ready
    состояние. Нужен только существующий JSON-контейнер, который потом смогут
    честно наполнить login/sync/repair-скрипты текущей учётки.
    """
    return {
        "providers": {
            "google": {
                "auth": "api-key",
                "api": "google-generative-ai",
                "apiKey": "",
                "models": [],
            },
            "lmstudio": {
                "api": "openai-completions",
                "auth": "api-key",
                "apiKey": "",
                "baseUrl": "http://localhost:1234/v1",
                "models": [],
            },
        }
    }


def _empty_auth_profiles_payload() -> dict[str, Any]:
    """Минимальный skeleton auth-profiles для текущей учётки."""
    return {
        "google": {"apiKey": ""},
        "gemini": {"apiKey": ""},
        "lmstudio": {"apiKey": ""},
    }


def _normalize_openclaw_config(path: Path) -> dict[str, Any]:
    """
    Доводит `openclaw.json` до минимально валидного provider-skeleton.

    Почему это нужно:
    - `openclaw onboard` создаёт config, но без project-specific runtime catalog;
    - наш repair-слой позже может заполнить `google.apiKey`, однако OpenClaw doctor
      ожидает, что у provider entry уже есть `baseUrl` и `models`;
    - лучше один раз создать пустой, но валидный каркас, чем потом ловить
      "Config invalid" на запуске gateway.
    """
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    models = payload.setdefault("models", {})
    providers = models.setdefault("providers", {})

    google = providers.setdefault("google", {})
    google.setdefault("api", "google-generative-ai")
    google.setdefault("auth", "api-key")
    google.setdefault("apiKey", "")
    google.setdefault("baseUrl", "https://generativelanguage.googleapis.com/v1beta")
    google.setdefault("models", [])

    lmstudio = providers.setdefault("lmstudio", {})
    lmstudio.setdefault("api", "openai-completions")
    lmstudio.setdefault("auth", "api-key")
    lmstudio.setdefault("apiKey", "")
    lmstudio.setdefault("baseUrl", "http://localhost:1234/v1")
    lmstudio.setdefault("models", [])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "path": str(path),
        "google_base_url": str(google.get("baseUrl") or ""),
        "google_models_count": len(google.get("models") or []),
        "lmstudio_base_url": str(lmstudio.get("baseUrl") or ""),
        "lmstudio_models_count": len(lmstudio.get("models") or []),
    }


def _write_json_if_missing(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Создаёт JSON-файл только если его ещё нет."""
    if path.exists():
        return {"path": str(path), "created": False, "exists": True}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"path": str(path), "created": True, "exists": True}


def _run_openclaw_onboard(openclaw_bin: str) -> dict[str, Any]:
    """
    Запускает официальный non-interactive onboarding.

    Почему именно `onboard`, а не ручной JSON:
    - OpenClaw сам генерирует корректный gateway token и meta-поля;
    - мы остаёмся совместимы с upstream форматом 2026.x;
    - rollback проще: helper только вызывает штатный инструмент.
    """
    workspace_dir = _workspace_dir()
    args = [
        openclaw_bin,
        "onboard",
        "--non-interactive",
        "--accept-risk",
        "--mode",
        "local",
        "--workspace",
        str(workspace_dir),
        "--skip-channels",
        "--skip-skills",
        "--skip-search",
        "--skip-ui",
        "--skip-daemon",
        "--skip-health",
    ]
    completed = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return {
        "cmd": args,
        "rc": int(completed.returncode),
        "output": str(completed.stdout or "").strip(),
    }


def bootstrap_openclaw_account(openclaw_bin: str) -> dict[str, Any]:
    """Готовит базовый OpenClaw runtime-layer для текущей учётки."""
    config_path = _openclaw_config_path()
    models_path = _models_path()
    auth_profiles_path = _auth_profiles_path()

    onboard_report: dict[str, Any] | None = None
    if not config_path.exists():
        onboard_report = _run_openclaw_onboard(openclaw_bin)
        if int(onboard_report.get("rc", 1)) != 0:
            return {
                "ok": False,
                "bootstrapped_config": False,
                "config_path": str(config_path),
                "onboard": onboard_report,
            }

    models_report = _write_json_if_missing(models_path, _empty_models_payload())
    auth_report = _write_json_if_missing(auth_profiles_path, _empty_auth_profiles_payload())
    config_report = _normalize_openclaw_config(config_path)

    return {
        "ok": True,
        "bootstrapped_config": onboard_report is not None,
        "config_path": str(config_path),
        "models_path": str(models_path),
        "auth_profiles_path": str(auth_profiles_path),
        "workspace_dir": str(_workspace_dir()),
        "onboard": onboard_report
        or {
            "cmd": [],
            "rc": 0,
            "output": "openclaw.json уже существует; onboarding не потребовался",
        },
        "config": config_report,
        "models": models_report,
        "auth_profiles": auth_report,
    }


def parse_args() -> argparse.Namespace:
    """Парсит аргументы CLI."""
    parser = argparse.ArgumentParser(description="Bootstrap OpenClaw runtime-layer for current macOS account.")
    parser.add_argument("--openclaw-bin", default=os.getenv("OPENCLAW_BIN", "openclaw"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = bootstrap_openclaw_account(str(args.openclaw_bin))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
