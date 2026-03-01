#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
One-click восстановление runtime-конфига OpenClaw для стабильной работы каналов.

Что исправляет:
1) Синхронизирует Gemini API key (AI Studio, формат `AIza...`) в:
   - ~/.openclaw/agents/main/agent/models.json
   - ~/.openclaw/openclaw.json
2) Убирает залипшие channel session overrides вида `lmstudio/local`,
   из-за которых каналы Telegram/iMessage/WhatsApp могут падать с
   `400 No models loaded`.
3) По желанию переводит DM policy каналов в `allowlist`, чтобы убрать
   навязчивые pairing-сообщения внешним контактам.
4) Нормализует allowlist-файлы (например, удаляет слишком широкие маски).

Почему это отдельный скрипт:
- Runtime OpenClaw хранится вне репозитория (~/.openclaw), поэтому нужен
  явный и повторяемый "ремонт" без ручного редактирования JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


LOCAL_MARKERS = {"local", "lmstudio", "lmstudio/local", "google/local"}
DEFAULT_CHANNELS = ("telegram", "imessage", "whatsapp", "signal")


def mask_secret(secret: str) -> str:
    """Маскирует секрет для безопасного вывода в лог/JSON."""
    value = str(secret or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def is_ai_studio_key(value: str) -> bool:
    """True, если ключ похож на Gemini AI Studio API key."""
    raw = str(value or "").strip()
    return raw.startswith("AIza") and len(raw) >= 30


def choose_target_key(*, free_key: str, paid_key: str, tier: str) -> tuple[str, str]:
    """
    Выбирает целевой ключ и tier.

    Логика:
    - tier=free/paid: строгий выбор.
    - tier=auto: free, иначе paid.
    """
    free = str(free_key or "").strip()
    paid = str(paid_key or "").strip()
    requested = str(tier or "auto").strip().lower()

    if requested == "free":
        return ("free", free) if is_ai_studio_key(free) else ("", "")
    if requested == "paid":
        return ("paid", paid) if is_ai_studio_key(paid) else ("", "")

    if is_ai_studio_key(free):
        return "free", free
    if is_ai_studio_key(paid):
        return "paid", paid
    return "", ""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any], *, backup: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_suffix(path.suffix + f".bak_{stamp}")
        shutil.copy2(path, backup_path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sync_models_json(path: Path, target_key: str) -> dict[str, Any]:
    """Синхронизирует providers.google.apiKey в models.json."""
    payload = _read_json(path)
    providers = payload.setdefault("providers", {})
    google = providers.setdefault("google", {})
    prev = str(google.get("apiKey", "") or "")
    google["apiKey"] = target_key
    google["auth"] = "api-key"
    google["api"] = "google-generative-ai"
    _write_json(path, payload)
    return {
        "path": str(path),
        "changed": prev != target_key,
        "prev_key_masked": mask_secret(prev),
        "new_key_masked": mask_secret(target_key),
    }


def sync_openclaw_json(path: Path, target_key: str) -> dict[str, Any]:
    """Синхронизирует providers.google.apiKey в корневом openclaw.json."""
    payload = _read_json(path)
    models = payload.setdefault("models", {})
    providers = models.setdefault("providers", {})
    google = providers.setdefault("google", {})
    prev = str(google.get("apiKey", "") or "")
    google["apiKey"] = target_key
    google["auth"] = "api-key"
    google["api"] = "google-generative-ai"
    _write_json(path, payload)
    return {
        "path": str(path),
        "changed": prev != target_key,
        "prev_key_masked": mask_secret(prev),
        "new_key_masked": mask_secret(target_key),
    }


def _default_model_from_openclaw(openclaw_payload: dict[str, Any]) -> tuple[str, str]:
    """
    Возвращает (provider, model) дефолтного primary-маршрута.
    Если не удалось распарсить — безопасный fallback.
    """
    primary = (
        str(
            (
                openclaw_payload.get("agents", {})
                .get("defaults", {})
                .get("model", {})
                .get("primary", "")
            )
            or ""
        )
        .strip()
        .lower()
    )
    if "/" in primary:
        provider = primary.split("/", 1)[0]
        return provider, primary
    return "google", "google/gemini-2.5-flash"


def repair_sessions(
    sessions_path: Path,
    *,
    channels: tuple[str, ...],
    default_provider: str,
    default_model: str,
) -> dict[str, Any]:
    """
    Убирает опасные local-overrides из channel sessions.
    Также снимает залипший generic `local` (без explicit модели), чтобы
    runtime мог вернуться к primary/fallback цепочке.
    """
    payload = _read_json(sessions_path)
    if not isinstance(payload, dict):
        return {"path": str(sessions_path), "changed": False, "fixed_entries": 0, "reason": "not_dict"}

    fixed_entries = 0
    cleared_overrides = 0
    replaced_generic_local = 0

    for key, meta in payload.items():
        if not isinstance(meta, dict):
            continue
        if not key.startswith("agent:main:"):
            continue
        matched_channel = None
        for channel in channels:
            token = f"agent:main:{channel}:"
            if key.startswith(token):
                matched_channel = channel
                break
        if not matched_channel:
            continue

        changed = False

        model_override = str(meta.get("modelOverride", "") or "").strip().lower()
        provider_override = str(meta.get("providerOverride", "") or "").strip().lower()
        if model_override in LOCAL_MARKERS or provider_override in {"lmstudio", "local"}:
            # Полностью убираем override-фиксацию, чтобы вернуть авто-роутинг.
            if "modelOverride" in meta:
                meta.pop("modelOverride", None)
                changed = True
            if "providerOverride" in meta:
                meta.pop("providerOverride", None)
                changed = True
            cleared_overrides += 1

        model = str(meta.get("model", "") or "").strip().lower()
        provider = str(meta.get("modelProvider", "") or "").strip().lower()
        if model in {"local", "lmstudio/local", "google/local"} and provider in {"", "lmstudio", "local"}:
            # Generic local без explicit model-id часто приводит к `No models loaded`.
            meta["modelProvider"] = default_provider
            meta["model"] = default_model
            replaced_generic_local += 1
            changed = True

        if changed:
            fixed_entries += 1

    if fixed_entries > 0:
        _write_json(sessions_path, payload)

    return {
        "path": str(sessions_path),
        "changed": fixed_entries > 0,
        "fixed_entries": fixed_entries,
        "cleared_overrides": cleared_overrides,
        "replaced_generic_local": replaced_generic_local,
    }


def normalize_allowlist(path: Path) -> dict[str, Any]:
    """
    Нормализует allowlist:
    - удаляет одиночные широкие wildcard-псевдоэлементы (`+`, `*`);
    - дубли;
    - пустые значения.
    """
    payload = _read_json(path)
    if not isinstance(payload, list):
        return {"path": str(path), "changed": False, "items": 0}

    before = [str(item or "").strip() for item in payload]
    after: list[str] = []
    seen: set[str] = set()
    for item in before:
        if not item:
            continue
        if item in {"+", "*"}:
            continue
        if item in seen:
            continue
        seen.add(item)
        after.append(item)

    changed = after != before
    if changed:
        _write_json(path, after)

    return {
        "path": str(path),
        "changed": changed,
        "items_before": len(before),
        "items_after": len(after),
    }


def apply_dm_policy(openclaw_path: Path, channels: tuple[str, ...], policy: str) -> dict[str, Any]:
    """
    Переводит dmPolicy каналов в выбранный режим (allowlist/open/pairing).
    Используется для отключения pairing-шума или открытия входящих DMs.
    """
    payload = _read_json(openclaw_path)
    channel_cfg = payload.setdefault("channels", {})
    changed_channels: list[str] = []
    allow_from_fixed: dict[str, str] = {}
    creds_root = openclaw_path.parent / "credentials"
    allow_from_changes = 0

    for channel in channels:
        cfg = channel_cfg.get(channel)
        if not isinstance(cfg, dict):
            continue
        current = str(cfg.get("dmPolicy", "") or "").strip().lower()
        if "dmPolicy" not in cfg:
            continue
        if current != policy:
            cfg["dmPolicy"] = policy
            changed_channels.append(channel)

        allow_from = cfg.get("allowFrom")
        allow_from_list = allow_from if isinstance(allow_from, list) else []

        if policy == "open":
            # Для open OpenClaw требует wildcard в allowFrom.
            if "*" not in allow_from_list:
                cfg["allowFrom"] = ["*"]
                allow_from_fixed[channel] = "set_wildcard"
                allow_from_changes += 1
        elif policy == "allowlist":
            # Для allowlist нужен непустой список.
            if not allow_from_list:
                channel_allowlist_file = creds_root / f"{channel}-allowFrom.json"
                loaded: list[str] = []
                if channel_allowlist_file.exists():
                    try:
                        raw = json.loads(channel_allowlist_file.read_text(encoding="utf-8"))
                        if isinstance(raw, list):
                            loaded = [str(x).strip() for x in raw if str(x).strip()]
                    except (OSError, ValueError):
                        loaded = []
                if loaded:
                    cfg["allowFrom"] = loaded
                    allow_from_fixed[channel] = "loaded_from_credentials"
                    allow_from_changes += 1

    if changed_channels or allow_from_changes > 0:
        _write_json(openclaw_path, payload)

    return {
        "path": str(openclaw_path),
        "changed": bool(changed_channels or allow_from_changes > 0),
        "channels": changed_channels,
        "allow_from_fixed": allow_from_fixed,
        "allow_from_changes": allow_from_changes,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Восстановление runtime-конфига OpenClaw/каналов после рефакторинга."
    )
    parser.add_argument(
        "--tier",
        choices=("auto", "free", "paid"),
        default="auto",
        help="Какой Gemini key синхронизировать в runtime.",
    )
    parser.add_argument(
        "--channels",
        default="telegram,imessage,whatsapp,signal",
        help="Список каналов для очистки сессий (через запятую).",
    )
    parser.add_argument(
        "--dm-policy",
        choices=("keep", "pairing", "allowlist", "open"),
        default="open",
        help="Режим dmPolicy для каналов. keep = не менять.",
    )
    parser.add_argument(
        "--skip-allowlist-normalize",
        action="store_true",
        help="Не нормализовать файлы allowlist.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv()

    openclaw_root = Path.home() / ".openclaw"
    models_path = openclaw_root / "agents" / "main" / "agent" / "models.json"
    openclaw_path = openclaw_root / "openclaw.json"
    sessions_path = openclaw_root / "agents" / "main" / "sessions" / "sessions.json"

    free_key = str(os.getenv("GEMINI_API_KEY_FREE", "") or "").strip()
    paid_key = str(os.getenv("GEMINI_API_KEY_PAID", "") or "").strip()

    selected_tier, target_key = choose_target_key(
        free_key=free_key,
        paid_key=paid_key,
        tier=args.tier,
    )
    if not target_key:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "no_valid_aistudio_key",
                    "selected_tier": args.tier,
                    "free_key_masked": mask_secret(free_key),
                    "paid_key_masked": mask_secret(paid_key),
                    "hint": "Ожидаются ключи AI Studio формата AIza...",
                },
                ensure_ascii=False,
            )
        )
        return 1

    if not openclaw_path.exists():
        print(json.dumps({"ok": False, "error": "openclaw_json_not_found", "path": str(openclaw_path)}, ensure_ascii=False))
        return 1

    if not models_path.exists():
        print(json.dumps({"ok": False, "error": "models_json_not_found", "path": str(models_path)}, ensure_ascii=False))
        return 1

    openclaw_payload = _read_json(openclaw_path)
    default_provider, default_model = _default_model_from_openclaw(openclaw_payload)
    channels = tuple(item.strip().lower() for item in str(args.channels).split(",") if item.strip())
    if not channels:
        channels = DEFAULT_CHANNELS

    report: dict[str, Any] = {
        "ok": True,
        "selected_tier": selected_tier,
        "target_key_masked": mask_secret(target_key),
        "channels": list(channels),
        "steps": {},
    }

    report["steps"]["sync_models_json"] = sync_models_json(models_path, target_key)
    report["steps"]["sync_openclaw_json"] = sync_openclaw_json(openclaw_path, target_key)
    report["steps"]["repair_sessions"] = repair_sessions(
        sessions_path,
        channels=channels,
        default_provider=default_provider,
        default_model=default_model,
    )

    dm_policy = str(args.dm_policy or "keep").strip().lower()
    if dm_policy != "keep":
        report["steps"]["dm_policy"] = apply_dm_policy(openclaw_path, channels, dm_policy)
    else:
        report["steps"]["dm_policy"] = {"skipped": True}

    if not args.skip_allowlist_normalize:
        allowlist_steps: dict[str, Any] = {}
        for channel in channels:
            allowlist_path = openclaw_root / "credentials" / f"{channel}-allowFrom.json"
            if allowlist_path.exists():
                allowlist_steps[channel] = normalize_allowlist(allowlist_path)
        report["steps"]["normalize_allowlists"] = allowlist_steps
    else:
        report["steps"]["normalize_allowlists"] = {"skipped": True}

    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
