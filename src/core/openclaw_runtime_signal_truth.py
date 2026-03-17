# -*- coding: utf-8 -*-
"""
Live source-of-truth для сигналов OpenClaw runtime.

Зачем нужен:
1) Один только `HTTP 200` от `/v1/chat/completions` не гарантирует, что
   ответ действительно пришёл от requested primary-модели: OpenClaw может
   тихо пересадить запрос на fallback-провайдера.
2) Для canary/promote-скриптов важно читать те же runtime-сигналы, которые
   уже использует web/runtime truth: свежий gateway log и session index.
3) Helper централизует выбор актуального log-файла и разбор live fallback,
   чтобы не плодить несогласованную диагностику в нескольких скриптах.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_GATEWAY_SIGNAL_LOOKBACK_SEC = 2 * 60 * 60
DEFAULT_RUNTIME_SESSIONS_PATH = Path.home() / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"

MODEL_FALLBACK_LOG_RE = re.compile(
    r'^(?P<ts>\S+)\s+\[model-fallback\]\s+Model "(?P<requested>[^"]+)"[\s\S]*?Fell back to "(?P<fallback>[^"]+)"\.',
    re.IGNORECASE,
)
EMBEDDED_SESSION_LANE_ERROR_RE = re.compile(
    r'^(?P<ts>\S+)\s+\[diagnostic\]\s+lane task error:\s+lane=session:agent:main:openai:(?P<session>[a-z0-9-]+)\s+durationMs=\d+\s+error="(?P<error>.+)"$',
    re.IGNORECASE,
)
RUNTIME_AUTH_SCOPE_MARKERS = (
    "missing scopes: model.request",
    "insufficient permissions for this operation",
)
RUNTIME_AUTH_PROVIDER_HINTS = {
    "lane=session:agent:main:openai:": "openai-codex",
}


def _unique_existing_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for item in paths:
        raw = Path(item).expanduser()
        key = str(raw)
        if key in seen:
            continue
        seen.add(key)
        if raw.exists() and raw.is_file():
            out.append(raw)
    return out


def _session_updated_at_epoch(session_meta: dict[str, Any]) -> float:
    """Нормализует `updatedAt`/`lastUpdatedAt` из sessions.json в epoch seconds."""
    for key in ("updatedAt", "lastUpdatedAt"):
        value = session_meta.get(key)
        if value in (None, ""):
            continue
        try:
            raw = float(value)
        except (TypeError, ValueError):
            continue
        # OpenClaw обычно хранит updatedAt в миллисекундах.
        if raw > 10_000_000_000:
            return raw / 1000.0
        return raw
    return 0.0


def model_matches(model_key: str, candidate: str) -> bool:
    """Сравнивает модель по full key и по хвосту после provider-префикса."""
    left = str(model_key or "").strip().lower()
    right = str(candidate or "").strip().lower()
    if not left or not right:
        return False
    if left == right:
        return True
    left_tail = left.split("/", 1)[1] if "/" in left else left
    right_tail = right.split("/", 1)[1] if "/" in right else right
    return left_tail == right_tail


def provider_from_model(model_key: str) -> str:
    raw = str(model_key or "").strip().lower()
    if "/" not in raw:
        return ""
    return raw.split("/", 1)[0].strip()


def compose_session_runtime_model(session_meta: dict[str, Any]) -> str:
    """
    Склеивает provider/model из sessions.json обратно в canonical full model id.
    """
    if not isinstance(session_meta, dict):
        return ""
    provider = str(session_meta.get("modelProvider") or "").strip()
    model = str(session_meta.get("model") or "").strip()
    if not model:
        return ""
    if "/" in model:
        return model
    if provider:
        return f"{provider}/{model}"
    return model


def parse_gateway_log_epoch(line: str) -> float | None:
    """Извлекает ISO timestamp из начала строки gateway-log."""
    raw = str(line or "").strip()
    if not raw:
        return None
    match = re.match(r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))", raw)
    if not match:
        return None
    ts = str(match.group("ts") or "").strip()
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def recent_gateway_log_lines(
    gateway_log_path: Path,
    *,
    max_age_sec: int = DEFAULT_GATEWAY_SIGNAL_LOOKBACK_SEC,
    now_epoch: float | None = None,
) -> list[str]:
    """
    Возвращает только свежие строки signal-log.

    Строки без parseable timestamp сохраняем как conservative fallback: это
    лучше, чем потерять live-сигнал из нестандартного launcher-формата.
    """
    if not gateway_log_path.exists():
        return []
    try:
        lines = gateway_log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []

    cutoff = float(now_epoch if now_epoch is not None else time.time()) - max(0, int(max_age_sec or 0))
    out: list[str] = []
    for line in lines[-800:]:
        epoch = parse_gateway_log_epoch(line)
        if epoch is None or epoch >= cutoff:
            out.append(line)
    return out


def discover_gateway_signal_log(
    *,
    preferred_path: Path | None = None,
    repo_root: Path | None = None,
) -> Path:
    """
    Выбирает наиболее живой log-файл для runtime truth.

    Порядок кандидатов:
    1) явно переданный `preferred_path`,
    2) launcher-capture `repo_root/openclaw.log`,
    3) самый свежий `/tmp/openclaw/openclaw-*.log`,
    4) legacy `~/.openclaw/logs/gateway.err.log`.

    Если существует несколько файлов, берём самый свежий по `mtime`, потому что
    именно он обычно содержит текущий lifecycle после последнего restart.
    """
    candidates: list[Path] = []
    if preferred_path is not None:
        candidates.append(Path(preferred_path).expanduser())
    if repo_root is not None:
        candidates.append(Path(repo_root).expanduser() / "openclaw.log")

    tmp_root = Path("/tmp/openclaw")
    if tmp_root.exists():
        latest_tmp_logs = sorted(tmp_root.glob("openclaw-*.log"))
        if latest_tmp_logs:
            candidates.append(latest_tmp_logs[-1])

    candidates.append(Path.home() / ".openclaw" / "logs" / "gateway.err.log")
    existing = _unique_existing_paths(candidates)
    if not existing:
        return Path(preferred_path).expanduser() if preferred_path is not None else Path()
    return max(existing, key=lambda path: (path.stat().st_mtime, path.stat().st_size))


def broken_models_from_signal_log(gateway_log_path: Path) -> list[str]:
    """Извлекает свежие модели, которые runtime уже пометил как `not found`."""
    out: list[str] = []
    for line in recent_gateway_log_lines(gateway_log_path):
        for candidate in re.findall(r'Model "([^"]+)" not found', line):
            raw = str(candidate or "").strip()
            if raw:
                out.append(raw)
        for candidate in re.findall(r"model `([^`]+)` does not exist", line, flags=re.IGNORECASE):
            raw = str(candidate or "").strip()
            if raw:
                out.append(raw)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in out:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def runtime_auth_failed_providers_from_signal_log(gateway_log_path: Path) -> dict[str, str]:
    """
    Извлекает провайдеров, которые сейчас стабильно падают по auth/scopes.
    """
    disabled: dict[str, str] = {}
    for line in recent_gateway_log_lines(gateway_log_path):
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


def _read_sessions_payload(sessions_path: Path | None) -> dict[str, Any]:
    raw_path = Path(sessions_path).expanduser() if sessions_path is not None else DEFAULT_RUNTIME_SESSIONS_PATH
    if not raw_path.exists():
        return {}
    try:
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_probe_runtime_truth(
    *,
    requested_model: str,
    request_started_at: float,
    gateway_log_path: Path,
    sessions_path: Path | None = None,
) -> dict[str, Any]:
    """
    Определяет, не ушёл ли probe-запрос на скрытый fallback после `HTTP 200`.

    Возвращает requested/effective model и краткую причину, чтобы canary/promote
    не принимали silent fallback за успешный primary.
    """
    normalized_requested = str(requested_model or "").strip()
    result = {
        "requested_model": normalized_requested,
        "effective_model": normalized_requested,
        "fallback_detected": False,
        "auth_scope_failure": False,
        "auth_failed_provider": "",
        "reason": "requested_model_confirmed",
        "signal_log_path": str(gateway_log_path or ""),
        "session_key": "",
        "error_excerpt": "",
    }
    if not normalized_requested or not gateway_log_path or not gateway_log_path.exists():
        result["reason"] = "signal_log_unavailable"
        return result

    recent_lines = recent_gateway_log_lines(gateway_log_path)
    cutoff = float(request_started_at) - 2.0
    for raw_line in reversed(recent_lines):
        line = str(raw_line or "").strip()
        if not line:
            continue
        match = MODEL_FALLBACK_LOG_RE.match(line)
        if not match:
            continue
        requested = str(match.group("requested") or "").strip()
        if not model_matches(normalized_requested, requested):
            continue
        event_ts = parse_gateway_log_epoch(line) or 0.0
        if event_ts and event_ts < cutoff:
            continue
        fallback_model = str(match.group("fallback") or "").strip()
        if not fallback_model or model_matches(normalized_requested, fallback_model):
            continue
        result.update(
            {
                "effective_model": fallback_model,
                "fallback_detected": True,
                "reason": "model_fallback_log",
            }
        )
        return result

    sessions_payload = _read_sessions_payload(sessions_path)
    if not sessions_payload:
        result["reason"] = "sessions_index_unavailable"
        return result

    for raw_line in reversed(recent_lines):
        line = str(raw_line or "").strip()
        if not line:
            continue
        match = EMBEDDED_SESSION_LANE_ERROR_RE.match(line)
        if not match:
            continue
        event_ts = parse_gateway_log_epoch(line) or 0.0
        if event_ts and event_ts < cutoff:
            continue

        session_key = f"agent:main:openai:{str(match.group('session') or '').strip()}"
        session_meta = sessions_payload.get(session_key)
        if not isinstance(session_meta, dict):
            continue
        session_epoch = _session_updated_at_epoch(session_meta)
        if session_epoch and session_epoch < cutoff:
            continue

        resolved_model = compose_session_runtime_model(session_meta)
        if not resolved_model or model_matches(normalized_requested, resolved_model):
            continue

        error_excerpt = str(match.group("error") or "").strip()
        lowered = error_excerpt.lower()
        auth_scope_failure = all(marker in lowered for marker in RUNTIME_AUTH_SCOPE_MARKERS)
        result.update(
            {
                "effective_model": resolved_model,
                "fallback_detected": True,
                "auth_scope_failure": auth_scope_failure,
                "auth_failed_provider": provider_from_model(normalized_requested) if auth_scope_failure else "",
                "reason": "session_runtime_fallback",
                "session_key": session_key,
                "error_excerpt": error_excerpt[:500],
            }
        )
        return result

    return result
