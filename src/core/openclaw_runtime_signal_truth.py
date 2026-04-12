# -*- coding: utf-8 -*-
"""
Live source-of-truth для сигналов OpenClaw runtime.

Зачем нужен:
1) Один только `HTTP 200` от `/v1/chat/completions` не является гарантией того,
   что запрос пошёл к нужной модели — gateway может тихо упасть на fallback.
2) Этот модуль читает gateway-логи и sessions.json, чтобы выявить silent fallbacks
   и auth-ошибки до того, как они дойдут до владельца через симптом «плохой ответ».
"""

from __future__ import annotations

import glob
import json
import re
import time
from pathlib import Path
from typing import Any

_LOG_TIMESTAMP_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))"
)
_MODEL_FALLBACK_RE = re.compile(
    r'^(?P<ts>\S+)\s+\[model-fallback\]\s+Model "(?P<requested>[^"]+)"[\s\S]*?Fell back to "(?P<fallback>[^"]+)"\.'
)
_LANE_TASK_ERROR_RE = re.compile(
    r"^(?P<ts>\S+)\s+\[diagnostic\]\s+lane task error:\s+lane=session:agent:main:openai:(?P<session>[a-z0-9-]+)\s+durationMs="
)


def _unique_existing_paths(paths: list[Path | None]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for p in paths:
        if p and p.exists() and p not in seen:
            seen.add(p)
            result.append(p)
    return result


def _session_updated_at_epoch(session_meta: dict[str, Any]) -> float:
    """Нормализует `updatedAt`/`lastUpdatedAt` из sessions.json в epoch seconds."""
    for key in ("updatedAt", "lastUpdatedAt", "updated_at"):
        raw = session_meta.get(key)
        if raw is None:
            continue
        if isinstance(raw, (int, float)):
            val = float(raw)
            # Millis vs seconds heuristic
            if val > 10_000_000_000:
                return val / 1000.0
            return val
        if isinstance(raw, str):
            try:
                import datetime

                dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
                return dt.timestamp()
            except Exception:
                pass
    return 0.0


def model_matches(model_key: str, candidate: str) -> bool:
    """Сравнивает модель по full key и по хвосту после provider-префикса."""
    if model_key == candidate:
        return True
    parts = candidate.split("/", 1)
    if len(parts) == 2 and parts[1] == model_key:
        return True
    return False


def provider_from_model(model_key: str) -> str:
    parts = model_key.split("/", 1)
    return parts[0] if len(parts) == 2 else ""


def compose_session_runtime_model(session_meta: dict[str, Any]) -> str:
    """
    Склеивает provider/model из sessions.json обратно в canonical full model id.
    """
    provider = str(session_meta.get("modelProvider") or "")
    model = str(session_meta.get("model") or "")
    if provider and model:
        return f"{provider}/{model}"
    return model or provider


def parse_gateway_log_epoch(line: str) -> float | None:
    """Извлекает ISO timestamp из начала строки gateway-log."""
    m = _LOG_TIMESTAMP_RE.match(line)
    if not m:
        return None
    try:
        import datetime

        ts = m.group("ts")
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return None


def recent_gateway_log_lines(
    gateway_log_path: Path,
    *,
    max_age_sec: float = 3600.0,
    tail_lines: int = 800,
) -> list[str]:
    """
    Возвращает только свежие строки signal-log.

    Строки без parseable timestamp сохраняем как conservative fallback: это
    лучше, чем потерять live-сигнал из нестандартного launcher-формата.
    """
    try:
        text = gateway_log_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    lines = text.splitlines()
    lines = lines[-tail_lines:] if len(lines) > tail_lines else lines
    if max_age_sec <= 0:
        return lines
    cutoff = time.time() - max_age_sec
    result: list[str] = []
    for line in lines:
        epoch = parse_gateway_log_epoch(line)
        if epoch is None or epoch >= cutoff:
            result.append(line)
    return result


def discover_gateway_signal_log(
    *,
    preferred_path: Path | None = None,
    repo_root: Path | None = None,
) -> Path | None:
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

    if preferred_path:
        candidates.append(preferred_path)

    if repo_root:
        candidates.append(repo_root / "openclaw.log")

    # /tmp/openclaw/openclaw-*.log
    tmp_logs = sorted(
        (Path(p) for p in glob.glob("/tmp/openclaw/openclaw-*.log")),
        key=lambda p: p.stat().st_mtime if p.exists() else -1,
        reverse=True,
    )
    candidates.extend(tmp_logs)

    candidates.append(Path.home() / ".openclaw" / "logs" / "gateway.err.log")

    existing = _unique_existing_paths(candidates)
    if not existing:
        return None

    # Pick freshest by mtime
    return max(existing, key=lambda p: p.stat().st_mtime)


def broken_models_from_signal_log(gateway_log_path: Path) -> set[str]:
    """Извлекает свежие модели, которые runtime уже пометил как `not found`."""
    if not gateway_log_path or not gateway_log_path.exists():
        return set()
    lines = recent_gateway_log_lines(gateway_log_path)
    broken: set[str] = set()
    not_found_re = re.compile(r'Model "([^"]+)" not found')
    not_exist_re = re.compile(r"model `([^`]+)` does not exist")
    for line in lines:
        for m in not_found_re.finditer(line):
            broken.add(m.group(1))
        for m in not_exist_re.finditer(line):
            broken.add(m.group(1))
    return broken


def runtime_auth_failed_providers_from_signal_log(
    gateway_log_path: Path | None,
) -> set[str]:
    """
    Извлекает провайдеров, которые сейчас стабильно падают по auth/scopes.
    """
    if not gateway_log_path or not gateway_log_path.exists():
        return set()
    lines = recent_gateway_log_lines(gateway_log_path)
    failed: set[str] = set()
    scope_re = re.compile(r"runtime_missing_scope_model_request\s+model=([^\s]+)")
    for line in lines:
        for m in scope_re.finditer(line):
            provider = provider_from_model(m.group(1))
            if provider:
                failed.add(provider)
    return failed


def _read_sessions_payload(sessions_path: Path) -> list[dict[str, Any]]:
    try:
        raw = sessions_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return list(data.values())
    except Exception:
        pass
    return []


def resolve_probe_runtime_truth(
    *,
    requested_model: str,
    sessions_path: Path | None = None,
    gateway_log_path: Path | None = None,
    max_age_sec: float = 2.0,
    response_model: str | None = None,
) -> dict[str, Any]:
    """
    Определяет, не ушёл ли probe-запрос на скрытый fallback после `HTTP 200`.

    Возвращает requested/effective model и краткую причину, чтобы canary/promote
    не принимали silent fallback за успешный primary.
    """
    result: dict[str, Any] = {
        "requested": requested_model,
        "fallback": None,
        "reason": "requested_model_confirmed",
        "model_fallback_log": None,
        "session": None,
        "error": None,
    }

    # Check response model header if available
    if response_model and not model_matches(requested_model, response_model):
        result["fallback"] = response_model
        result["reason"] = "session_runtime_fallback"
        return result

    # Check sessions.json
    if sessions_path and sessions_path.exists():
        try:
            sessions = _read_sessions_payload(sessions_path)
            cutoff = time.time() - max_age_sec * 500
            for sess in sessions:
                updated = _session_updated_at_epoch(sess)
                if updated < cutoff:
                    continue
                # Check if this session's model differs from requested
                lane = str(sess.get("id") or "")
                if "agent:main:openai:" in lane:
                    runtime_model = compose_session_runtime_model(sess)
                    if runtime_model and not model_matches(requested_model, runtime_model):
                        result["fallback"] = runtime_model
                        result["session"] = lane.split("agent:main:openai:")[-1]
                        result["reason"] = "session_runtime_fallback"
                        return result
        except Exception as exc:
            result["error"] = str(exc)
            result["reason"] = "sessions_index_unavailable"

    # Check gateway log for fallback events
    if gateway_log_path:
        try:
            lines = recent_gateway_log_lines(gateway_log_path, max_age_sec=max_age_sec * 500)
            for line in reversed(lines):
                m = _MODEL_FALLBACK_RE.match(line)
                if m and model_matches(requested_model, m.group("requested")):
                    result["fallback"] = m.group("fallback")
                    result["model_fallback_log"] = line
                    result["reason"] = "model_fallback_log"
                    return result
        except Exception:
            pass

    result["reason"] = (
        "signal_log_unavailable" if not gateway_log_path else "requested_model_confirmed"
    )
    return result
