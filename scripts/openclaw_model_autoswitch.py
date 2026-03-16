#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenClaw model autoswitch для Krab: быстрые профили local/cloud без ручного JSON-редактирования.

Зачем нужен:
1) После рефакторинга в проекте осталась заглушка autoswitch, из-за чего
   переключение режима выполнялось только вручную через несколько файлов.
2) Для каналов OpenClaw (Telegram Bot / WhatsApp / iMessage и др.) нужен
   единый и быстрый способ переключать модельный профиль:
   - local-first (локальная модель как primary),
   - cloud-first (облако как primary, локальная как fallback),
   - production-safe (только подтвержденные runtime cloud-кандидаты),
   - gpt54-canary (честная попытка поднять целевой GPT-5.4 primary).
3) Скрипт работает с runtime-файлами `~/.openclaw`, делает бэкапы и
   возвращает детерминированный JSON для web-панели.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


DEFAULT_TARGET_PRIMARY_MODEL = "openai-codex/gpt-5.4"
DEFAULT_SAFE_CLOUD_MODELS = (
    "google/gemini-2.5-flash",
)
RUNTIME_AUTH_SCOPE_MARKERS = (
    "missing scopes: model.request",
    "insufficient permissions for this operation",
)
RUNTIME_AUTH_PROVIDER_HINTS = {
    "lane=session:agent:main:openai:": "openai-codex",
}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _is_local_model(model_key: str) -> bool:
    raw = str(model_key or "").strip().lower()
    return raw.startswith("lmstudio/") or raw in {"local", "lmstudio/local"}


def _known_provider_names(openclaw_payload: dict[str, Any]) -> set[str]:
    providers = ((openclaw_payload.get("models") or {}).get("providers") or {})
    known = {"lmstudio", "google", "openai", "anthropic", "groq", "xai", "azure", "ollama"}
    if isinstance(providers, dict):
        known.update(str(key or "").strip().lower() for key in providers.keys() if str(key or "").strip())
    return known


def _normalize_model_key(provider: str, model_id: str, known_providers: set[str] | None = None) -> str:
    provider_raw = str(provider or "").strip().lower()
    model_raw = str(model_id or "").strip()
    if not model_raw:
        return ""
    if model_raw.startswith(f"{provider_raw}/"):
        return model_raw
    if "/" in model_raw:
        first = model_raw.split("/", 1)[0].strip().lower()
        if known_providers and first in known_providers:
            return model_raw
    if provider_raw:
        return f"{provider_raw}/{model_raw}"
    if "/" in model_raw:
        return model_raw
    return model_raw


def _pick_local_model_key(openclaw_payload: dict[str, Any], override: str = "") -> str:
    forced = str(override or "").strip()
    if forced:
        return forced

    known_providers = _known_provider_names(openclaw_payload)
    defaults = ((openclaw_payload.get("agents") or {}).get("defaults") or {})
    model_cfg = defaults.get("model") if isinstance(defaults, dict) else {}
    if isinstance(model_cfg, dict):
        primary = str(model_cfg.get("primary") or "").strip()
        if primary and _is_local_model(primary):
            return primary
        fallbacks = model_cfg.get("fallbacks")
        if isinstance(fallbacks, list):
            for candidate in fallbacks:
                raw = str(candidate or "").strip()
                if raw and _is_local_model(raw):
                    return raw

    providers = ((openclaw_payload.get("models") or {}).get("providers") or {})
    lmstudio = providers.get("lmstudio") if isinstance(providers, dict) else {}
    if isinstance(lmstudio, dict):
        models = lmstudio.get("models")
        if isinstance(models, list):
            for item in models:
                if isinstance(item, dict):
                    key = _normalize_model_key("lmstudio", str(item.get("id") or ""), known_providers)
                    if key:
                        return key
    return "lmstudio/local"


def _resolve_local_override(openclaw_payload: dict[str, Any], explicit_override: str = "") -> str:
    """
    Возвращает local override c приоритетом:
    1) явный `--local-model`,
    2) `LOCAL_PREFERRED_MODEL` из env/.env.
    """
    known_providers = _known_provider_names(openclaw_payload)
    explicit = str(explicit_override or "").strip()
    if explicit:
        return _normalize_model_key("lmstudio", explicit, known_providers)

    env_preferred = str(os.getenv("LOCAL_PREFERRED_MODEL", "") or "").strip()
    if not env_preferred or env_preferred.lower() in {"auto", "smallest"}:
        return ""
    return _normalize_model_key("lmstudio", env_preferred, known_providers)


def _pick_cloud_model_key(openclaw_payload: dict[str, Any], override: str = "") -> str:
    forced = str(override or "").strip()
    if forced:
        return forced

    defaults = ((openclaw_payload.get("agents") or {}).get("defaults") or {})
    model_cfg = defaults.get("model") if isinstance(defaults, dict) else {}
    if isinstance(model_cfg, dict):
        primary = str(model_cfg.get("primary") or "").strip()
        if primary and not _is_local_model(primary):
            return primary
        fallbacks = model_cfg.get("fallbacks")
        if isinstance(fallbacks, list):
            for candidate in fallbacks:
                raw = str(candidate or "").strip()
                if raw and not _is_local_model(raw):
                    return raw
    return "google/gemini-2.5-flash"


def _unique_non_empty(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        raw = str(item or "").strip()
        if not raw:
            continue
        if raw in seen:
            continue
        seen.add(raw)
        out.append(raw)
    return out


def _provider_from_model(model_key: str) -> str:
    raw = str(model_key or "").strip().lower()
    if "/" not in raw:
        return ""
    return raw.split("/", 1)[0].strip()


def _runtime_models_from_payload(
    runtime_models_payload: dict[str, Any],
    *,
    fallback_openclaw_payload: dict[str, Any],
) -> list[str]:
    """
    Собирает канонический registry моделей из `models.json`.

    Если отдельный runtime registry недоступен, пытаемся аккуратно деградировать
    до провайдеров, зашитых в `openclaw.json`, чтобы dry-run не падал в вакуум.
    """
    source = runtime_models_payload if runtime_models_payload else (fallback_openclaw_payload.get("models") or {})
    providers = source.get("providers") if isinstance(source, dict) else {}
    if not isinstance(providers, dict):
        return []

    known_providers = set(str(key or "").strip().lower() for key in providers.keys() if str(key or "").strip())
    out: list[str] = []
    for provider_name, provider_cfg in providers.items():
        models = provider_cfg.get("models") if isinstance(provider_cfg, dict) else None
        if not isinstance(models, list):
            continue
        for item in models:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            normalized = _normalize_model_key(str(provider_name or ""), model_id, known_providers)
            if normalized:
                out.append(normalized)
    return _unique_non_empty(out)


def _model_matches(model_key: str, candidate: str) -> bool:
    left = str(model_key or "").strip().lower()
    right = str(candidate or "").strip().lower()
    if not left or not right:
        return False
    if left == right:
        return True
    left_tail = left.split("/", 1)[1] if "/" in left else left
    right_tail = right.split("/", 1)[1] if "/" in right else right
    return left_tail == right_tail


def _registry_contains_model(model_key: str, registry_models: list[str]) -> bool:
    return any(_model_matches(model_key, item) for item in registry_models)


def _matching_auth_entries(auth_payload: dict[str, Any], provider: str) -> tuple[list[tuple[str, dict[str, Any]]], list[tuple[str, dict[str, Any]]]]:
    provider_raw = str(provider or "").strip().lower()
    profiles_raw = auth_payload.get("profiles") if isinstance(auth_payload.get("profiles"), dict) else {}
    usage_raw = auth_payload.get("usageStats") if isinstance(auth_payload.get("usageStats"), dict) else {}

    matching_profiles: list[tuple[str, dict[str, Any]]] = []
    for profile_key, profile_value in profiles_raw.items():
        if not isinstance(profile_value, dict):
            continue
        profile_provider = str(profile_value.get("provider") or "").strip().lower()
        if profile_provider == provider_raw or str(profile_key).strip().lower().startswith(f"{provider_raw}:"):
            matching_profiles.append((str(profile_key), profile_value))

    matching_usage: list[tuple[str, dict[str, Any]]] = []
    for usage_key, usage_value in usage_raw.items():
        if not isinstance(usage_value, dict):
            continue
        if str(usage_key).strip().lower().startswith(f"{provider_raw}:"):
            matching_usage.append((str(usage_key), usage_value))

    return matching_profiles, matching_usage


def _provider_health(auth_payload: dict[str, Any], provider: str) -> dict[str, Any]:
    """
    Нормализует auth/usage truth по провайдеру.

    `disabledReason` трактуем как runtime stop-сигнал, даже если таймер уже
    почти истек: для safe-профиля лучше не промотировать сомнительный провайдер.
    """
    profiles, usage_entries = _matching_auth_entries(auth_payload, provider)
    disabled_reason = ""
    disabled_until = 0
    failure_counts: dict[str, int] = {}
    error_count = 0
    healthy_profiles: list[str] = []
    expired_profiles: list[str] = []
    disabled_profiles: list[str] = []
    usage_by_key = {key: value for key, value in usage_entries}
    now_ms = time.time() * 1000.0

    for profile_key, profile_payload in profiles:
        expired = False
        if isinstance(profile_payload, dict):
            try:
                expires_at = float(profile_payload.get("expires", 0) or 0)
            except (TypeError, ValueError):
                expires_at = 0.0
            if expires_at > 0 and expires_at <= now_ms:
                expired = True
                expired_profiles.append(profile_key)
        usage = usage_by_key.get(profile_key)
        profile_disabled_reason = ""
        if isinstance(usage, dict):
            profile_disabled_reason = str(usage.get("disabledReason") or "").strip()
        if profile_disabled_reason:
            disabled_profiles.append(profile_key)
        if not expired and not profile_disabled_reason:
            healthy_profiles.append(profile_key)

    for _, usage in usage_entries:
        reason = str(usage.get("disabledReason") or "").strip()
        if reason and not disabled_reason:
            disabled_reason = reason
        disabled_until = max(disabled_until, int(usage.get("disabledUntil") or 0))
        error_count = max(error_count, int(usage.get("errorCount") or 0))
        raw_failures = usage.get("failureCounts")
        if isinstance(raw_failures, dict):
            for key, value in raw_failures.items():
                name = str(key or "").strip()
                if not name:
                    continue
                failure_counts[name] = failure_counts.get(name, 0) + int(value or 0)

    # Если живых auth-профилей не осталось, safe-профиль не должен считать
    # провайдера рабочим только потому, что disabledReason пустой.
    exhausted_auth = bool(profiles) and not healthy_profiles and bool(expired_profiles or disabled_profiles)
    disabled = bool(disabled_reason) or exhausted_auth
    if disabled and not disabled_reason and expired_profiles:
        disabled_reason = "auth_expired"
    return {
        "provider": str(provider or "").strip().lower(),
        "has_profile": bool(profiles),
        "profiles": [key for key, _ in profiles],
        "healthy_profiles": healthy_profiles,
        "disabled_profiles": disabled_profiles,
        "expired_profiles": expired_profiles,
        "disabled": disabled,
        "disabled_reason": disabled_reason or "",
        "disabled_until": disabled_until,
        "failure_counts": failure_counts,
        "error_count": error_count,
    }


def _broken_models_from_gateway_log(gateway_log_path: Path) -> list[str]:
    """
    Извлекает модели, которые runtime уже пометил как `not found`.
    """
    if not gateway_log_path.exists():
        return []
    try:
        lines = gateway_log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []

    out: list[str] = []
    for line in lines[-400:]:
        for candidate in re.findall(r'Model "([^"]+)" not found', line):
            out.append(str(candidate).strip())
        for candidate in re.findall(r"model `([^`]+)` does not exist", line, flags=re.IGNORECASE):
            out.append(str(candidate).strip())
    return _unique_non_empty(out)


def _is_model_broken(model_key: str, broken_models: list[str]) -> bool:
    return any(_model_matches(model_key, item) for item in broken_models)


def _runtime_auth_failed_providers(gateway_log_path: Path) -> dict[str, str]:
    """
    Возвращает провайдеры, которые уже доказанно падают в embedded runtime по auth/scopes.

    Почему это нужно:
    - `production-safe` раньше снимал только `model not found`;
    - но боевой primary может быть "формально существующим" и всё равно стабильно
      умирать на `401 Missing scopes: model.request`, тратя время на каждый failover.
    """
    if not gateway_log_path.exists():
        return {}
    try:
        lines = gateway_log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return {}

    disabled: dict[str, str] = {}
    for line in lines[-400:]:
        raw = str(line or "").strip()
        if not raw:
            continue
        lowered = raw.lower()
        if not all(marker in lowered for marker in RUNTIME_AUTH_SCOPE_MARKERS):
            continue
        for hint, provider in RUNTIME_AUTH_PROVIDER_HINTS.items():
            if hint in lowered:
                disabled[str(provider or "").strip()] = "runtime_missing_scope_model_request"
    return disabled


def _safe_cloud_candidates(
    *,
    openclaw_payload: dict[str, Any],
    registry_models: list[str],
    auth_payload: dict[str, Any],
    broken_models: list[str],
    runtime_auth_failed_providers: dict[str, str],
    target_primary: str,
) -> list[str]:
    defaults = ((openclaw_payload.get("agents") or {}).get("defaults") or {})
    model_cfg = defaults.get("model") if isinstance(defaults, dict) else {}
    current_primary = str((model_cfg or {}).get("primary") or "").strip() if isinstance(model_cfg, dict) else ""
    raw_fallbacks = model_cfg.get("fallbacks") if isinstance(model_cfg, dict) else []
    current_fallbacks = [
        str(item or "").strip()
        for item in raw_fallbacks
        if str(item or "").strip()
    ] if isinstance(raw_fallbacks, list) else []

    candidate_chain = _unique_non_empty(
        [
            target_primary,
            current_primary,
            *current_fallbacks,
            *DEFAULT_SAFE_CLOUD_MODELS,
        ]
    )

    safe_models: list[str] = []
    for candidate in candidate_chain:
        if not candidate or _is_local_model(candidate):
            continue
        if not _registry_contains_model(candidate, registry_models):
            continue
        provider = _provider_from_model(candidate)
        if provider and runtime_auth_failed_providers.get(provider):
            continue
        health = _provider_health(auth_payload, provider) if provider else {}
        if health.get("disabled"):
            continue
        if _is_model_broken(candidate, broken_models):
            continue
        safe_models.append(candidate)
    return _unique_non_empty(safe_models)


def _plan_special_profile(
    *,
    requested_profile: str,
    openclaw_payload: dict[str, Any],
    local_model: str,
    registry_models: list[str],
    auth_payload: dict[str, Any],
    broken_models: list[str],
    runtime_auth_failed_providers: dict[str, str],
    target_primary: str,
) -> dict[str, Any] | None:
    if requested_profile not in {"production-safe", "gpt54-canary"}:
        return None

    safe_cloud_models = _safe_cloud_candidates(
        openclaw_payload=openclaw_payload,
        registry_models=registry_models,
        auth_payload=auth_payload,
        broken_models=broken_models,
        runtime_auth_failed_providers=runtime_auth_failed_providers,
        target_primary=target_primary if requested_profile == "gpt54-canary" else "",
    )

    provider_health = {
        provider: _provider_health(auth_payload, provider)
        for provider in ("openai-codex", "google-gemini-cli", "google-antigravity", "google", "openai", "qwen-portal")
    }
    for provider, reason in runtime_auth_failed_providers.items():
        health = dict(provider_health.get(provider) or {})
        health["disabled"] = True
        health["disabled_reason"] = str(reason or "runtime_auth_failed")
        provider_health[provider] = health

    if requested_profile == "gpt54-canary":
        if not _registry_contains_model(target_primary, registry_models):
            return {
                "ok": False,
                "status": "BLOCKED",
                "reason": "target_model_not_in_runtime_registry",
                "primary": "",
                "fallbacks": [],
                "changed": {},
                "details": {
                    "target_primary_candidate": target_primary,
                    "registry_models": registry_models,
                    "provider_health": provider_health,
                    "broken_models": broken_models,
                },
            }
        target_provider = _provider_from_model(target_primary)
        target_provider_health = provider_health.get(target_provider, {})
        if target_provider_health.get("disabled"):
            return {
                "ok": False,
                "status": "BLOCKED",
                "reason": "target_provider_disabled",
                "primary": "",
                "fallbacks": [],
                "changed": {},
                "details": {
                    "target_primary_candidate": target_primary,
                    "provider_health": provider_health,
                    "broken_models": broken_models,
                },
            }
        if _is_model_broken(target_primary, broken_models):
            return {
                "ok": False,
                "status": "BLOCKED",
                "reason": "target_model_marked_broken",
                "primary": "",
                "fallbacks": [],
                "changed": {},
                "details": {
                    "target_primary_candidate": target_primary,
                    "provider_health": provider_health,
                    "broken_models": broken_models,
                },
            }

        remaining_cloud = [item for item in safe_cloud_models if not _model_matches(item, target_primary)]
        return {
            "ok": True,
            "status": "READY",
            "reason": "canary_target_ready",
            "primary": target_primary,
            "fallbacks": _unique_non_empty([*remaining_cloud, local_model]),
            "changed": {},
            "details": {
                "target_primary_candidate": target_primary,
                "provider_health": provider_health,
                "broken_models": broken_models,
                "registry_models": registry_models,
            },
        }

    if not safe_cloud_models:
        return {
            "ok": False,
            "status": "BLOCKED",
            "reason": "no_safe_cloud_candidates",
            "primary": "",
            "fallbacks": [],
            "changed": {},
            "details": {
                "target_primary_candidate": target_primary,
                "provider_health": provider_health,
                "broken_models": broken_models,
                "registry_models": registry_models,
            },
        }

    return {
        "ok": True,
        "status": "READY",
        "reason": "safe_cloud_chain_built",
        "primary": safe_cloud_models[0],
        "fallbacks": _unique_non_empty([*safe_cloud_models[1:], local_model]),
        "changed": {},
        "details": {
            "target_primary_candidate": target_primary,
            "provider_health": provider_health,
            "broken_models": broken_models,
            "registry_models": registry_models,
        },
    }


def _build_target_profile(
    *,
    openclaw_payload: dict[str, Any],
    profile: str,
    local_model: str,
    cloud_model: str,
) -> tuple[str, list[str]]:
    defaults = ((openclaw_payload.get("agents") or {}).get("defaults") or {})
    model_cfg = defaults.get("model") if isinstance(defaults, dict) else {}
    current_fallbacks_raw = model_cfg.get("fallbacks") if isinstance(model_cfg, dict) else []
    current_fallbacks = [
        str(item or "").strip()
        for item in current_fallbacks_raw
        if str(item or "").strip()
    ] if isinstance(current_fallbacks_raw, list) else []

    if profile == "local-first":
        primary = local_model
        non_local_existing = [item for item in current_fallbacks if not _is_local_model(item)]
        # Сохраняем cloud-модель первой в fallback-цепочке, чтобы toggle был детерминирован.
        fallbacks = _unique_non_empty([cloud_model, *non_local_existing, "openai/gpt-4o-mini"])
        return primary, [item for item in fallbacks if item != primary]

    # cloud-first
    primary = cloud_model
    fallbacks = _unique_non_empty([local_model, *current_fallbacks, "openai/gpt-4o-mini"])
    return primary, [item for item in fallbacks if item != primary]


def _detect_current_profile(openclaw_payload: dict[str, Any]) -> str:
    defaults = ((openclaw_payload.get("agents") or {}).get("defaults") or {})
    model_cfg = defaults.get("model") if isinstance(defaults, dict) else {}
    primary = str((model_cfg or {}).get("primary") or "").strip() if isinstance(model_cfg, dict) else ""
    if not primary:
        return "unknown"
    return "local-first" if _is_local_model(primary) else "cloud-first"


def _resolve_requested_profile(requested: str, current: str) -> str:
    raw = str(requested or "").strip().lower()
    if raw in {"local-first", "cloud-first", "production-safe", "gpt54-canary"}:
        return raw
    if raw == "toggle":
        return "cloud-first" if current == "local-first" else "local-first"
    if raw in {"current", "auto", ""}:
        if current in {"local-first", "cloud-first"}:
            return current
        return "local-first"
    return "local-first"


def _apply_agent_targets(
    *,
    openclaw_payload: dict[str, Any],
    agent_payload: dict[str, Any],
    primary: str,
    fallbacks: list[str],
) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    agents = openclaw_payload.setdefault("agents", {})
    if not isinstance(agents, dict):
        agents = {}
        openclaw_payload["agents"] = agents

    defaults = agents.setdefault("defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}
        agents["defaults"] = defaults

    model_cfg = defaults.setdefault("model", {})
    if not isinstance(model_cfg, dict):
        model_cfg = {}
        defaults["model"] = model_cfg

    prev_primary = str(model_cfg.get("primary") or "")
    if prev_primary != primary:
        model_cfg["primary"] = primary
        changed["agents.defaults.model.primary"] = {"from": prev_primary, "to": primary}

    prev_fallbacks = model_cfg.get("fallbacks")
    if prev_fallbacks != fallbacks:
        model_cfg["fallbacks"] = fallbacks
        changed["agents.defaults.model.fallbacks"] = {"from": prev_fallbacks, "to": fallbacks}

    subagents = defaults.setdefault("subagents", {})
    if not isinstance(subagents, dict):
        subagents = {}
        defaults["subagents"] = subagents
    prev_sub_model = str(subagents.get("model") or "")
    if prev_sub_model != primary:
        subagents["model"] = primary
        changed["agents.defaults.subagents.model"] = {"from": prev_sub_model, "to": primary}

    agents_list = agents.get("list")
    if isinstance(agents_list, list):
        for item in agents_list:
            if not isinstance(item, dict):
                continue
            if str(item.get("id") or "") != "main":
                continue
            prev_item_model = str(item.get("model") or "")
            if prev_item_model != primary:
                item["model"] = primary
                changed["agents.list[main].model"] = {"from": prev_item_model, "to": primary}
            break

    prev_agent_model = str(agent_payload.get("model") or "")
    if prev_agent_model != primary:
        agent_payload["model"] = primary
        changed["agents.main.agent.json.model"] = {"from": prev_agent_model, "to": primary}

    return changed


def _backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_suffix(path.suffix + f".bak_autoswitch_{stamp}")
    backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup_path


def _read_state(path: Path) -> dict[str, Any]:
    raw = _read_json(path)
    return raw if isinstance(raw, dict) else {}


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    _write_json(path, payload)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="OpenClaw model autoswitch (local/cloud profiles)")
    parser.add_argument("--dry-run", action="store_true", help="Только диагностика, без записи файлов")
    parser.add_argument(
        "--profile",
        choices=("local-first", "cloud-first", "production-safe", "gpt54-canary", "toggle", "current", "auto"),
        default="local-first",
        help="Профиль маршрутизации для OpenClaw main-agent.",
    )
    parser.add_argument("--openclaw-json", default=str(Path.home() / ".openclaw" / "openclaw.json"))
    parser.add_argument("--agent-json", default=str(Path.home() / ".openclaw" / "agents" / "main" / "agent" / "agent.json"))
    parser.add_argument("--state-json", default=str(Path.home() / ".openclaw" / "krab_autoswitch_state.json"))
    parser.add_argument("--models-json", default=str(Path.home() / ".openclaw" / "agents" / "main" / "agent" / "models.json"))
    parser.add_argument("--auth-profiles-json", default=str(Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"))
    parser.add_argument("--gateway-log", default=str(Path.home() / ".openclaw" / "logs" / "gateway.err.log"))
    parser.add_argument("--local-model", default="", help="Принудительный local model key, например lmstudio/zai-org/glm-4.6v-flash")
    parser.add_argument("--cloud-model", default="", help="Принудительный cloud model key, например google/gemini-2.5-flash")
    args = parser.parse_args()

    openclaw_path = Path(args.openclaw_json).expanduser()
    agent_path = Path(args.agent_json).expanduser()
    state_path = Path(args.state_json).expanduser()
    models_path = Path(args.models_json).expanduser()
    auth_profiles_path = Path(args.auth_profiles_json).expanduser()
    gateway_log_path = Path(args.gateway_log).expanduser()

    openclaw_payload = _read_json(openclaw_path)
    agent_payload = _read_json(agent_path)
    runtime_models_payload = _read_json(models_path)
    auth_payload = _read_json(auth_profiles_path)
    if not openclaw_payload:
        print(
            json.dumps(
                {
                    "ok": False,
                    "mode": "dry-run" if args.dry_run else "apply",
                    "status": "error",
                    "reason": "openclaw_json_missing_or_invalid",
                    "timestamp": int(time.time()),
                    "details": {"openclaw_json": str(openclaw_path)},
                },
                ensure_ascii=False,
            )
        )
        return 1

    local_override = _resolve_local_override(openclaw_payload, explicit_override=args.local_model)
    local_model = _pick_local_model_key(openclaw_payload, override=local_override)
    cloud_model = _pick_cloud_model_key(openclaw_payload, override=args.cloud_model)
    target_primary = str(os.getenv("OPENCLAW_TARGET_PRIMARY_MODEL", DEFAULT_TARGET_PRIMARY_MODEL) or DEFAULT_TARGET_PRIMARY_MODEL).strip()
    registry_models = _runtime_models_from_payload(
        runtime_models_payload,
        fallback_openclaw_payload=openclaw_payload,
    )
    broken_models = _broken_models_from_gateway_log(gateway_log_path)
    runtime_auth_failed_providers = _runtime_auth_failed_providers(gateway_log_path)
    current_profile = _detect_current_profile(openclaw_payload)
    effective_profile = _resolve_requested_profile(args.profile, current_profile)
    special_plan = _plan_special_profile(
        requested_profile=effective_profile,
        openclaw_payload=openclaw_payload,
        local_model=local_model,
        registry_models=registry_models,
        auth_payload=auth_payload if isinstance(auth_payload, dict) else {},
        broken_models=broken_models,
        runtime_auth_failed_providers=runtime_auth_failed_providers,
        target_primary=target_primary,
    )
    if special_plan is not None:
        primary = str(special_plan.get("primary") or "").strip()
        fallbacks_raw = special_plan.get("fallbacks")
        fallbacks = [
            str(item or "").strip()
            for item in fallbacks_raw
            if str(item or "").strip()
        ] if isinstance(fallbacks_raw, list) else []
    else:
        primary, fallbacks = _build_target_profile(
            openclaw_payload=openclaw_payload,
            profile=effective_profile,
            local_model=local_model,
            cloud_model=cloud_model,
        )

    changed: dict[str, Any] = {}
    if primary:
        changed = _apply_agent_targets(
            openclaw_payload=openclaw_payload,
            agent_payload=agent_payload if isinstance(agent_payload, dict) else {},
            primary=primary,
            fallbacks=fallbacks,
        )

    backup_openclaw = ""
    backup_agent = ""
    last_switch_iso = ""
    state_before = _read_state(state_path)
    if isinstance(state_before, dict):
        last_switch_iso = str(state_before.get("last_switch") or "").strip()

    if changed and not args.dry_run:
        if openclaw_path.exists():
            backup_openclaw = str(_backup(openclaw_path))
        _write_json(openclaw_path, openclaw_payload)
        if agent_path.exists():
            backup_agent = str(_backup(agent_path))
        _write_json(agent_path, agent_payload if isinstance(agent_payload, dict) else {"id": "main", "model": primary})
        last_switch_iso = _utc_now_iso()
        _write_state(
            state_path,
            {
                "last_switch": last_switch_iso,
                "last_profile": effective_profile,
                "reason": "profile_applied",
                "updated_at_epoch": int(time.time()),
            },
        )

    if special_plan is not None and str(special_plan.get("status") or "").upper() == "BLOCKED":
        status_value = "BLOCKED"
        reason_value = str(special_plan.get("reason") or "profile_blocked").strip() or "profile_blocked"
        ok_value = False
    else:
        status_value = "OK" if (changed or args.dry_run) else "ACTIVE"
        reason_value = "profile_applied" if changed else "already_in_desired_state"
        if special_plan is not None and str(special_plan.get("reason") or "").strip():
            reason_value = str(special_plan.get("reason") or "").strip()
        ok_value = True
    payload = {
        "ok": ok_value,
        "mode": "dry-run" if args.dry_run else "apply",
        "status": status_value,
        "changed": bool(changed),
        "reason": reason_value,
        "last_switch": last_switch_iso or "-",
        "timestamp": int(time.time()),
        "details": {
            "requested_profile": args.profile,
            "effective_profile": effective_profile,
            "current_profile_before": current_profile,
            "primary_model": primary,
            "fallbacks": fallbacks,
            "local_model_detected": local_model,
            "cloud_model_detected": cloud_model,
            "target_primary_candidate": target_primary,
            "runtime_registry_source": str(models_path),
            "auth_profiles_source": str(auth_profiles_path),
            "gateway_log_source": str(gateway_log_path),
            "runtime_registry_models": registry_models,
            "runtime_registry_count": len(registry_models),
            "broken_models": broken_models,
            "runtime_auth_failed_providers": runtime_auth_failed_providers,
            "openclaw_json": str(openclaw_path),
            "agent_json": str(agent_path),
            "state_json": str(state_path),
            "backup_openclaw_json": backup_openclaw,
            "backup_agent_json": backup_agent,
            "changed_fields": changed,
            "special_profile": special_plan.get("details") if isinstance(special_plan, dict) else {},
            "note": "После apply перезапусти OpenClaw gateway для гарантированного применения.",
        },
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
