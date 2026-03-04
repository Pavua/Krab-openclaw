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
   - cloud-first (облако как primary, локальная как fallback).
3) Скрипт работает с runtime-файлами `~/.openclaw`, делает бэкапы и
   возвращает детерминированный JSON для web-панели.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
    if raw in {"local-first", "cloud-first"}:
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
    parser = argparse.ArgumentParser(description="OpenClaw model autoswitch (local/cloud profiles)")
    parser.add_argument("--dry-run", action="store_true", help="Только диагностика, без записи файлов")
    parser.add_argument(
        "--profile",
        choices=("local-first", "cloud-first", "toggle", "current", "auto"),
        default="local-first",
        help="Профиль маршрутизации для OpenClaw main-agent.",
    )
    parser.add_argument("--openclaw-json", default=str(Path.home() / ".openclaw" / "openclaw.json"))
    parser.add_argument("--agent-json", default=str(Path.home() / ".openclaw" / "agents" / "main" / "agent" / "agent.json"))
    parser.add_argument("--state-json", default=str(Path.home() / ".openclaw" / "krab_autoswitch_state.json"))
    parser.add_argument("--local-model", default="", help="Принудительный local model key, например lmstudio/zai-org/glm-4.6v-flash")
    parser.add_argument("--cloud-model", default="", help="Принудительный cloud model key, например google/gemini-2.5-flash")
    args = parser.parse_args()

    openclaw_path = Path(args.openclaw_json).expanduser()
    agent_path = Path(args.agent_json).expanduser()
    state_path = Path(args.state_json).expanduser()

    openclaw_payload = _read_json(openclaw_path)
    agent_payload = _read_json(agent_path)
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

    local_model = _pick_local_model_key(openclaw_payload, override=args.local_model)
    cloud_model = _pick_cloud_model_key(openclaw_payload, override=args.cloud_model)
    current_profile = _detect_current_profile(openclaw_payload)
    effective_profile = _resolve_requested_profile(args.profile, current_profile)
    primary, fallbacks = _build_target_profile(
        openclaw_payload=openclaw_payload,
        profile=effective_profile,
        local_model=local_model,
        cloud_model=cloud_model,
    )

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

    status_value = "OK" if (changed or args.dry_run) else "ACTIVE"
    reason_value = "profile_applied" if changed else "already_in_desired_state"
    payload = {
        "ok": True,
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
            "openclaw_json": str(openclaw_path),
            "agent_json": str(agent_path),
            "state_json": str(state_path),
            "backup_openclaw_json": backup_openclaw,
            "backup_agent_json": backup_agent,
            "changed_fields": changed,
            "note": "После apply перезапусти OpenClaw gateway для гарантированного применения.",
        },
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
