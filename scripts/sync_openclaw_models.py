#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Синхронизатор OpenClaw models.json из .env (явный и безопасный).

Что делает:
- Читает GEMINI_API_KEY_FREE/GEMINI_API_KEY_PAID из окружения.
- Читает LM_STUDIO_API_KEY / LM_STUDIO_AUTH_TOKEN из окружения.
- Обновляет providers.google.apiKey и, при наличии токена, providers.lmstudio.apiKey
  в ~/.openclaw/agents/main/agent/models.json.
- Обновляет compact provider catalog LM Studio в models.json и openclaw.json,
  чтобы внешние каналы не жили на stale `glm-4.6v-flash`.
- Дополнительно синхронизирует ~/.openclaw/agents/main/agent/auth-profiles.json,
  чтобы direct-каналы не оставались на stale `local-dummy-key`.
- Не печатает секреты в открытом виде.

Зачем:
- Source-of-truth для runtime ключа в OpenClaw — models.json.
- Устраняет рассинхрон после рефакторинга и ручных правок.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

try:
    from scripts.openclaw_runtime_repair import repair_lmstudio_provider_catalog
except ModuleNotFoundError:
    # Скрипт часто запускают как `python scripts/sync_openclaw_models.py`.
    # В этом режиме пакет `scripts` может не попасть в sys.path автоматически.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.openclaw_runtime_repair import repair_lmstudio_provider_catalog


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


def _auth_profiles_path() -> Path:
    return Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"


def _openclaw_path() -> Path:
    return Path.home() / ".openclaw" / "openclaw.json"


def _choose_lmstudio_token() -> str:
    primary = str(os.getenv("LM_STUDIO_API_KEY", "") or "").strip()
    legacy = str(os.getenv("LM_STUDIO_AUTH_TOKEN", "") or "").strip()
    return primary or legacy


def main() -> int:
    load_dotenv()

    free_key = str(os.getenv("GEMINI_API_KEY_FREE", "") or "").strip()
    paid_key = str(os.getenv("GEMINI_API_KEY_PAID", "") or "").strip()
    lmstudio_token = _choose_lmstudio_token()

    path = _models_path()
    auth_profiles_path = _auth_profiles_path()
    openclaw_path = _openclaw_path()
    if not path.exists():
        print(json.dumps({"ok": False, "error": "models_json_not_found", "path": str(path)}, ensure_ascii=False))
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    providers = data.setdefault("providers", {})
    google = providers.setdefault("google", {})
    lmstudio = providers.get("lmstudio") if isinstance(providers.get("lmstudio"), dict) else None

    target_tier = ""
    target_key = ""
    # Платный ключ должен выигрывать у бесплатного автоматически:
    # иначе любой sync может тихо откатить runtime обратно на free-tier,
    # даже если owner уже осознанно держит billing-enabled проект как основной.
    if _is_aistudio(paid_key):
        target_tier = "paid"
        target_key = paid_key
    elif _is_aistudio(free_key):
        target_tier = "free"
        target_key = free_key

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
    prev_lmstudio = str(lmstudio.get("apiKey", "") or "") if lmstudio else ""
    if lmstudio and lmstudio_token:
        lmstudio["apiKey"] = lmstudio_token
        lmstudio["auth"] = "api-key"

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    auth_profiles_payload: dict[str, dict[str, str]] = {}
    if auth_profiles_path.exists():
        loaded = json.loads(auth_profiles_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            auth_profiles_payload = loaded

    def _ensure_profile(name: str) -> dict[str, str]:
        profile = auth_profiles_payload.get(name)
        if not isinstance(profile, dict):
            profile = {}
            auth_profiles_payload[name] = profile
        return profile

    auth_google = _ensure_profile("google")
    auth_gemini = _ensure_profile("gemini")
    auth_lmstudio = _ensure_profile("lmstudio")
    prev_auth_google = str(auth_google.get("apiKey", "") or "")
    prev_auth_gemini = str(auth_gemini.get("apiKey", "") or "")
    prev_auth_lmstudio = str(auth_lmstudio.get("apiKey", "") or "")

    auth_google["apiKey"] = target_key
    auth_gemini["apiKey"] = target_key
    if lmstudio_token:
        auth_lmstudio["apiKey"] = lmstudio_token

    auth_profiles_path.parent.mkdir(parents=True, exist_ok=True)
    auth_profiles_path.write_text(
        json.dumps(auth_profiles_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    primary_model = "lmstudio/nvidia/nemotron-3-nano"
    if openclaw_path.exists():
        try:
            openclaw_payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
            primary_model = str(
                (
                    openclaw_payload.get("agents", {})
                    .get("defaults", {})
                    .get("model", {})
                    .get("primary", primary_model)
                )
                or primary_model
            )
        except (OSError, ValueError, TypeError):
            primary_model = "lmstudio/nvidia/nemotron-3-nano"

    preferred_vision_model = str(os.getenv("LOCAL_PREFERRED_VISION_MODEL", "auto") or "").strip()
    preferred_text_model = str(os.getenv("LOCAL_PREFERRED_MODEL", "") or "").strip()
    models_catalog_report = repair_lmstudio_provider_catalog(
        path,
        primary_model=primary_model,
        preferred_text_model=preferred_text_model,
        preferred_vision_model=preferred_vision_model,
        lmstudio_token=lmstudio_token,
    )
    openclaw_catalog_report = {"skipped": True}
    if openclaw_path.exists():
        openclaw_catalog_report = repair_lmstudio_provider_catalog(
            openclaw_path,
            primary_model=primary_model,
            preferred_text_model=preferred_text_model,
            preferred_vision_model=preferred_vision_model,
            lmstudio_token=lmstudio_token,
        )

    print(
        json.dumps(
            {
                "ok": True,
                "path": str(path),
                "openclaw_path": str(openclaw_path),
                "auth_profiles_path": str(auth_profiles_path),
                "selected_tier": target_tier,
                "prev_key_masked": _mask(prev_key),
                "new_key_masked": _mask(target_key),
                "google_auth": str(google.get("auth", "api-key")),
                "google_api": str(google.get("api", "google-generative-ai")),
                "lmstudio_token_present": bool(lmstudio_token),
                "lmstudio_prev_masked": _mask(prev_lmstudio),
                "lmstudio_new_masked": _mask(lmstudio_token),
                "auth_profiles_prev_google_masked": _mask(prev_auth_google),
                "auth_profiles_prev_gemini_masked": _mask(prev_auth_gemini),
                "auth_profiles_prev_lmstudio_masked": _mask(prev_auth_lmstudio),
                "models_catalog": models_catalog_report,
                "openclaw_catalog": openclaw_catalog_report,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
