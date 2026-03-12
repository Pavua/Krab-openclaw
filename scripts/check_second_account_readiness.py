#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проверяет готовность проекта Краб к продолжению работы из соседней macOS-учётки.

Что делает:
1) Снимает machine-readable профиль текущей учётки и локального runtime-контекста.
2) Проверяет, что репозиторий, docs и ключевые runtime-пути доступны из текущего HOME.
3) Пытается прочитать live endpoints `:8080`, если runtime уже поднят в этой учётке.

Зачем:
- переход на другую macOS-учётку должен быть детерминированным и проверяемым;
- нельзя продолжать работу "вслепую", не понимая, какой `~/.openclaw/*` и какой browser state сейчас активны.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"


def _http_json(url: str, *, timeout: float = 2.5) -> dict[str, Any]:
    """Безопасно читает локальный JSON endpoint; на ошибке возвращает structured payload."""
    req = Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as response:  # noqa: S310 - локальный endpoint
            raw = response.read().decode("utf-8", errors="replace")
            return {
                "ok": True,
                "status": int(getattr(response, "status", 200) or 200),
                "json": json.loads(raw),
            }
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "status": None,
            "error": str(exc),
        }


def _default_browser_state_root() -> Path:
    """Возвращает browser state root для текущей учётки."""
    env_candidates = (
        "CHROME_USER_DATA_DIR",
        "GOOGLE_CHROME_USER_DATA_DIR",
        "OPENCLAW_BROWSER_PROFILE_DIR",
    )
    for key in env_candidates:
        raw = str(os.getenv(key, "") or "").strip()
        if raw:
            return Path(raw).expanduser()
    return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"


def _launcher_state_dir(home_dir: Path) -> Path:
    """
    Возвращает per-account каталог launcher/runtime state.

    После multi-account миграции lock/pid/sentinel больше не должны жить в общем
    корне репозитория, иначе соседние учётки будут оставлять ложные stale-хвосты.
    """
    return home_dir / ".openclaw" / "krab_runtime_state"


def _python_candidates() -> list[str]:
    """Возвращает детерминированный список Python-кандидатов для текущей учётки."""
    candidates = [
        ROOT / ".venv" / "bin" / "python",
        ROOT / "venv" / "bin" / "python",
        Path(sys.executable),
    ]
    out: list[str] = []
    for path in candidates:
        text = str(path)
        if text not in out and Path(text).exists():
            out.append(text)
    if "python3" not in out:
        out.append("python3")
    return out


def build_readiness_report() -> dict[str, Any]:
    """Собирает readiness-report для текущей macOS-учётки."""
    home_dir = Path.home()
    operator_name = str(os.getenv("USER", "") or "").strip() or home_dir.name
    openclaw_home = home_dir / ".openclaw"
    launcher_state_dir = _launcher_state_dir(home_dir)
    openclaw_config_path = openclaw_home / "openclaw.json"
    models_path = openclaw_home / "agents" / "main" / "agent" / "models.json"
    auth_profiles_path = openclaw_home / "agents" / "main" / "agent" / "auth-profiles.json"
    browser_state_root = _default_browser_state_root()

    required_docs = {
        "master_plan": DOCS_DIR / "MASTER_PLAN_VNEXT_RU.md",
        "translator_audit": DOCS_DIR / "CALL_TRANSLATOR_AUDIT_RU.md",
        "multi_account_switchover": DOCS_DIR / "MULTI_ACCOUNT_SWITCHOVER_RU.md",
        "parallel_dialog_protocol": DOCS_DIR / "PARALLEL_DIALOG_PROTOCOL_RU.md",
    }
    latest_handoff = sorted(ROOT.glob("artifacts/handoff_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    handoff_latest = latest_handoff[0] if latest_handoff else None

    runtime_profile = _http_json("http://127.0.0.1:8080/api/runtime/operator-profile")
    translator_readiness = _http_json("http://127.0.0.1:8080/api/translator/readiness")
    health_lite = _http_json("http://127.0.0.1:8080/api/health/lite")

    docs_missing = [name for name, path in required_docs.items() if not path.exists()]
    recommendations: list[str] = []
    if docs_missing:
        recommendations.append("В репозитории отсутствуют обязательные docs для перехода в другую учётку.")
    if not openclaw_home.exists():
        recommendations.append("В текущем HOME ещё нет ~/.openclaw; сначала потребуется локальная инициализация runtime и auth.")
    if openclaw_home.exists() and not openclaw_config_path.exists():
        recommendations.append("В текущем HOME нет ~/.openclaw/openclaw.json; перед live-работой нужен OpenClaw bootstrap для этой учётки.")
    if openclaw_home.exists() and not models_path.exists():
        recommendations.append("Отсутствует models.json текущей учётки; cloud/local routing truth будет неполным до bootstrap/sync.")
    if openclaw_home.exists() and not auth_profiles_path.exists():
        recommendations.append("Отсутствует auth-profiles.json текущей учётки; OAuth/API-key truth ещё не инициализирован.")
    if not runtime_profile.get("ok"):
        recommendations.append("Endpoint /api/runtime/operator-profile недоступен; если runtime не поднят, это допустимо, но перед работой нужен fresh запуск.")
    if not health_lite.get("ok"):
        recommendations.append("Owner web panel :8080 сейчас не отвечает; перед live-работой нужно поднять runtime в этой учётке.")

    ready_for_continue = not docs_missing and ROOT.exists()

    return {
        "ok": True,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "multi_account_readiness",
        "strategy": "shared_repo_docs_artifacts__split_runtime_auth_secrets_browser_state_per_account",
        "operator": {
            "name": operator_name,
            "home_dir": str(home_dir),
            "python_candidates": _python_candidates(),
        },
        "project": {
            "root": str(ROOT),
            "exists": ROOT.exists(),
            "writable": bool(os.access(ROOT, os.W_OK)),
            "latest_handoff_bundle": str(handoff_latest) if handoff_latest else None,
        },
        "runtime_paths": {
            "openclaw_home": str(openclaw_home),
            "openclaw_home_exists": openclaw_home.exists(),
            "openclaw_config_path": str(openclaw_config_path),
            "openclaw_config_exists": openclaw_config_path.exists(),
            "auth_profiles_path": str(auth_profiles_path),
            "auth_profiles_exists": auth_profiles_path.exists(),
            "models_path": str(models_path),
            "models_exists": models_path.exists(),
            "launcher_state_dir": str(launcher_state_dir),
            "launcher_state_dir_exists": launcher_state_dir.exists(),
            "launcher_lock_path": str(launcher_state_dir / "launcher.lock"),
            "launcher_stop_flag_path": str(launcher_state_dir / "stop_krab"),
            "browser_state_root": str(browser_state_root),
            "browser_state_root_exists": browser_state_root.exists(),
        },
        "docs": {
            key: {
                "path": str(path),
                "exists": path.exists(),
            }
            for key, path in required_docs.items()
        },
        "live_endpoints": {
            "health_lite": health_lite,
            "runtime_operator_profile": runtime_profile,
            "translator_readiness": translator_readiness,
        },
        "ready_for_continue": ready_for_continue,
        "recommendations": recommendations
        or [
            "Базовая документация и структура доступны; можно продолжать работу из этой учётки после локальной проверки runtime/auth.",
        ],
    }


def main() -> int:
    """Печатает readiness-report в JSON, чтобы его было удобно приложить в handoff."""
    print(json.dumps(build_readiness_report(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
