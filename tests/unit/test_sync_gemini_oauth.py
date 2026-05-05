"""Wave 25-A: Тесты sync_gemini_oauth_to_openclaw.

4 сценария:
  1. source missing → returns 0, no-op
  2. destination missing → returns 0, no-op
  3. already synced → returns 0, no write
  4. desynced → atomic write, оба набора полей обновлены
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

# --- вспомогательная загрузка модуля с патченными путями ---

def _load_module(
    tmp_path: Path,
    gemini_creds: dict | None,
    openclaw_auth: dict | None,
):
    """
    Загружает sync_gemini_oauth_to_openclaw с подменёнными константами путей.
    gemini_creds=None → файл не создаётся (тест 'source missing').
    openclaw_auth=None → файл не создаётся (тест 'destination missing').
    """
    gemini_path = tmp_path / ".gemini" / "oauth_creds.json"
    openclaw_path = tmp_path / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    log_path = tmp_path / ".openclaw" / "krab_runtime_state" / "oauth_resync.log"

    if gemini_creds is not None:
        gemini_path.parent.mkdir(parents=True, exist_ok=True)
        gemini_path.write_text(json.dumps(gemini_creds), encoding="utf-8")

    if openclaw_auth is not None:
        openclaw_path.parent.mkdir(parents=True, exist_ok=True)
        openclaw_path.write_text(json.dumps(openclaw_auth), encoding="utf-8")

    # Удаляем кэш модуля если уже загружался
    for key in list(sys.modules):
        if "sync_gemini_oauth_to_openclaw" in key:
            del sys.modules[key]

    # Добавляем scripts/ в path для импорта
    scripts_dir = Path(__file__).parents[2] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import sync_gemini_oauth_to_openclaw as mod

    # Патчим константы путей и лог
    mod.GEMINI_CREDS = gemini_path
    mod.OPENCLAW_AUTH = openclaw_path
    mod.LOG_FILE = log_path

    return mod, openclaw_path


# --- фикстуры данных ---

GEMINI_CREDS_PAYLOAD = {
    "access_token": "ya29.new-access",
    "refresh_token": "1//new-refresh",
    "expiry_date": 9999999999000,
    "token_type": "Bearer",
    "scope": "https://www.googleapis.com/auth/cloud-platform",
    "id_token": "id-token-value",
}

OPENCLAW_AUTH_PAYLOAD = {
    "version": 1,
    "profiles": {
        "google-gemini-cli:pavelr7@gmail.com": {
            "type": "oauth",
            "provider": "google-gemini-cli",
            "access": "ya29.old-access",
            "refresh": "1//old-refresh",
            "expires": 1000,
            "access_token": "ya29.old-access",
            "refresh_token": "1//old-refresh",
            "expiry_date": 1000,
            "token_type": "Bearer",
            "scope": "old-scope",
            "id_token": "old-id-token",
            "email": "pavelr7@gmail.com",
        }
    },
}


# --- тесты ---


def test_source_missing_returns_zero(tmp_path: Path) -> None:
    """Если ~/.gemini/oauth_creds.json отсутствует — выходим 0, ничего не пишем."""
    mod, openclaw_path = _load_module(tmp_path, gemini_creds=None, openclaw_auth=OPENCLAW_AUTH_PAYLOAD)

    result = mod.main()

    assert result == 0
    # openclaw файл не трогаем
    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert payload["profiles"]["google-gemini-cli:pavelr7@gmail.com"]["access"] == "ya29.old-access"


def test_destination_missing_returns_zero(tmp_path: Path) -> None:
    """Если auth-profiles.json отсутствует — выходим 0, не падаем."""
    mod, openclaw_path = _load_module(tmp_path, gemini_creds=GEMINI_CREDS_PAYLOAD, openclaw_auth=None)

    result = mod.main()

    assert result == 0
    assert not openclaw_path.exists()


def test_already_synced_no_write(tmp_path: Path) -> None:
    """Если токены уже совпадают — idempotent no-op, файл не перезаписывается."""
    # Синхронизированный стейт: access/refresh/expires совпадают с gemini
    synced_auth = {
        "version": 1,
        "profiles": {
            "google-gemini-cli:pavelr7@gmail.com": {
                "type": "oauth",
                "provider": "google-gemini-cli",
                # родные OpenClaw
                "access": GEMINI_CREDS_PAYLOAD["access_token"],
                "refresh": GEMINI_CREDS_PAYLOAD["refresh_token"],
                "expires": GEMINI_CREDS_PAYLOAD["expiry_date"],
                # gemini-cli зеркало
                "access_token": GEMINI_CREDS_PAYLOAD["access_token"],
                "refresh_token": GEMINI_CREDS_PAYLOAD["refresh_token"],
                "expiry_date": GEMINI_CREDS_PAYLOAD["expiry_date"],
                "token_type": "Bearer",
                "scope": GEMINI_CREDS_PAYLOAD["scope"],
                "id_token": GEMINI_CREDS_PAYLOAD["id_token"],
                "email": "pavelr7@gmail.com",
            }
        },
    }

    mod, openclaw_path = _load_module(tmp_path, gemini_creds=GEMINI_CREDS_PAYLOAD, openclaw_auth=synced_auth)

    # Запоминаем mtime до вызова
    mtime_before = openclaw_path.stat().st_mtime

    result = mod.main()

    assert result == 0
    # Файл не должен быть перезаписан (mtime тот же)
    assert openclaw_path.stat().st_mtime == mtime_before


def test_desynced_atomic_write_updates_both_field_sets(tmp_path: Path) -> None:
    """При desync — atomic write обновляет оба набора полей (OpenClaw + gemini-cli)."""
    mod, openclaw_path = _load_module(
        tmp_path, gemini_creds=GEMINI_CREDS_PAYLOAD, openclaw_auth=OPENCLAW_AUTH_PAYLOAD
    )

    result = mod.main()

    assert result == 0
    assert openclaw_path.exists()
    # tmp файл должен быть удалён после os.replace
    assert not openclaw_path.with_suffix(".tmp").exists()

    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    prof = payload["profiles"]["google-gemini-cli:pavelr7@gmail.com"]

    # --- родные OpenClaw-поля ---
    assert prof["access"] == GEMINI_CREDS_PAYLOAD["access_token"]
    assert prof["refresh"] == GEMINI_CREDS_PAYLOAD["refresh_token"]
    assert prof["expires"] == GEMINI_CREDS_PAYLOAD["expiry_date"]

    # --- gemini-cli зеркало ---
    assert prof["access_token"] == GEMINI_CREDS_PAYLOAD["access_token"]
    assert prof["refresh_token"] == GEMINI_CREDS_PAYLOAD["refresh_token"]
    assert prof["expiry_date"] == GEMINI_CREDS_PAYLOAD["expiry_date"]
    assert prof["token_type"] == "Bearer"
    assert prof["scope"] == GEMINI_CREDS_PAYLOAD["scope"]
    assert prof["id_token"] == GEMINI_CREDS_PAYLOAD["id_token"]

    # --- прочие поля профиля сохранены ---
    assert prof["email"] == "pavelr7@gmail.com"
    assert prof["type"] == "oauth"
