# -*- coding: utf-8 -*-
"""
Read-only диагностика auth/runtime recovery для текущей macOS-учётки.

Что это:
- единый source-of-truth для безопасной проверки, какие provider-auth контуры
  реально готовы на этой учётке без запуска интерактивного login flow;
- используется и из CLI/.command-диагностики, и из owner web panel.

Зачем нужно:
- на shared repo с разными macOS-учётками runtime может быть рабочим через один
  auth path, но не иметь готового recovery для OAuth-провайдеров;
- helper-кнопки релогина уже есть, но без preflight-пояснения owner не видит,
  что именно отсутствует: auth-profile, внешний store или только TTY login.

Как связано с проектом:
- не мутирует `~/.openclaw/*` и не включает plugins;
- читает `openclaw models status --json`, `auth-profiles.json`, runtime config и
  при необходимости внешний store Gemini CLI;
- отдаёт компактный per-provider verdict для `openai-codex`,
  `google-gemini-cli`, `google-antigravity` и связанных helper'ов.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .runtime_policy import provider_runtime_policy

AUTH_PROFILES_PATH = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
RUNTIME_MODELS_PATH = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "models.json"
RUNTIME_CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"
GEMINI_STORE_PATH = Path.home() / ".gemini" / "oauth_creds.json"
GEMINI_API_KEY_ENV_KEYS = ("GOOGLE_API_KEY", "GEMINI_API_KEY")
CORE_OAUTH_PROVIDERS = {"openai-codex"}

PROVIDER_SPECS: dict[str, dict[str, Any]] = {
    "codex-cli": {
        "label": "Codex CLI",
        "expected_auth": "cli",
        "priority": 15,
        "legacy": False,
        "helper_name": "Login Codex CLI.command",
        "requires_plugin": False,
    },
    "google-gemini-cli": {
        "label": "Gemini CLI OAuth",
        "expected_auth": "oauth",
        "priority": 10,
        "legacy": False,
        "helper_name": "Login Gemini CLI OAuth.command",
        "requires_plugin": True,
    },
    "openai-codex": {
        "label": "OpenAI Codex",
        "expected_auth": "oauth",
        "priority": 20,
        "legacy": False,
        "helper_name": "Login OpenAI Codex OAuth.command",
        "requires_plugin": False,
    },
    "qwen-portal": {
        "label": "Qwen Portal",
        "expected_auth": "oauth",
        "priority": 30,
        "legacy": False,
        "helper_name": "Login Qwen Portal OAuth.command",
        "requires_plugin": True,
    },
    "google-antigravity": {
        "label": "Google OAuth (legacy)",
        "expected_auth": "oauth",
        "priority": 90,
        "legacy": True,
        "helper_name": "Login Google Antigravity OAuth.command",
        "requires_plugin": True,
    },
}


def provider_repair_helper_path(project_root: Path, provider_name: str) -> Path | None:
    """Возвращает canonical `.command` helper для recovery конкретного провайдера."""
    spec = PROVIDER_SPECS.get(str(provider_name or "").strip().lower())
    if not spec:
        return None
    helper_name = str(spec.get("helper_name") or "").strip()
    if not helper_name:
        return None
    return project_root / helper_name


def _load_json_file(path: Path, default: Any) -> Any:
    """Безопасно читает JSON-файл и при ошибке возвращает default."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _path_exists_safe(p: Path) -> bool:
    """Return False for both missing paths and permission-denied paths."""
    try:
        return p.exists()
    except PermissionError:
        return False


def _resolve_openclaw_bin() -> str:
    """Ищет бинарник `openclaw` без интерактивных предположений."""
    explicit = os.getenv("OPENCLAW_BIN", "").strip()
    if explicit and _path_exists_safe(Path(explicit)):
        return explicit
    default = Path("/opt/homebrew/bin/openclaw")
    if _path_exists_safe(default):
        return str(default)
    completed = subprocess.run(
        ["bash", "-lc", "command -v openclaw || true"],
        capture_output=True,
        text=True,
        check=False,
    )
    candidate = str(completed.stdout or "").strip()
    return candidate


def _run_json_command(cmd: Sequence[str], *, cwd: Path) -> dict[str, Any]:
    """Запускает read-only команду и возвращает JSON либо structured error."""
    try:
        completed = subprocess.run(
            list(cmd),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except Exception as exc:
        return {
            "ok": False,
            "command": list(cmd),
            "error": str(exc),
            "payload": {},
        }
    if completed.returncode != 0:
        return {
            "ok": False,
            "command": list(cmd),
            "returncode": int(completed.returncode),
            "stderr": str(completed.stderr or "").strip(),
            "payload": {},
        }
    try:
        payload = json.loads(str(completed.stdout or "{}"))
    except (TypeError, ValueError) as exc:
        return {
            "ok": False,
            "command": list(cmd),
            "error": f"json_decode_failed: {exc}",
            "payload": {},
        }
    return {
        "ok": True,
        "command": list(cmd),
        "payload": payload if isinstance(payload, dict) else {},
    }


def _iter_profile_entries(
    auth_profiles_payload: dict[str, Any],
) -> Iterable[tuple[str, dict[str, Any]]]:
    """Нормализует разные формы auth-profiles payload к списку профилей."""
    profiles = (
        auth_profiles_payload.get("profiles") if isinstance(auth_profiles_payload, dict) else None
    )
    if isinstance(profiles, dict):
        for key, payload in profiles.items():
            if isinstance(payload, dict):
                yield (str(key or "").strip(), payload)
        return

    # Хлебная крошка: на некоторых учётках auth-profiles уже был в старой форме,
    # где верхний уровень содержал provider-key без вложенного `profiles`.
    if isinstance(auth_profiles_payload, dict):
        for key, payload in auth_profiles_payload.items():
            if key in {"profiles", "usageStats"}:
                continue
            if isinstance(payload, dict) and (":" in str(key) or payload.get("provider")):
                yield (str(key or "").strip(), payload)


def _build_status_provider_maps(
    status_payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Собирает provider->meta maps из `openclaw models status --json`."""
    auth_root = status_payload.get("auth") if isinstance(status_payload, dict) else {}
    providers_root = auth_root.get("providers") if isinstance(auth_root, dict) else []
    oauth_root = auth_root.get("oauth") if isinstance(auth_root, dict) else {}
    oauth_providers = oauth_root.get("providers") if isinstance(oauth_root, dict) else []

    providers_map: dict[str, dict[str, Any]] = {}
    for item in providers_root if isinstance(providers_root, list) else []:
        if not isinstance(item, dict):
            continue
        provider_name = str(item.get("provider", "") or "").strip().lower()
        if provider_name:
            providers_map[provider_name] = item

    oauth_map: dict[str, dict[str, Any]] = {}
    for item in oauth_providers if isinstance(oauth_providers, list) else []:
        if not isinstance(item, dict):
            continue
        provider_name = str(item.get("provider", "") or "").strip().lower()
        if provider_name:
            oauth_map[provider_name] = item

    return providers_map, oauth_map


def _runtime_primary_auth_state(
    status_payload: dict[str, Any], providers_map: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Проверяет, жив ли текущий default runtime на этой учётке."""
    current_primary = str(
        status_payload.get("resolvedDefault") or status_payload.get("defaultModel") or ""
    ).strip()
    primary_provider = (
        current_primary.split("/", 1)[0].strip().lower() if "/" in current_primary else ""
    )
    provider_meta = providers_map.get(primary_provider, {})
    effective = provider_meta.get("effective") if isinstance(provider_meta, dict) else {}
    effective_kind = (
        str(effective.get("kind", "") or "").strip().lower() if isinstance(effective, dict) else ""
    )
    runtime_ok = bool(
        primary_provider and effective_kind in {"env", "oauth", "token", "models.json"}
    )
    if runtime_ok:
        label = f"Текущий runtime жив: primary `{current_primary}` подтверждён через `{effective_kind}`."
    elif current_primary:
        label = f"Primary `{current_primary}` не имеет подтверждённого auth path на этой учётке."
    else:
        label = "Primary runtime model не определён."
    return {
        "ok": runtime_ok,
        "primary_model": current_primary,
        "primary_provider": primary_provider,
        "effective_kind": effective_kind,
        "label": label,
    }


def _loaded_plugin_provider_ids(*, project_root: Path) -> set[str]:
    """Читает provider ids реально загруженных plugin'ов OpenClaw."""
    openclaw_bin = _resolve_openclaw_bin()
    if not openclaw_bin:
        return set()
    payload = _run_json_command([openclaw_bin, "plugins", "list", "--json"], cwd=project_root).get(
        "payload", {}
    )
    plugins = payload.get("plugins") if isinstance(payload, dict) else []
    loaded: set[str] = set()
    for item in plugins if isinstance(plugins, list) else []:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("enabled")):
            continue
        if str(item.get("status") or "").strip().lower() != "loaded":
            continue
        provider_ids = item.get("providerIds")
        if not isinstance(provider_ids, list):
            continue
        for provider_id in provider_ids:
            normalized = str(provider_id or "").strip().lower()
            if normalized:
                loaded.add(normalized)
    return loaded


def _gemini_cli_api_key_hint() -> dict[str, Any]:
    """Показывает, может ли локальный `gemini` CLI хотя бы стартовать через API key."""
    present_env_key = ""
    for env_key in GEMINI_API_KEY_ENV_KEYS:
        if str(os.getenv(env_key, "") or "").strip():
            present_env_key = env_key
            break
    gemini_bin = shutil.which("gemini") or ""
    return {
        "cli_binary_present": bool(gemini_bin),
        "cli_binary_path": str(gemini_bin),
        "api_key_env_present": bool(present_env_key),
        "api_key_env_name": present_env_key,
    }


def _codex_cli_hint() -> dict[str, Any]:
    """Проверяет, установлен ли локальный Codex CLI и подтверждён ли login."""
    codex_bin = shutil.which("codex") or ""
    if not codex_bin:
        return {
            "cli_binary_present": False,
            "cli_binary_path": "",
            "login_ready": False,
            "status_text": "",
        }

    try:
        completed = subprocess.run(
            [codex_bin, "login", "status"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except Exception as exc:
        return {
            "cli_binary_present": True,
            "cli_binary_path": str(codex_bin),
            "login_ready": False,
            "status_text": f"status_probe_failed:{exc}",
        }

    status_text = str(completed.stdout or completed.stderr or "").strip()
    return {
        "cli_binary_present": True,
        "cli_binary_path": str(codex_bin),
        "login_ready": completed.returncode == 0,
        "status_text": status_text,
    }


def _provider_usage_flags(
    provider_name: str,
    *,
    status_payload: dict[str, Any],
    runtime_models_payload: dict[str, Any],
    runtime_config_payload: dict[str, Any],
) -> dict[str, Any]:
    """Показывает, насколько provider реально участвует в текущем runtime."""
    normalized = str(provider_name or "").strip().lower()
    default_model = str(
        status_payload.get("resolvedDefault") or status_payload.get("defaultModel") or ""
    ).strip()
    fallbacks = [
        str(item or "").strip()
        for item in (status_payload.get("fallbacks") or [])
        if str(item or "").strip()
    ]
    allowed = [
        str(item or "").strip()
        for item in (status_payload.get("allowed") or [])
        if str(item or "").strip()
    ]

    runtime_providers = (
        runtime_models_payload.get("providers") if isinstance(runtime_models_payload, dict) else {}
    )
    runtime_provider_present = (
        isinstance(runtime_providers, dict) and normalized in runtime_providers
    )

    config_models = (
        runtime_config_payload.get("agents", {}).get("defaults", {}).get("model", {})
        if isinstance(runtime_config_payload, dict)
        else {}
    )
    config_primary = str(config_models.get("primary") or "").strip()
    config_fallbacks = [
        str(item or "").strip()
        for item in (config_models.get("fallbacks") or [])
        if str(item or "").strip()
    ]

    provider_allowed = [item for item in allowed if item.startswith(f"{normalized}/")]
    provider_fallbacks = [item for item in fallbacks if item.startswith(f"{normalized}/")]
    provider_config_fallbacks = [
        item for item in config_fallbacks if item.startswith(f"{normalized}/")
    ]

    role = "discoverable"
    if default_model.startswith(f"{normalized}/"):
        role = "primary"
    elif provider_fallbacks or provider_config_fallbacks:
        role = "fallback"
    elif provider_allowed:
        role = "allowed"
    elif runtime_provider_present:
        role = "runtime-configured"

    return {
        "role": role,
        "default_model": default_model,
        "fallback_models": provider_fallbacks or provider_config_fallbacks,
        "allowed_models": provider_allowed,
        "runtime_provider_present": runtime_provider_present,
        "config_primary": config_primary,
    }


def _provider_profile_counts(
    provider_name: str, auth_profiles_payload: dict[str, Any]
) -> dict[str, int]:
    """Считает локальные auth-profile и usage stats для провайдера."""
    normalized = str(provider_name or "").strip().lower()
    usage = (
        auth_profiles_payload.get("usageStats") if isinstance(auth_profiles_payload, dict) else {}
    )
    usage = usage if isinstance(usage, dict) else {}

    profile_count = 0
    usage_count = 0
    for key, payload in _iter_profile_entries(auth_profiles_payload):
        provider = str(payload.get("provider", "") or "").strip().lower()
        if provider == normalized or key.startswith(f"{normalized}:"):
            profile_count += 1
            if key in usage:
                usage_count += 1
    return {
        "profile_count": profile_count,
        "usage_count": usage_count,
    }


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Декодирует payload JWT без валидации подписи для read-only scope-диагностики."""
    raw = str(token or "").strip()
    if raw.count(".") < 2:
        return {}
    try:
        payload = raw.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
        parsed = json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_scope_values(raw: Any) -> set[str]:
    """Приводит scope-представление к множеству строковых значений."""
    if isinstance(raw, str):
        return {item for item in raw.replace(",", " ").split() if item}
    if isinstance(raw, (list, tuple, set)):
        return {str(item or "").strip() for item in raw if str(item or "").strip()}
    return set()


def provider_oauth_scope_truth(
    provider_name: str, auth_profiles_payload: dict[str, Any]
) -> dict[str, Any]:
    """Возвращает truthful срез scopes из локальных OAuth-профилей провайдера."""
    normalized = str(provider_name or "").strip().lower()
    scopes: set[str] = set()
    matched_profiles: list[str] = []
    for key, payload in _iter_profile_entries(auth_profiles_payload):
        provider = str(payload.get("provider", "") or "").strip().lower()
        if provider != normalized and not key.startswith(f"{normalized}:"):
            continue
        matched_profiles.append(str(key or "").strip())
        scopes.update(_normalize_scope_values(payload.get("scope")))
        scopes.update(_normalize_scope_values(payload.get("scopes")))
        for token_key in ("access", "accessToken", "token"):
            token_payload = _decode_jwt_payload(str(payload.get(token_key, "") or "").strip())
            scopes.update(_normalize_scope_values(token_payload.get("scope")))
            scopes.update(_normalize_scope_values(token_payload.get("scp")))

    ordered_scopes = sorted(scopes)
    return {
        "profiles": matched_profiles,
        "scopes": ordered_scopes,
        "scope_truth_available": bool(matched_profiles),
        "has_model_request": "model.request" in scopes,
    }


def _provider_detail(
    provider_name: str,
    *,
    state: str,
    role: str,
    helper_available: bool,
    helper_path: Path | None,
    external_store_present: bool,
    profile_count: int,
    effective_kind: str,
    oauth_status: str,
    provider_plugin_available: bool,
    gemini_cli_hint: dict[str, Any],
) -> tuple[str, str]:
    """Собирает short/full detail для UI и CLI."""
    label = PROVIDER_SPECS.get(provider_name, {}).get("label") or provider_name
    role_human = {
        "primary": "в текущем primary",
        "fallback": "в fallback-цепочке",
        "allowed": "в allowed/runtime inventory",
        "runtime-configured": "в runtime registry",
        "discoverable": "в recovery-контуре",
    }.get(role, "в recovery-контуре")

    if state == "ready":
        detail = (
            f"{label} уже подтверждён {role_human}: OpenClaw видит auth path "
            f"`{effective_kind or 'oauth'}` и локальных профилей {profile_count}."
        )
        return ("OAuth уже подтверждён на этой учётке.", detail)

    if state == "syncable":
        detail = (
            f"{label} пока не синхронизирован в OpenClaw auth store, но найден внешний store, "
            f"поэтому recovery можно сделать через существующий helper без ручного поиска команд."
        )
        return ("Есть внешний store, нужен sync/relogin.", detail)

    if state == "plugin_missing":
        if provider_name == "google-antigravity":
            detail = (
                f"{label}: штатный provider plugin сейчас не загружен в OpenClaw runtime, "
                "поэтому этот read-only OAuth snapshot не подтверждает legacy-контур. "
                "Если у проекта есть отдельный обходной путь для Antigravity, он должен "
                "проверяться отдельно и не считается эквивалентом штатного OAuth recovery."
            )
            return ("Штатный plugin не загружен; bypass отдельно.", detail)
        detail = (
            f"{label} не может пройти штатный relogin на этой учётке: соответствующий provider plugin "
            "сейчас не загружен в OpenClaw runtime."
        )
        return ("Provider plugin не загружен.", detail)

    helper_hint = (
        f" Жми helper `{helper_path.name}` из панели или Finder."
        if helper_available and helper_path
        else " Helper для one-click recovery на этой учётке не найден."
    )
    if provider_name == "google-gemini-cli" and not external_store_present:
        if gemini_cli_hint.get("cli_binary_present") and gemini_cli_hint.get("api_key_env_present"):
            env_name = str(gemini_cli_hint.get("api_key_env_name") or "API key").strip()
            detail = (
                f"{label} не подтверждён {role_human}: в OpenClaw OAuth status `{oauth_status or 'missing'}`, "
                "локальный `~/.gemini/oauth_creds.json` отсутствует. На этой учётке также виден отдельный "
                f"non-OAuth контур через `{env_name}`, но он не является подтверждением `Gemini CLI OAuth` "
                "и не должен смешиваться с отдельным bypass-путём проекта."
                f"{helper_hint}"
            )
            return ("Есть отдельный non-OAuth контур, но OAuth missing.", detail)
        detail = (
            f"{label} не подтверждён {role_human}: в OpenClaw OAuth status `{oauth_status or 'missing'}`, "
            "локальный `~/.gemini/oauth_creds.json` тоже отсутствует."
            f"{helper_hint}"
        )
        return ("Нет ни auth-profile, ни локального Gemini store.", detail)

    if provider_name == "codex-cli":
        detail = (
            f"{label} не подтверждён {role_human}: локальный Codex CLI либо не установлен, "
            f"либо ещё не прошёл `codex login` на этой macOS-учётке.{helper_hint}"
        )
        return ("Codex CLI login на этой учётке не подтверждён.", detail)

    if provider_name == "openai-codex":
        detail = (
            f"{label} не подтверждён {role_human}: OAuth-профиль для `openai-codex` отсутствует. "
            "Обычный OpenAI API key не заменяет этот OAuth-path."
            f"{helper_hint}"
        )
        return ("Codex OAuth на этой учётке не подтверждён.", detail)

    if provider_name == "google-antigravity":
        detail = (
            f"{label} не подтверждён на этой учётке. Legacy-контур оставлен в проекте, "
            "но здесь у него нет live OAuth-профиля."
            f"{helper_hint}"
        )
        return ("Legacy OAuth не подтверждён.", detail)

    detail = (
        f"{label} не подтверждён {role_human}: OpenClaw видит OAuth status `{oauth_status or 'missing'}` "
        f"и локальных профилей {profile_count}.{helper_hint}"
    )
    return ("Нужен relogin через helper.", detail)


def _provider_recovery_entry(
    provider_name: str,
    *,
    project_root: Path,
    status_payload: dict[str, Any],
    providers_map: dict[str, dict[str, Any]],
    oauth_map: dict[str, dict[str, Any]],
    auth_profiles_payload: dict[str, Any],
    runtime_models_payload: dict[str, Any],
    runtime_config_payload: dict[str, Any],
    loaded_plugin_providers: set[str],
) -> dict[str, Any]:
    """Строит recovery verdict для одного провайдера."""
    normalized = str(provider_name or "").strip().lower()
    spec = PROVIDER_SPECS.get(normalized, {})
    helper_path = provider_repair_helper_path(project_root, normalized)
    requires_plugin = bool(spec.get("requires_plugin"))
    provider_plugin_available = (
        (normalized in CORE_OAUTH_PROVIDERS)
        or (normalized in loaded_plugin_providers)
        or not requires_plugin
    )
    helper_available = bool(
        helper_path and _path_exists_safe(helper_path) and provider_plugin_available
    )
    local_counts = _provider_profile_counts(normalized, auth_profiles_payload)
    scope_truth = provider_oauth_scope_truth(normalized, auth_profiles_payload)
    usage = _provider_usage_flags(
        normalized,
        status_payload=status_payload,
        runtime_models_payload=runtime_models_payload,
        runtime_config_payload=runtime_config_payload,
    )
    provider_meta = providers_map.get(normalized, {})
    oauth_meta = oauth_map.get(normalized, {})
    effective = provider_meta.get("effective") if isinstance(provider_meta, dict) else {}
    effective_kind = (
        str(effective.get("kind", "") or "").strip().lower() if isinstance(effective, dict) else ""
    )
    effective_detail = (
        str(effective.get("detail", "") or "").strip() if isinstance(effective, dict) else ""
    )
    oauth_status = str(oauth_meta.get("status", "") or "").strip().lower()
    remaining_ms = oauth_meta.get("remainingMs")
    remaining_human = ""
    if isinstance(remaining_ms, int):
        total_minutes = max(int(remaining_ms), 0) // 60000
        hours, minutes = divmod(total_minutes, 60)
        remaining_human = f"{hours}ч {minutes}м" if hours else f"{minutes}м"
    external_store_present = normalized == "google-gemini-cli" and _path_exists_safe(
        GEMINI_STORE_PATH
    )
    gemini_cli_hint = _gemini_cli_api_key_hint() if normalized == "google-gemini-cli" else {}
    codex_cli_hint = _codex_cli_hint() if normalized == "codex-cli" else {}

    oauth_ready = oauth_status == "ok" or (
        local_counts["profile_count"] > 0 and effective_kind in {"oauth", "token"}
    )
    state = "missing"
    severity = "warn"
    state_label = "OAuth не подтверждён"
    if normalized == "codex-cli":
        if bool(codex_cli_hint.get("login_ready")):
            state = "ready"
            severity = "ok"
            state_label = "CLI OK"
        elif bool(codex_cli_hint.get("cli_binary_present")):
            state = "missing"
            severity = "bad" if usage["role"] in {"primary", "fallback"} else "warn"
            state_label = "CLI login missing"
        else:
            state = "missing"
            severity = "bad" if usage["role"] in {"primary", "fallback"} else "warn"
            state_label = "CLI missing"
    if normalized == "codex-cli" and state == "ready":
        pass
    elif oauth_ready:
        state = "ready"
        severity = "ok"
        state_label = "OAuth OK"
    elif normalized == "google-gemini-cli" and external_store_present:
        state = "syncable"
        severity = "warn"
        state_label = "Есть store, нужен sync"
    elif requires_plugin and not provider_plugin_available:
        state = "plugin_missing"
        severity = "warn"
        state_label = "Provider plugin не загружен"
    elif usage["role"] in {"primary", "fallback"}:
        severity = "bad"
        state_label = "Recovery блокирован"
    elif spec.get("legacy"):
        state_label = "Legacy пока не подтверждён"

    detail_short, detail = _provider_detail(
        normalized,
        state=state,
        role=str(usage.get("role") or "discoverable"),
        helper_available=helper_available,
        helper_path=helper_path,
        external_store_present=external_store_present,
        profile_count=int(local_counts["profile_count"]),
        effective_kind=effective_kind,
        oauth_status=oauth_status,
        provider_plugin_available=provider_plugin_available,
        gemini_cli_hint=gemini_cli_hint,
    )

    recommended_action_label = ""
    if helper_available:
        recommended_action_label = "Запустить one-click helper из панели"
    elif normalized == "codex-cli" and bool(codex_cli_hint.get("cli_binary_present")):
        recommended_action_label = "Запустить Codex CLI login helper"
    elif normalized == "google-gemini-cli" and external_store_present:
        recommended_action_label = "Синхронизировать Gemini store в OpenClaw"

    runtime_policy = provider_runtime_policy(
        normalized,
        readiness="ready"
        if severity == "ok"
        else ("attention" if severity == "warn" else "blocked"),
        auth_mode=str(spec.get("expected_auth") or "oauth"),
        oauth_status=oauth_status,
        helper_available=helper_available,
        legacy=bool(spec.get("legacy")),
        cli_login_ready=bool(codex_cli_hint.get("login_ready")),
    )

    return {
        "provider": normalized,
        "label": str(spec.get("label") or normalized),
        "expected_auth": str(spec.get("expected_auth") or "oauth"),
        "legacy": bool(spec.get("legacy")),
        "priority": int(spec.get("priority", 500) or 500),
        "usage_role": str(usage.get("role") or "discoverable"),
        "state": state,
        "severity": severity,
        "state_label": state_label,
        "detail_short": detail_short,
        "detail": detail,
        "effective_kind": effective_kind,
        "effective_detail": effective_detail,
        "oauth_status": oauth_status or "missing",
        "oauth_remaining_human": remaining_human,
        "observed_scopes": list(scope_truth.get("scopes") or []),
        "scope_truth_available": bool(scope_truth.get("scope_truth_available")),
        "has_model_request_scope": bool(scope_truth.get("has_model_request")),
        "profile_count": int(local_counts["profile_count"]),
        "usage_stats_count": int(local_counts["usage_count"]),
        "allowed_models": list(usage.get("allowed_models") or []),
        "fallback_models": list(usage.get("fallback_models") or []),
        "helper_available": helper_available,
        "helper_path": str(helper_path) if helper_path else "",
        "helper_tty_required": helper_available,
        "external_store_present": external_store_present,
        "external_store_path": str(GEMINI_STORE_PATH) if normalized == "google-gemini-cli" else "",
        "recommended_action_label": recommended_action_label,
        "provider_plugin_available": provider_plugin_available,
        "cli_binary_present": bool(gemini_cli_hint.get("cli_binary_present")),
        "cli_binary_path": str(gemini_cli_hint.get("cli_binary_path") or ""),
        "cli_api_key_present": bool(gemini_cli_hint.get("api_key_env_present")),
        "cli_api_key_env_name": str(gemini_cli_hint.get("api_key_env_name") or ""),
        "codex_cli_binary_present": bool(codex_cli_hint.get("cli_binary_present")),
        "codex_cli_binary_path": str(codex_cli_hint.get("cli_binary_path") or ""),
        "codex_cli_login_ready": bool(codex_cli_hint.get("login_ready")),
        "codex_cli_status_text": str(codex_cli_hint.get("status_text") or ""),
        **runtime_policy,
    }


def build_auth_recovery_readiness_snapshot(
    *,
    project_root: Path,
    status_payload: dict[str, Any] | None = None,
    auth_profiles_payload: dict[str, Any] | None = None,
    runtime_models_payload: dict[str, Any] | None = None,
    runtime_config_payload: dict[str, Any] | None = None,
    current_user: str | None = None,
    home_dir: Path | None = None,
) -> dict[str, Any]:
    """Строит полный auth recovery snapshot для текущей учётки."""
    resolved_project_root = Path(project_root).resolve()
    resolved_status = status_payload if isinstance(status_payload, dict) else {}
    if not resolved_status:
        openclaw_bin = _resolve_openclaw_bin()
        if openclaw_bin:
            resolved_status = _run_json_command(
                [openclaw_bin, "models", "status", "--json"], cwd=resolved_project_root
            ).get(
                "payload",
                {},
            )

    resolved_auth_profiles = (
        auth_profiles_payload
        if isinstance(auth_profiles_payload, dict)
        else _load_json_file(AUTH_PROFILES_PATH, {})
    )
    resolved_runtime_models = (
        runtime_models_payload
        if isinstance(runtime_models_payload, dict)
        else _load_json_file(
            RUNTIME_MODELS_PATH,
            {"providers": {}},
        )
    )
    resolved_runtime_config = (
        runtime_config_payload
        if isinstance(runtime_config_payload, dict)
        else _load_json_file(
            RUNTIME_CONFIG_PATH,
            {},
        )
    )

    providers_map, oauth_map = _build_status_provider_maps(resolved_status)
    runtime_primary = _runtime_primary_auth_state(resolved_status, providers_map)
    loaded_plugin_providers = _loaded_plugin_provider_ids(project_root=resolved_project_root)

    provider_names: set[str] = set(PROVIDER_SPECS.keys())
    provider_names.update(providers_map.keys())
    provider_names.update(oauth_map.keys())

    runtime_providers = (
        resolved_runtime_models.get("providers")
        if isinstance(resolved_runtime_models, dict)
        else {}
    )
    if isinstance(runtime_providers, dict):
        provider_names.update(
            str(name or "").strip().lower()
            for name in runtime_providers.keys()
            if str(name or "").strip()
        )

    for model_id in (
        [runtime_primary.get("primary_model", "")]
        + list(resolved_status.get("fallbacks") or [])
        + list(resolved_status.get("allowed") or [])
    ):
        normalized = (
            str(model_id.split("/", 1)[0] if "/" in str(model_id or "") else "").strip().lower()
        )
        if normalized:
            provider_names.add(normalized)

    providers: list[dict[str, Any]] = []
    for provider_name in provider_names:
        if provider_name in {"google", "openai", "lmstudio", "local", "github-copilot"}:
            continue
        providers.append(
            _provider_recovery_entry(
                provider_name,
                project_root=resolved_project_root,
                status_payload=resolved_status,
                providers_map=providers_map,
                oauth_map=oauth_map,
                auth_profiles_payload=resolved_auth_profiles,
                runtime_models_payload=resolved_runtime_models,
                runtime_config_payload=resolved_runtime_config,
                loaded_plugin_providers=loaded_plugin_providers,
            )
        )

    providers.sort(
        key=lambda item: (int(item.get("priority", 500) or 500), str(item.get("provider") or ""))
    )
    providers_by_name = {str(item.get("provider") or ""): item for item in providers}

    bad_count = sum(1 for item in providers if str(item.get("severity") or "") == "bad")
    warn_count = sum(1 for item in providers if str(item.get("severity") or "") == "warn")
    ready_count = sum(1 for item in providers if str(item.get("state") or "") == "ready")
    stage = "ready"
    if not runtime_primary["ok"]:
        stage = "blocked"
    elif bad_count or warn_count:
        stage = "attention"

    stage_label = {
        "ready": "Recovery-пути подтверждены",
        "attention": "Runtime жив, но recovery на этой учётке неполный",
        "blocked": "Текущий runtime auth не подтверждён",
    }[stage]

    next_step = ""
    for item in providers:
        if str(item.get("severity") or "") in {"bad", "warn"} and bool(
            item.get("helper_available")
        ):
            next_step = (
                f"Для `{item['label']}` можно сразу жать кнопку релогина в web panel: "
                f"`{item['recommended_action_label'] or 'Запустить helper'}`."
            )
            break
    if not next_step and not runtime_primary["ok"]:
        next_step = "Сначала нужно восстановить auth для текущего primary-провайдера."
    if not next_step:
        next_step = "Критичных recovery-хвостов для этой учётки сейчас не видно."

    resolved_home = (home_dir or Path.home()).expanduser().resolve()
    resolved_user = (current_user or os.getenv("USER") or resolved_home.name).strip()

    return {
        "ok": True,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_root": str(resolved_project_root),
        "current_account": {
            "user": resolved_user,
            "home_dir": str(resolved_home),
            "auth_profiles_path": str(AUTH_PROFILES_PATH),
            "gemini_store_path": str(GEMINI_STORE_PATH),
        },
        "runtime_primary": runtime_primary,
        "summary": {
            "recovery_stage": stage,
            "recovery_stage_label": stage_label,
            "runtime_ready": bool(runtime_primary["ok"]),
            "runtime_label": str(runtime_primary["label"]),
            "providers_total": len(providers),
            "providers_ready": ready_count,
            "providers_attention": warn_count,
            "providers_blocked": bad_count,
            "next_step": next_step,
            "panel_hint": "Кнопки быстрого релогина уже доступны на карточках провайдеров в owner panel.",
        },
        "providers": providers,
        "providers_by_name": providers_by_name,
    }
