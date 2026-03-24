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
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
ENV_PATH = ROOT / ".env"
CANONICAL_SHARED_ROOT = Path("/Users/Shared/Antigravity_AGENTS/Краб")
ACTIVE_SHARED_ROOT = Path("/Users/Shared/Antigravity_AGENTS/Краб-active")


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


def _read_env_file(path: Path) -> dict[str, str]:
    """Минимальный парсер .env без внешних зависимостей."""
    if not path.exists():
        return {}
    loaded: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
            value = value[1:-1]
        loaded[key] = os.path.expandvars(os.path.expanduser(value))
    return loaded


def _tool_presence() -> dict[str, bool]:
    """Сводка наличия базовых CLI-инструментов."""
    return {
        "python3": bool(shutil.which("python3")),
        "node": bool(shutil.which("node")),
        "npx": bool(shutil.which("npx")),
        "rg": bool(shutil.which("rg")),
        "openclaw": bool(shutil.which("openclaw")),
        "gh": bool(shutil.which("gh")),
    }


def _git_stdout(args: list[str], *, cwd: Path) -> str:
    """Возвращает stdout git-команды или пустую строку."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _write_access_probe(path: Path) -> dict[str, Any]:
    """Проверяет, можно ли безопасно писать в shared path текущей учёткой."""
    exists = path.exists()
    writable = bool(os.access(path, os.W_OK)) if exists else False
    mode = ""
    owner_uid: int | None = None
    group_gid: int | None = None
    if exists:
        try:
            stats = path.stat()
            mode = stat.filemode(stats.st_mode)
            owner_uid = stats.st_uid
            group_gid = stats.st_gid
        except OSError:
            mode = ""
    return {
        "path": str(path),
        "exists": exists,
        "writable": writable,
        "mode": mode,
        "owner_uid": owner_uid,
        "group_gid": group_gid,
    }


def _shared_repo_status() -> dict[str, Any]:
    """Собирает статус канонического shared repo и его drift относительно текущей копии."""
    exists = CANONICAL_SHARED_ROOT.exists()
    git_dir_exists = (CANONICAL_SHARED_ROOT / ".git").exists()
    branch = _git_stdout(["rev-parse", "--abbrev-ref", "HEAD"], cwd=CANONICAL_SHARED_ROOT) if git_dir_exists else ""
    head = _git_stdout(["rev-parse", "HEAD"], cwd=CANONICAL_SHARED_ROOT) if git_dir_exists else ""
    status_short = _git_stdout(["status", "--short", "--branch"], cwd=CANONICAL_SHARED_ROOT) if git_dir_exists else ""
    current_branch = _git_stdout(["rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT)
    current_head = _git_stdout(["rev-parse", "HEAD"], cwd=ROOT)
    return {
        "path": str(CANONICAL_SHARED_ROOT),
        "exists": exists,
        "git_dir_exists": git_dir_exists,
        "branch": branch,
        "head": head,
        "status_short": status_short,
        "current_branch": current_branch,
        "current_head": current_head,
        "drift_vs_current_repo": bool(branch and head and (branch != current_branch or head != current_head)),
        "write_access": {
            "repo_root": _write_access_probe(CANONICAL_SHARED_ROOT),
            "docs_dir": _write_access_probe(CANONICAL_SHARED_ROOT / "docs"),
            "artifacts_dir": _write_access_probe(CANONICAL_SHARED_ROOT / "artifacts"),
        },
    }


def _active_shared_worktree_status() -> dict[str, Any]:
    """Собирает truthful статус fast-path shared worktree `Краб-active`."""
    exists = ACTIVE_SHARED_ROOT.exists()
    git_dir_exists = (ACTIVE_SHARED_ROOT / ".git").exists()
    marker_path = ACTIVE_SHARED_ROOT / "ACTIVE_SHARED_WORKTREE.json"
    marker_payload: dict[str, Any] = {}
    if marker_path.exists():
        try:
            marker_payload = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            marker_payload = {}
    branch = _git_stdout(["rev-parse", "--abbrev-ref", "HEAD"], cwd=ACTIVE_SHARED_ROOT) if git_dir_exists else ""
    head = _git_stdout(["rev-parse", "HEAD"], cwd=ACTIVE_SHARED_ROOT) if git_dir_exists else ""
    status_short = _git_stdout(["status", "--short", "--branch"], cwd=ACTIVE_SHARED_ROOT) if git_dir_exists else ""
    current_branch = _git_stdout(["rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT)
    current_head = _git_stdout(["rev-parse", "HEAD"], cwd=ROOT)
    return {
        "path": str(ACTIVE_SHARED_ROOT),
        "exists": exists,
        "git_dir_exists": git_dir_exists,
        "marker_path": str(marker_path),
        "marker_exists": marker_path.exists(),
        "marker_payload": marker_payload,
        "branch": branch,
        "head": head,
        "status_short": status_short,
        "current_branch": current_branch,
        "current_head": current_head,
        "matches_current_repo": bool(branch and head and branch == current_branch and head == current_head),
        "write_access": {
            "repo_root": _write_access_probe(ACTIVE_SHARED_ROOT),
            "docs_dir": _write_access_probe(ACTIVE_SHARED_ROOT / "docs"),
            "artifacts_dir": _write_access_probe(ACTIVE_SHARED_ROOT / "artifacts"),
        },
    }


def _gh_auth_status() -> dict[str, Any]:
    """Проверяет, авторизован ли GitHub CLI."""
    if not shutil.which("gh"):
        return {"available": False, "authenticated": False, "login": "", "error": "gh_not_installed"}

    status = subprocess.run(
        ["gh", "auth", "status", "--hostname", "github.com"],
        capture_output=True,
        text=True,
        check=False,
    )
    authenticated = status.returncode == 0
    login = ""
    if authenticated:
        login_proc = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True,
            text=True,
            check=False,
        )
        if login_proc.returncode == 0:
            login = (login_proc.stdout or "").strip()
    return {
        "available": True,
        "authenticated": authenticated,
        "login": login,
        "error": "" if authenticated else (status.stderr or status.stdout or "").strip(),
    }


def _lmstudio_mcp_status() -> dict[str, Any]:
    """Читает LM Studio mcp.json и проверяет наличие ключевых серверов."""
    path = Path.home() / ".lmstudio" / "mcp.json"
    if not path.exists():
        return {
            "exists": False,
            "path": str(path),
            "servers": [],
            "context7_present": False,
            "github_present": False,
            "chrome_profile_present": False,
            "openclaw_browser_present": False,
            "error": "mcp_json_missing",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {
            "exists": True,
            "path": str(path),
            "servers": [],
            "context7_present": False,
            "github_present": False,
            "chrome_profile_present": False,
            "openclaw_browser_present": False,
            "error": str(exc),
        }

    servers = payload.get("mcpServers", {}) if isinstance(payload, dict) else {}
    if not isinstance(servers, dict):
        servers = {}
    names = sorted(servers.keys())
    return {
        "exists": True,
        "path": str(path),
        "servers": names,
        "context7_present": "context7" in servers,
        "github_present": "github" in servers,
        "chrome_profile_present": "chrome-profile" in servers,
        "openclaw_browser_present": "openclaw-browser" in servers,
        "error": "",
    }


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
    env_loaded = _read_env_file(ENV_PATH)
    merged_env = dict(env_loaded)
    merged_env.update(os.environ)
    context7_key = str(merged_env.get("CONTEXT7_API_KEY", "") or "").strip()
    tools = _tool_presence()
    github_cli = _gh_auth_status()
    lmstudio_mcp = _lmstudio_mcp_status()
    shared_repo = _shared_repo_status()
    active_shared_worktree = _active_shared_worktree_status()
    latest_handoff = sorted(ROOT.glob("artifacts/handoff_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    handoff_latest = latest_handoff[0] if latest_handoff else None

    runtime_profile = _http_json("http://127.0.0.1:8080/api/runtime/operator-profile")
    translator_readiness = _http_json("http://127.0.0.1:8080/api/translator/readiness")
    health_lite = _http_json("http://127.0.0.1:8080/api/health/lite")

    docs_missing = [name for name, path in required_docs.items() if not path.exists()]
    recommendations: list[str] = []
    blockers: list[str] = []
    if docs_missing:
        recommendations.append("В репозитории отсутствуют обязательные docs для перехода в другую учётку.")
        blockers.append("missing_docs")
    missing_tools = [name for name, ok in tools.items() if not ok]
    if missing_tools:
        recommendations.append(f"Отсутствуют CLI-инструменты: {', '.join(sorted(missing_tools))}.")
        blockers.append("missing_tools")
    if not openclaw_home.exists():
        recommendations.append("В текущем HOME ещё нет ~/.openclaw; сначала потребуется локальная инициализация runtime и auth.")
    if openclaw_home.exists() and not openclaw_config_path.exists():
        recommendations.append("В текущем HOME нет ~/.openclaw/openclaw.json; перед live-работой нужен OpenClaw bootstrap для этой учётки.")
    if openclaw_home.exists() and not models_path.exists():
        recommendations.append("Отсутствует models.json текущей учётки; cloud/local routing truth будет неполным до bootstrap/sync.")
    if openclaw_home.exists() and not auth_profiles_path.exists():
        recommendations.append("Отсутствует auth-profiles.json текущей учётки; OAuth/API-key truth ещё не инициализирован.")
    if not context7_key:
        recommendations.append("Context7 API ключ не найден; MCP документация будет недоступна.")
    if not lmstudio_mcp.get("exists"):
        recommendations.append("LM Studio mcp.json не найден; синхронизируй через Sync LM Studio MCP.command.")
    if lmstudio_mcp.get("exists") and not lmstudio_mcp.get("context7_present"):
        recommendations.append("LM Studio mcp.json не содержит context7; проверь Sync LM Studio MCP.command.")
    if github_cli.get("available") and not github_cli.get("authenticated"):
        recommendations.append("GitHub CLI не авторизован; запусти Login GitHub Push Auth.command.")
    if not runtime_profile.get("ok"):
        recommendations.append("Endpoint /api/runtime/operator-profile недоступен; если runtime не поднят, это допустимо, но перед работой нужен fresh запуск.")
    if not health_lite.get("ok"):
        recommendations.append("Owner web panel :8080 сейчас не отвечает; перед live-работой нужно поднять runtime в этой учётке.")
    active_fast_path_ready = bool(active_shared_worktree.get("matches_current_repo"))
    if not shared_repo.get("exists"):
        if active_fast_path_ready:
            recommendations.append("Канонический shared repo отсутствует, но `Краб-active` уже совпадает с текущим WIP и годится как fast-path.")
        else:
            recommendations.append("Канонический shared repo отсутствует; multi-account режим будет непредсказуемым до восстановления /Users/Shared/Antigravity_AGENTS/Краб или публикации `Краб-active`.")
            blockers.append("shared_repo_missing")
    elif shared_repo.get("drift_vs_current_repo"):
        if active_fast_path_ready:
            recommendations.append("Legacy shared repo расходится с текущей копией, но `Краб-active` уже опубликован и совпадает с текущим WIP.")
        else:
            recommendations.append("Shared repo расходится с текущей рабочей копией; перед новым coding/live циклом синхронизируй branch и HEAD осознанно или опубликуй `Краб-active`.")
            blockers.append("shared_repo_drift")
    shared_writes = shared_repo.get("write_access") if isinstance(shared_repo.get("write_access"), dict) else {}
    for key in ("repo_root", "docs_dir", "artifacts_dir"):
        probe = shared_writes.get(key) if isinstance(shared_writes.get(key), dict) else {}
        if probe.get("exists") and not probe.get("writable"):
            recommendations.append(f"Нет записи в shared path `{probe.get('path')}`; другая учётка не сможет безопасно обновлять repo/docs/artifacts.")
            blockers.append(f"{key}_not_writable")
    active_shared_writes = (
        active_shared_worktree.get("write_access")
        if isinstance(active_shared_worktree.get("write_access"), dict)
        else {}
    )
    for key in ("repo_root", "docs_dir", "artifacts_dir"):
        probe = active_shared_writes.get(key) if isinstance(active_shared_writes.get(key), dict) else {}
        if probe.get("exists") and not probe.get("writable"):
            recommendations.append(
                f"Fast-path `{probe.get('path')}` опубликован, но текущая учётка не может в него писать; проверь права именно на этот shared path."
            )

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
        "tools": tools,
        "mcp": {
            "context7_api_key_present": bool(context7_key),
            "github_cli": github_cli,
            "lmstudio_mcp": lmstudio_mcp,
        },
        "project": {
            "root": str(ROOT),
            "exists": ROOT.exists(),
            "writable": bool(os.access(ROOT, os.W_OK)),
            "latest_handoff_bundle": str(handoff_latest) if handoff_latest else None,
        },
        "shared_repo": shared_repo,
        "active_shared_worktree": active_shared_worktree,
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
        "blocking_items": blockers,
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
