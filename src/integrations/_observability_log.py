"""Wave 44-U: Krab agent runs observability log.

Каждый успешный/неуспешный CLI subprocess bypass run → JSONL line в
`~/.openclaw/krab_runtime_state/runs_history.jsonl`. Дашборд в Owner panel
читает это файл и показывает live + completed runs.

Best-effort: запись никогда не должна тормозить или ломать agent flow.
Также (best-effort) пытается зарегистрировать run в OpenClaw external
session register API — чтобы run появился в OpenClaw Sessions dashboard.

Ротация: при достижении 100MB файл переименуется в .1 и начинается новый.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

# Путь к JSONL-файлу с записями agent runs
RUNS_LOG = Path.home() / ".openclaw/krab_runtime_state/runs_history.jsonl"

# Максимальный размер в байтах перед ротацией (100 MB)
MAX_LOG_BYTES = 100 * 1024 * 1024

# OpenClaw gateway external session register endpoint (best-effort).
OPENCLAW_SESSIONS_URL = os.environ.get(
    "KRAB_OPENCLAW_SESSIONS_URL",
    "http://127.0.0.1:18789/api/sessions/external",
)


def _truncate(text: str | None, limit: int) -> str:
    if not text:
        return ""
    s = str(text)
    if len(s) <= limit:
        return s
    return s[:limit] + "…"


def _maybe_rotate() -> None:
    """Ротация файла при превышении MAX_LOG_BYTES."""
    try:
        if RUNS_LOG.exists() and RUNS_LOG.stat().st_size >= MAX_LOG_BYTES:
            backup = RUNS_LOG.with_suffix(RUNS_LOG.suffix + ".1")
            try:
                if backup.exists():
                    backup.unlink()
            except Exception:  # noqa: BLE001
                pass
            RUNS_LOG.rename(backup)
    except Exception:  # noqa: BLE001
        pass


def record_agent_run(
    *,
    request_id: str | None = None,
    user_id: int | str | None = None,
    chat_id: int | str | None = None,
    model: str = "",
    kind: str = "krab-bypass",
    prompt_text: str = "",
    response_text: str = "",
    started_at: float | None = None,
    completed_at: float | None = None,
    duration_sec: float = 0.0,
    status: str = "ok",
    exit_code: int | None = None,
    stderr_excerpt: str = "",
    tools_called: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Записывает agent run в jsonl + best-effort POST в OpenClaw register API.

    Returns:
        request_id (создаётся если None).

    Никогда не выбрасывает исключений: запись best-effort.
    """
    rid = request_id or uuid.uuid4().hex[:16]
    now = time.time()
    started = started_at if started_at is not None else now - duration_sec
    completed = completed_at if completed_at is not None else now

    record: dict[str, Any] = {
        "ts_started": started,
        "ts_completed": completed,
        "request_id": rid,
        "user_id": user_id,
        "chat_id": chat_id,
        "model": model,
        "kind": kind,
        "prompt_len": len(prompt_text or ""),
        "prompt_excerpt": _truncate(prompt_text, 200),
        "response_len": len(response_text or ""),
        "response_excerpt": _truncate(response_text, 500),
        "duration_sec": round(duration_sec, 3),
        "status": status,
        "exit_code": exit_code,
        "stderr_excerpt": _truncate(stderr_excerpt, 300) if stderr_excerpt else "",
        "tools_called": tools_called or [],
    }
    if extra:
        record.update(extra)

    try:
        RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
        _maybe_rotate()
        with RUNS_LOG.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass

    # Best-effort: register session в OpenClaw — никогда не блокирует.
    _try_register_openclaw(record)

    return rid


def _try_register_openclaw(record: dict[str, Any]) -> None:
    """POST run summary в OpenClaw `/api/sessions/external` (best-effort, sync).

    Если endpoint не существует или unreachable — silently skip. Используем
    короткий timeout (1.5s) чтобы не тормозить caller. Делаем синхронно через
    urllib чтобы не зависеть от async-контекста.
    """
    try:
        import urllib.error
        import urllib.request

        payload = {
            "key": f"krab-bypass:{record.get('request_id')}",
            "kind": record.get("kind") or "krab-bypass",
            "model": record.get("model") or "",
            "prompt_summary": record.get("prompt_excerpt") or "",
            "response_summary": record.get("response_excerpt") or "",
            "started_at": record.get("ts_started"),
            "completed_at": record.get("ts_completed"),
            "status": record.get("status") or "ok",
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            OPENCLAW_SESSIONS_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=1.5):  # noqa: S310
            pass
    except Exception:  # noqa: BLE001
        # Endpoint missing / network err / firewall — silently skip.
        pass


def read_runs(
    *,
    since_sec: int | None = None,
    limit: int = 200,
    status_filter: str | None = None,
    chat_id_filter: int | str | None = None,
    model_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Читает runs_history.jsonl и возвращает список runs (most recent first).

    Args:
        since_sec: окно — только runs за последние N секунд (None = все).
        limit: максимум записей (default 200).
        status_filter: только эти status (e.g. 'error').
        chat_id_filter: только runs с этим chat_id.
        model_filter: подстрока в model.
    """
    if not RUNS_LOG.exists():
        return []

    cutoff = (time.time() - since_sec) if since_sec else None
    out: list[dict[str, Any]] = []
    try:
        with RUNS_LOG.open() as f:
            lines = f.readlines()
    except Exception:  # noqa: BLE001
        return []

    # newest first
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if cutoff is not None and rec.get("ts_started", 0) < cutoff:
            continue
        if status_filter and rec.get("status") != status_filter:
            continue
        if chat_id_filter is not None and str(rec.get("chat_id") or "") != str(chat_id_filter):
            continue
        if model_filter and model_filter not in str(rec.get("model") or ""):
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def get_run(request_id: str) -> dict[str, Any] | None:
    """Возвращает full record по request_id или None."""
    if not RUNS_LOG.exists() or not request_id:
        return None
    try:
        with RUNS_LOG.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if rec.get("request_id") == request_id:
                    return rec
    except Exception:  # noqa: BLE001
        return None
    return None
