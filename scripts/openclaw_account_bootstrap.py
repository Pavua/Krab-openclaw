#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Idempotent bootstrap helper для account-local `~/.openclaw`.

Что делает:
- при отсутствии `~/.openclaw/openclaw.json` запускает официальный `openclaw onboard`;
- гарантирует минимальный runtime skeleton для `models.json`, `auth-profiles.json`, `agent.json`;
- дозаполняет безопасные поля в существующем `openclaw.json`, не перетирая живую routing truth.

Зачем нужен:
- launcher зовёт этот helper на каждом старте, чтобы новая macOS-учётка не падала на пустом
  `~/.openclaw`;
- старые/частично мигрированные конфиги могут быть не полностью валидны, и такой bootstrap
  должен чинить только отсутствующие части, а не сбрасывать рабочую конфигурацию.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_PRIMARY_MODEL = "google/gemini-2.5-flash"
DEFAULT_OPENAI_FALLBACK = "openai/gpt-4o-mini"
GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
LMSTUDIO_BASE_URL = "http://localhost:1234/v1"


def _read_json(path: Path) -> dict[str, Any]:
    """Читает JSON-объект или возвращает пустой словарь, если файл отсутствует/битый."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Пишет JSON с человекочитаемым форматированием."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ensure_dict(parent: dict[str, Any], key: str) -> dict[str, Any]:
    """Гарантирует, что значение по ключу — словарь."""
    value = parent.get(key)
    if not isinstance(value, dict):
        value = {}
        parent[key] = value
    return value


def _ensure_list(parent: dict[str, Any], key: str) -> list[Any]:
    """Гарантирует, что значение по ключу — список."""
    value = parent.get(key)
    if not isinstance(value, list):
        value = []
        parent[key] = value
    return value


def _run_openclaw_onboard(openclaw_bin: str) -> dict[str, object]:
    """Запускает официальный onboard и возвращает machine-readable результат."""
    cmd = [openclaw_bin, "onboard"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    return {
        "cmd": cmd,
        "rc": int(proc.returncode),
        "output": output.strip(),
    }


def _seed_runtime_config(config_path: Path, models_path: Path) -> dict[str, Any]:
    """
    Дозаполняет `openclaw.json`, не ломая текущую routing truth.

    Правило простое:
    - если поле уже задано — уважаем существующее значение;
    - если поля нет — добавляем безопасный skeleton.
    """
    payload = _read_json(config_path)
    models_root = _read_json(models_path)

    models_section = _ensure_dict(payload, "models")
    providers = _ensure_dict(models_section, "providers")

    google_provider = _ensure_dict(providers, "google")
    google_provider.setdefault("baseUrl", GOOGLE_BASE_URL)
    _ensure_list(google_provider, "models")

    lmstudio_provider = _ensure_dict(providers, "lmstudio")
    lmstudio_provider.setdefault("baseUrl", LMSTUDIO_BASE_URL)
    _ensure_list(lmstudio_provider, "models")

    gateway = _ensure_dict(payload, "gateway")
    gateway_http = _ensure_dict(gateway, "http")
    endpoints = _ensure_dict(gateway_http, "endpoints")
    chat_completions = _ensure_dict(endpoints, "chatCompletions")
    chat_completions.setdefault("enabled", True)

    agents = _ensure_dict(payload, "agents")
    defaults = _ensure_dict(agents, "defaults")
    model_defaults = _ensure_dict(defaults, "model")

    runtime_primary = str(model_defaults.get("primary") or "").strip()
    if not runtime_primary:
        runtime_primary = DEFAULT_PRIMARY_MODEL
        model_defaults["primary"] = runtime_primary

    lmstudio_models = (
        (((models_root.get("providers") or {}) if isinstance(models_root.get("providers"), dict) else {}).get("lmstudio"))
        if isinstance(models_root, dict)
        else {}
    )
    lmstudio_model_items = (lmstudio_models.get("models") or []) if isinstance(lmstudio_models, dict) else []
    first_lmstudio_id = ""
    if isinstance(lmstudio_model_items, list):
        for item in lmstudio_model_items:
            if isinstance(item, dict) and str(item.get("id") or "").strip():
                first_lmstudio_id = str(item.get("id")).strip()
                break

    fallback_seed = f"lmstudio/{first_lmstudio_id}" if first_lmstudio_id else "lmstudio/local"
    existing_fallbacks = model_defaults.get("fallbacks")
    if not isinstance(existing_fallbacks, list) or not existing_fallbacks:
        model_defaults["fallbacks"] = [fallback_seed, DEFAULT_OPENAI_FALLBACK]

    subagents = _ensure_dict(defaults, "subagents")
    subagents.setdefault("model", runtime_primary)

    agent_list = _ensure_list(agents, "list")
    main_agent = None
    for item in agent_list:
        if isinstance(item, dict) and str(item.get("id") or "").strip() == "main":
            main_agent = item
            break
    if main_agent is None:
        main_agent = {"id": "main", "model": runtime_primary}
        agent_list.append(main_agent)
    else:
        main_agent.setdefault("model", runtime_primary)

    _write_json(config_path, payload)
    return payload


def bootstrap_openclaw_account(openclaw_bin: str) -> dict[str, Any]:
    """Готовит account-local `~/.openclaw` к безопасному старту launcher-а."""
    home = Path.home()
    openclaw_root = home / ".openclaw"
    config_path = openclaw_root / "openclaw.json"
    models_path = openclaw_root / "agents" / "main" / "agent" / "models.json"
    auth_profiles_path = openclaw_root / "agents" / "main" / "agent" / "auth-profiles.json"
    agent_config_path = openclaw_root / "agents" / "main" / "agent" / "agent.json"
    workspace_dir = openclaw_root / "workspace-main-messaging"

    bootstrapped_config = False
    if config_path.exists():
        onboard_report = {
            "cmd": [],
            "rc": 0,
            "output": "openclaw.json уже существует; onboarding не потребовался",
        }
    else:
        onboard_report = _run_openclaw_onboard(openclaw_bin)
        bootstrapped_config = bool(onboard_report.get("rc") == 0 and config_path.exists())
        if not config_path.exists():
            return {
                "ok": False,
                "bootstrapped_config": False,
                "config_path": str(config_path),
                "models_path": str(models_path),
                "auth_profiles_path": str(auth_profiles_path),
                "agent_config_path": str(agent_config_path),
                "workspace_dir": str(workspace_dir),
                "onboard": onboard_report,
            }

    if not models_path.exists():
        _write_json(models_path, {"providers": {}})
        models_created = True
    else:
        models_created = False

    if not auth_profiles_path.exists():
        _write_json(auth_profiles_path, {"profiles": {}})
        auth_created = True
    else:
        auth_created = False

    config_payload = _seed_runtime_config(config_path, models_path)
    primary_model = str(
        (((((config_payload.get("agents") or {}) if isinstance(config_payload.get("agents"), dict) else {}).get("defaults") or {})
          if isinstance((((config_payload.get("agents") or {}) if isinstance(config_payload.get("agents"), dict) else {}).get("defaults")), dict)
          else {}).get("model") or {}
         ).get("primary")
        or DEFAULT_PRIMARY_MODEL
    ).strip()

    agent_payload = _read_json(agent_config_path)
    agent_created = not agent_config_path.exists()
    if not isinstance(agent_payload, dict) or not agent_payload:
        agent_payload = {"id": "main", "model": primary_model}
    else:
        agent_payload.setdefault("id", "main")
        agent_payload.setdefault("model", primary_model)
    _write_json(agent_config_path, agent_payload)

    google_provider = (((config_payload.get("models") or {}) if isinstance(config_payload.get("models"), dict) else {}).get("providers") or {})
    google_provider = (google_provider if isinstance(google_provider, dict) else {}).get("google") or {}
    lmstudio_provider = (((config_payload.get("models") or {}) if isinstance(config_payload.get("models"), dict) else {}).get("providers") or {})
    lmstudio_provider = (lmstudio_provider if isinstance(lmstudio_provider, dict) else {}).get("lmstudio") or {}

    fallbacks = (
        (((((config_payload.get("agents") or {}) if isinstance(config_payload.get("agents"), dict) else {}).get("defaults") or {})
          if isinstance((((config_payload.get("agents") or {}) if isinstance(config_payload.get("agents"), dict) else {}).get("defaults")), dict)
          else {}).get("model") or {}
         ).get("fallbacks")
        or []
    )
    if not isinstance(fallbacks, list):
        fallbacks = []

    return {
        "ok": True,
        "bootstrapped_config": bootstrapped_config,
        "config_path": str(config_path),
        "models_path": str(models_path),
        "auth_profiles_path": str(auth_profiles_path),
        "agent_config_path": str(agent_config_path),
        "workspace_dir": str(workspace_dir),
        "onboard": onboard_report,
        "config": {
            "path": str(config_path),
            "google_base_url": str((google_provider if isinstance(google_provider, dict) else {}).get("baseUrl") or ""),
            "google_models_count": len((google_provider if isinstance(google_provider, dict) else {}).get("models") or []),
            "lmstudio_base_url": str((lmstudio_provider if isinstance(lmstudio_provider, dict) else {}).get("baseUrl") or ""),
            "lmstudio_models_count": len((lmstudio_provider if isinstance(lmstudio_provider, dict) else {}).get("models") or []),
            "chat_completions_enabled": bool(
                (((((config_payload.get("gateway") or {}) if isinstance(config_payload.get("gateway"), dict) else {}).get("http") or {})
                   if isinstance((((config_payload.get("gateway") or {}) if isinstance(config_payload.get("gateway"), dict) else {}).get("http")), dict)
                   else {}).get("endpoints") or {}
                 ).get("chatCompletions", {})
                .get("enabled", False)
            ),
            "primary_model": primary_model,
            "fallbacks": fallbacks,
        },
        "models": {
            "path": str(models_path),
            "created": models_created,
            "exists": models_path.exists(),
        },
        "auth_profiles": {
            "path": str(auth_profiles_path),
            "created": auth_created,
            "exists": auth_profiles_path.exists(),
        },
        "agent_config": {
            "path": str(agent_config_path),
            "created": agent_created,
            "updated": True,
            "exists": agent_config_path.exists(),
            "model": str(agent_payload.get("model") or ""),
        },
    }


def main() -> int:
    """CLI entrypoint launcher-friendly: печатает JSON и возвращает 0/1."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--openclaw-bin", default="openclaw")
    args = parser.parse_args()

    report = bootstrap_openclaw_account(args.openclaw_bin)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
