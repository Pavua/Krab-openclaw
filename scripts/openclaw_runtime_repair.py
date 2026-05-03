#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenClaw runtime repair — вызывается из /api/runtime/recover (Wave 16-J).

Без этого скрипта endpoint возвращал exit 127 (script_not_found) и вся
recovery chain ломалась. Скрипт выполняет полный цикл диагностики и
авторемонта runtime-контура.

Шаги:
1. Validate openclaw.json (JSON-парс, обязательные ключи, web_search.provider)
2. Probe gateway :18789/health
3. Repair Pyrogram session via sqlite3 .recover (idempotent, 1h cooldown)
4. Detect stale plugin entries (report-only, без авто-удаления)

Exit codes:
  0 — всё ok (или non-critical issues)
  1 — есть ошибки (non-fatal для chain)
  78 — критично: требует ручного вмешательства (recovery loop detected)

Output: structured JSON в stdout.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ── константы ────────────────────────────────────────────────────────────────

_OPENCLAW_JSON = Path.home() / ".openclaw" / "openclaw.json"
_GATEWAY_HEALTH_URL = "http://127.0.0.1:18789/health"
_GATEWAY_TIMEOUT_SEC = 5

# Обязательные top-level ключи в openclaw.json
_REQUIRED_KEYS = ("agents", "providers", "tools")

# Допустимые провайдеры для tools.web.search.provider
_VALID_SEARCH_PROVIDERS = frozenset(
    {"brave", "duckduckgo", "exa", "firecrawl", "gemini", "kimi", "perplexity", "tavily"}
)

# Kraab session — источник истины (critical DB)
_SESSION_PATH = Path(__file__).parent.parent / "data" / "sessions" / "kraab.session"

# Idempotency cooldown: повторная попытка recovery в пределах 1h → exit 78
_RECENT_BACKUP_COOLDOWN_SEC = 3600


# ── helpers ──────────────────────────────────────────────────────────────────


def _clean_subprocess_env() -> dict[str, str]:
    """Подключаем clean_subprocess_env из src/ или строим локально."""
    import os

    try:
        # Если запускается из repo — импортируем готовый helper
        repo_root = Path(__file__).parent.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root / "src"))
        from core.subprocess_env import clean_subprocess_env  # type: ignore[import]

        return clean_subprocess_env()
    except ImportError:
        # Fallback: воспроизводим логику inline
        env = os.environ.copy()
        for key in (
            "MallocStackLogging",
            "MallocStackLoggingNoCompact",
            "MallocScribble",
            "MallocGuardEdges",
            "MallocCheckHeapEach",
        ):
            env.pop(key, None)
        current_path = env.get("PATH", "")
        path_entries = current_path.split(os.pathsep) if current_path else []
        for prefix in ("/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"):
            if prefix not in path_entries:
                path_entries.insert(0, prefix)
        env["PATH"] = os.pathsep.join(path_entries)
        return env


def _integrity_check(path: Path) -> tuple[bool, str]:
    """PRAGMA quick_check на read-only URI. Возвращает (ok, detail)."""
    uri = f"file:{path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        try:
            cur = conn.execute("PRAGMA quick_check")
            result = cur.fetchone()
            if result and result[0] == "ok":
                return True, "ok"
            # quick_check вернул строку с ошибкой
            return False, str(result[0]) if result else "no_result"
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return False, f"db_error: {exc}"
    except Exception as exc:  # noqa: BLE001
        return False, f"unexpected: {exc}"


def _recovery_backup_paths(path: Path) -> list[Path]:
    """Ищет backup-файлы *.bak-corrupt-* рядом с path."""
    if not path.parent.exists():
        return []
    prefix = f"{path.name}.bak-corrupt-"
    return [p for p in path.parent.iterdir() if p.name.startswith(prefix)]


def _has_recent_backup(path: Path, *, within_sec: int = _RECENT_BACKUP_COOLDOWN_SEC) -> bool:
    """True если recent backup (< 1h) уже существует → idempotency guard."""
    cutoff = time.time() - within_sec
    for backup in _recovery_backup_paths(path):
        try:
            if backup.stat().st_mtime >= cutoff:
                return True
        except OSError:
            continue
    return False


def _cleanup_sidecars(path: Path) -> list[str]:
    """Удаляет WAL/SHM/journal sidecar-файлы рядом с corrupt session."""
    removed: list[str] = []
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            try:
                sidecar.unlink()
                removed.append(str(sidecar))
            except OSError:
                pass
    return removed


# ── check functions ───────────────────────────────────────────────────────────


def check_openclaw_config(verbose: bool = False) -> dict[str, Any]:
    """
    Шаг 1: Validate openclaw.json.

    - JSON парсится без ошибок
    - Required keys присутствуют
    - tools.web.search.provider входит в допустимый список
    Не модифицирует файл (read-only).
    """
    result: dict[str, Any] = {
        "name": "config_valid",
        "ok": False,
        "detail": "",
    }
    if not _OPENCLAW_JSON.exists():
        result["detail"] = f"missing:{_OPENCLAW_JSON}"
        return result

    # 1a. JSON-парс
    try:
        data: dict = json.loads(_OPENCLAW_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        result["detail"] = f"json_parse_error: {exc}"
        return result

    # 1b. Обязательные top-level ключи
    missing_keys = [k for k in _REQUIRED_KEYS if k not in data]
    if missing_keys:
        # providers может отсутствовать в новом формате (они в agents/*.json)
        # сообщаем но НЕ фейлим критически — некоторые версии OpenClaw их не имеют
        result["ok"] = True  # non-fatal warning
        result["detail"] = f"missing_optional_keys:{missing_keys}"
        result["warning"] = True
        return result

    # 1c. tools.web.search.provider
    tools: dict = data.get("tools", {})
    web: dict = tools.get("web", {})
    search: dict = web.get("search", {})
    provider: str = str(search.get("provider", "") or "").strip().lower()

    if provider and provider not in _VALID_SEARCH_PROVIDERS:
        result["detail"] = (
            f"invalid_provider:{provider!r} at tools.web.search.provider "
            f"(valid:{sorted(_VALID_SEARCH_PROVIDERS)})"
        )
        # Репортим как warning — Krab может работать без web_search
        result["ok"] = True
        result["warning"] = True
        result["invalid_provider"] = provider
        return result

    detail = f"ok (provider={provider!r})" if provider else "ok (no search provider set)"
    if verbose:
        detail += f" keys={sorted(data.keys())}"
    result["ok"] = True
    result["detail"] = detail
    return result


def check_gateway_health(verbose: bool = False) -> dict[str, Any]:
    """
    Шаг 2: Probe gateway :18789/health через urllib (no external deps needed).

    НЕ запускает openclaw gateway — это ответственность runtime invoke.
    """
    result: dict[str, Any] = {
        "name": "gateway_health",
        "ok": False,
        "detail": "",
    }
    try:
        req = urllib.request.Request(_GATEWAY_HEALTH_URL, method="GET")
        with urllib.request.urlopen(req, timeout=_GATEWAY_TIMEOUT_SEC) as resp:
            status = resp.status
            body = resp.read(512).decode("utf-8", errors="replace")
            if status == 200:
                result["ok"] = True
                result["detail"] = f"http_200 body_prefix={body[:80]!r}"
            else:
                result["detail"] = f"http_{status}"
    except urllib.error.URLError as exc:
        result["detail"] = f"unreachable:{exc.reason}"
        result["hint"] = "Запусти: openclaw gateway"
    except OSError as exc:
        result["detail"] = f"connection_error:{exc}"
        result["hint"] = "Запусти: openclaw gateway"
    except Exception as exc:  # noqa: BLE001
        result["detail"] = f"unexpected:{exc}"
    return result


def repair_session_integrity(
    session_path: Path | None = None,
    *,
    check_only: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Шаг 3: Проверяем и при необходимости восстанавливаем kraab.session.

    Wave 16-N: делегирует recovery в shared module src/bootstrap/session_recovery.py
    (DRY — та же логика используется preflight'ом и этим скриптом).
    Внешний API (имена ключей, exit codes) НЕ изменён — backward-compatible.

    - PRAGMA quick_check (read-only)
    - Если corrupt и check_only=False:
        - Idempotency guard: recent backup < 1h → exit 78
        - Backup → bak-corrupt-{ts}
        - sqlite3 .recover | sqlite3 fresh  (через shared attempt_recovery)
        - Verify integrity → atomic replace
    """
    path = session_path or _SESSION_PATH
    result: dict[str, Any] = {
        "name": "session_integrity",
        "ok": False,
        "detail": "",
        "action": "none",
        "path": str(path),
    }

    if not path.exists():
        # Session отсутствует — не ошибка (первый запуск или уже удалён)
        result["ok"] = True
        result["detail"] = "session_not_found (ok for first run)"
        result["action"] = "none"
        return result

    ok, detail = _integrity_check(path)
    if ok:
        result["ok"] = True
        result["detail"] = "integrity_ok"
        result["action"] = "none"
        return result

    # Сессия повреждена
    result["detail"] = f"corrupt:{detail}"

    if check_only:
        result["ok"] = False
        result["action"] = "skipped_check_only"
        return result

    # Idempotency guard
    if _has_recent_backup(path):
        result["detail"] = f"corrupt:{detail} — recent backup exists (<1h), recovery loop detected"
        result["action"] = "exit_78"
        result["ok"] = False
        result["exit_code_override"] = 78
        return result

    # Recovery flow — делегируем в shared module (Wave 16-N DRY).
    # Пробуем импортировать из src/ (если запущены из repo root),
    # иначе падаем обратно на inline recovery (stand-alone run).
    try:
        repo_root = Path(__file__).parent.parent
        _src_root = str(repo_root / "src")
        if _src_root not in sys.path:
            sys.path.insert(0, _src_root)
        from bootstrap.session_recovery import (
            attempt_recovery as _attempt_recovery,  # type: ignore[import]
        )

        recovery = _attempt_recovery(path, idempotency_sec=0)  # idempotency уже проверена выше
        if recovery.get("recovered"):
            result["ok"] = True
            result["action"] = "recovered"
            result["backup_path"] = recovery.get("backup_path", "")
            result["detail"] = "recovered_ok"
            if verbose and recovery.get("sidecars_removed"):
                result["sidecars_removed"] = recovery["sidecars_removed"]
            return result
        # Recovery failed — возвращаем detail из shared module.
        result["detail"] = f"corrupt:{detail} — {recovery.get('detail', 'recovery_failed')}"
        return result

    except ImportError:
        # Fallback: inline recovery если shared module недоступен
        # (например, скрипт запущен вне repo context).
        pass

    # Inline recovery (fallback для stand-alone run без src/ в PATH).
    ts = int(time.time())
    backup_path = path.with_name(f"{path.name}.bak-corrupt-{ts}")
    fresh_path = path.with_name(f"{path.name}.recovered-{ts}")

    # Backup
    try:
        shutil.copy2(path, backup_path)
    except OSError as exc:
        result["detail"] = f"backup_failed:{exc}"
        return result

    # Cleanup sidecars
    removed = _cleanup_sidecars(path)
    if removed and verbose:
        result["sidecars_removed"] = removed

    # sqlite3 .recover | sqlite3 fresh
    env = _clean_subprocess_env()
    try:
        dump = subprocess.run(
            ["sqlite3", str(path), ".recover"],
            capture_output=True,
            timeout=30.0,
            check=False,
            env=env,
        )
        if dump.returncode != 0 and not dump.stdout:
            result["detail"] = (
                f"recover_dump_failed rc={dump.returncode} "
                f"stderr={dump.stderr.decode('utf-8', errors='replace')[:200]}"
            )
            return result

        load = subprocess.run(
            ["sqlite3", str(fresh_path)],
            input=dump.stdout,
            capture_output=True,
            timeout=30.0,
            check=False,
            env=env,
        )
        if load.returncode != 0:
            result["detail"] = (
                f"recover_load_failed rc={load.returncode} "
                f"stderr={load.stderr.decode('utf-8', errors='replace')[:200]}"
            )
            return result

    except subprocess.TimeoutExpired:
        result["detail"] = "recover_timeout"
        return result
    except FileNotFoundError:
        result["detail"] = "sqlite3_not_in_path"
        return result
    except Exception as exc:  # noqa: BLE001
        result["detail"] = f"recover_unexpected:{exc}"
        return result

    # Verify recovered integrity
    ok2, detail2 = _integrity_check(fresh_path)
    if not ok2:
        result["detail"] = f"recovered_still_corrupt:{detail2}"
        try:
            fresh_path.unlink()
        except OSError:
            pass
        return result

    # Atomic replace
    try:
        fresh_path.replace(path)
    except OSError as exc:
        result["detail"] = f"atomic_replace_failed:{exc}"
        try:
            fresh_path.unlink()
        except OSError:
            pass
        return result

    result["ok"] = True
    result["action"] = "recovered"
    result["backup_path"] = str(backup_path)
    result["detail"] = "recovered_ok"
    return result


def check_stale_plugins(verbose: bool = False) -> dict[str, Any]:
    """
    Шаг 4: Detect stale plugin entries в openclaw.json.

    Report-only — НЕ удаляет ничего автоматически (manual decision).
    """
    result: dict[str, Any] = {
        "name": "stale_plugins",
        "ok": True,
        "detail": "",
        "action": "report_only",
    }
    if not _OPENCLAW_JSON.exists():
        result["detail"] = "config_missing_skip"
        return result

    try:
        data = json.loads(_OPENCLAW_JSON.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        result["detail"] = f"parse_error:{exc}"
        return result

    plugins: dict = data.get("plugins", {})
    entries: dict = plugins.get("entries", {})
    if not entries:
        result["detail"] = "no_plugin_entries"
        return result

    # Определяем stale: entry есть, но installed=false или вообще нет installed ключа
    stale: list[str] = []
    for name, entry in entries.items():
        if isinstance(entry, dict) and not entry.get("installed", True):
            stale.append(name)

    if stale:
        result["detail"] = f"stale_entries:{stale} (manual cleanup needed)"
        result["stale_plugins"] = stale
        if verbose:
            result["all_plugins"] = list(entries.keys())
    else:
        result["detail"] = f"ok ({len(entries)} entries, none stale)"

    return result


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OpenClaw runtime repair script (Wave 16-J)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Только диагностика, без авторемонта",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Детальный вывод",
    )
    parser.add_argument(
        "--session-path",
        type=Path,
        default=None,
        help="Путь к kraab.session (по умолчанию: data/sessions/kraab.session)",
    )
    args = parser.parse_args()

    t_start = time.monotonic()

    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    # Шаг 1: config validation
    cfg_check = check_openclaw_config(verbose=args.verbose)
    checks.append(cfg_check)
    if not cfg_check["ok"]:
        errors.append(f"config_valid: {cfg_check['detail']}")
    elif cfg_check.get("warning"):
        warnings.append(f"config_valid: {cfg_check['detail']}")

    # Шаг 2: gateway probe
    gw_check = check_gateway_health(verbose=args.verbose)
    checks.append(gw_check)
    if not gw_check["ok"]:
        # Gateway unreachable — warning, не fatal (runtime запустит его сам)
        warnings.append(f"gateway_health: {gw_check['detail']}")

    # Шаг 3: session integrity + repair
    sess_check = repair_session_integrity(
        session_path=args.session_path,
        check_only=args.check_only,
        verbose=args.verbose,
    )
    checks.append(sess_check)
    exit_78 = sess_check.get("exit_code_override") == 78
    if not sess_check["ok"]:
        if exit_78:
            errors.append(f"session_integrity: {sess_check['detail']}")
        else:
            errors.append(f"session_integrity: {sess_check['detail']}")

    # Шаг 4: stale plugins (report-only)
    plugin_check = check_stale_plugins(verbose=args.verbose)
    checks.append(plugin_check)
    if plugin_check.get("stale_plugins"):
        warnings.append(f"stale_plugins: {plugin_check['detail']}")

    duration_ms = int((time.monotonic() - t_start) * 1000)

    # Итоговый статус
    overall_ok = len(errors) == 0

    output = {
        "ok": overall_ok,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "duration_ms": duration_ms,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))

    # Exit codes
    if exit_78:
        return 78
    if not overall_ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
