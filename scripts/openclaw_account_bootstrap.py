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

DEFAULT_LOCAL_MODEL = "lmstudio/local"
DEFAULT_CLOUD_MODEL = "google/gemini-2.5-flash"
DEFAULT_OPENAI_SAFE_MODEL = "openai/gpt-4o-mini"


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


def _agent_config_path() -> Path:
    return _openclaw_root() / "agents" / "main" / "agent" / "agent.json"


def _normalize_runtime_model_key(provider: str, model_id: str) -> str:
    """Возвращает канонический model key с provider-префиксом."""
    provider_raw = str(provider or "").strip().lower()
    model_raw = str(model_id or "").strip()
    if not model_raw:
        return ""
    if provider_raw and model_raw.startswith(f"{provider_raw}/"):
        return model_raw
    if provider_raw:
        return f"{provider_raw}/{model_raw}"
    return model_raw


def _pick_local_primary_model(runtime_models_payload: dict[str, Any]) -> str:
    """
    Выбирает локальную primary-модель для bootstrap skeleton.

    Почему это важно:
    - если `agents.defaults.model.primary` пустой, Python routing откатывается в
      исторический `config.MODEL`;
    - на новой учётке это даёт ложный cloud warmup вместо реального local-first.
    """
    providers = runtime_models_payload.get("providers") if isinstance(runtime_models_payload, dict) else {}
    lmstudio = providers.get("lmstudio") if isinstance(providers, dict) else {}
    models = lmstudio.get("models") if isinstance(lmstudio, dict) else []
    if isinstance(models, list):
        for item in models:
            if not isinstance(item, dict):
                continue
            normalized = _normalize_runtime_model_key("lmstudio", str(item.get("id") or ""))
            if normalized:
                return normalized
    return DEFAULT_LOCAL_MODEL


def _unique_non_empty(items: list[str]) -> list[str]:
    """Удаляет пустые значения и дубликаты, сохраняя порядок."""
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        raw = str(item or "").strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        out.append(raw)
    return out


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


def _read_json_or_default(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    """Читает JSON или возвращает переданный default без исключения наружу."""
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, ValueError, TypeError):
        return default


def _normalize_openclaw_config(path: Path, *, runtime_models_payload: dict[str, Any]) -> dict[str, Any]:
    """
    Доводит `openclaw.json` до минимально валидного provider-skeleton.

    Почему это нужно:
    - `openclaw onboard` создаёт config, но без project-specific runtime catalog;
    - наш repair-слой позже может заполнить `google.apiKey`, однако OpenClaw doctor
      ожидает, что у provider entry уже есть `baseUrl` и `models`;
    - лучше один раз создать пустой, но валидный каркас, чем потом ловить
      "Config invalid" на запуске gateway.
    """
    payload = _read_json_or_default(path, {})
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

    gateway = payload.setdefault("gateway", {})
    if not isinstance(gateway, dict):
        gateway = {}
        payload["gateway"] = gateway
    http_cfg = gateway.setdefault("http", {})
    if not isinstance(http_cfg, dict):
        http_cfg = {}
        gateway["http"] = http_cfg
    endpoints = http_cfg.setdefault("endpoints", {})
    if not isinstance(endpoints, dict):
        endpoints = {}
        http_cfg["endpoints"] = endpoints
    chat_completions = endpoints.setdefault("chatCompletions", {})
    if not isinstance(chat_completions, dict):
        chat_completions = {}
        endpoints["chatCompletions"] = chat_completions
    # Наш production-клиент и runtime warmup всё ещё ходят через
    # `/v1/chat/completions`, а в свежем OpenClaw этот endpoint может быть
    # выключен по умолчанию после onboard. Включаем его явно, чтобы новый
    # профиль не выглядел "живым" только по `/health`, но падал 404 на чате.
    chat_completions["enabled"] = True

    agents = payload.setdefault("agents", {})
    if not isinstance(agents, dict):
        agents = {}
        payload["agents"] = agents
    defaults = agents.setdefault("defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}
        agents["defaults"] = defaults

    model_cfg = defaults.get("model")
    if not isinstance(model_cfg, dict):
        model_cfg = {}
        defaults["model"] = model_cfg

    local_primary = _pick_local_primary_model(runtime_models_payload)
    primary_model = str(model_cfg.get("primary") or "").strip() or DEFAULT_CLOUD_MODEL
    model_cfg["primary"] = primary_model

    current_fallbacks = model_cfg.get("fallbacks")
    normalized_fallbacks = []
    if isinstance(current_fallbacks, list):
        normalized_fallbacks = [
            str(item or "").strip()
            for item in current_fallbacks
            if str(item or "").strip()
        ]
    if not normalized_fallbacks:
        if primary_model.startswith("lmstudio/"):
            normalized_fallbacks = [DEFAULT_CLOUD_MODEL, DEFAULT_OPENAI_SAFE_MODEL]
        else:
            normalized_fallbacks = [local_primary, DEFAULT_OPENAI_SAFE_MODEL]
    model_cfg["fallbacks"] = [
        item for item in _unique_non_empty(normalized_fallbacks) if item != primary_model
    ]

    subagents = defaults.get("subagents")
    if not isinstance(subagents, dict):
        subagents = {}
        defaults["subagents"] = subagents
    if not str(subagents.get("model") or "").strip():
        subagents["model"] = primary_model

    agents_list = agents.get("list")
    if not isinstance(agents_list, list):
        agents_list = []
        agents["list"] = agents_list
    main_agent = None
    for item in agents_list:
        if isinstance(item, dict) and str(item.get("id") or "").strip() == "main":
            main_agent = item
            break
    if main_agent is None:
        main_agent = {"id": "main", "workspace": str(_workspace_dir())}
        agents_list.append(main_agent)
    if not str(main_agent.get("model") or "").strip():
        main_agent["model"] = primary_model

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "path": str(path),
        "google_base_url": str(google.get("baseUrl") or ""),
        "google_models_count": len(google.get("models") or []),
        "lmstudio_base_url": str(lmstudio.get("baseUrl") or ""),
        "lmstudio_models_count": len(lmstudio.get("models") or []),
        "chat_completions_enabled": bool(chat_completions.get("enabled")),
        "primary_model": primary_model,
        "fallbacks": list(model_cfg.get("fallbacks") or []),
    }


def _write_json_if_missing(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Создаёт JSON-файл только если его ещё нет."""
    if path.exists():
        return {"path": str(path), "created": False, "exists": True}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"path": str(path), "created": True, "exists": True}


def _ensure_agent_config(path: Path, *, primary_model: str) -> dict[str, Any]:
    """
    Создаёт или нормализует `main agent.json`, чтобы runtime и UI видели один primary.
    """
    payload = _read_json_or_default(path, {})
    created = not path.exists()
    updated = False
    if not isinstance(payload, dict):
        payload = {}
        updated = True
    if str(payload.get("id") or "").strip() != "main":
        payload["id"] = "main"
        updated = True
    if str(payload.get("model") or "").strip() != primary_model:
        payload["model"] = primary_model
        updated = True

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "path": str(path),
        "created": created,
        "updated": updated,
        "exists": True,
        "model": primary_model,
    }


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
    agent_config_path = _agent_config_path()

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
    runtime_models_payload = _read_json_or_default(models_path, _empty_models_payload())
    config_report = _normalize_openclaw_config(config_path, runtime_models_payload=runtime_models_payload)
    agent_report = _ensure_agent_config(
        agent_config_path,
        primary_model=str(config_report.get("primary_model") or DEFAULT_LOCAL_MODEL),
    )

    return {
        "ok": True,
        "bootstrapped_config": onboard_report is not None,
        "config_path": str(config_path),
        "models_path": str(models_path),
        "auth_profiles_path": str(auth_profiles_path),
        "agent_config_path": str(agent_config_path),
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
        "agent_config": agent_report,
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
