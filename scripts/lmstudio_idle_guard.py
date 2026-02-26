#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Страховочный idle-guard для LM Studio.

Зачем:
- иногда авторазгрузка LM Studio (TTL) не срабатывает предсказуемо;
- guard не заменяет нативный TTL, а лишь подчищает "залипшие" модели,
  если сервер простаивает дольше порога.

Что делает:
1) читает список загруженных моделей LM Studio;
2) определяет последнюю активность по server-логам LM Studio;
3) если idle >= порога, делает POST /api/v1/models/unload (all=true).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts" / "ops"
DEFAULT_LOGS_DIR = Path.home() / ".lmstudio" / "server-logs"


@dataclass
class HttpResult:
    ok: bool
    status: int
    payload: Any
    error: str = ""


@dataclass
class UnloadAttempt:
    route: str
    payload: dict[str, Any]
    ok: bool
    status: int
    error: str = ""


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_lm_base_url(raw: str) -> str:
    base = (raw or "http://127.0.0.1:1234").strip().rstrip("/")
    for suffix in ("/v1", "/api/v1"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base


def _http_json(method: str, url: str, body: dict[str, Any] | None = None, timeout: float = 6.0) -> HttpResult:
    raw_data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        raw_data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(url=url, data=raw_data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            text = resp.read().decode("utf-8", errors="replace")
            status = int(getattr(resp, "status", 200) or 200)
    except urlerror.HTTPError as exc:
        text = ""
        try:
            text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return HttpResult(ok=False, status=int(exc.code or 0), payload=text[:320], error=f"http_{exc.code}")
    except Exception as exc:  # noqa: BLE001
        return HttpResult(ok=False, status=0, payload=None, error=str(exc) or exc.__class__.__name__)

    try:
        payload = json.loads(text) if text.strip() else {}
    except Exception:
        payload = text
    return HttpResult(ok=True, status=status, payload=payload)


def _extract_models(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("models", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [m for m in value if isinstance(m, dict)]
    if isinstance(payload, list):
        return [m for m in payload if isinstance(m, dict)]
    return []


def _is_loaded_model(entry: dict[str, Any]) -> bool:
    if bool(entry.get("loaded")):
        return True
    instances = entry.get("loaded_instances")
    if isinstance(instances, list) and len(instances) > 0:
        return True
    return False


def _extract_model_id(entry: dict[str, Any]) -> str:
    for key in ("id", "key", "model", "model_id", "modelId", "identifier", "name"):
        raw = entry.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return ""


def _extract_instance_ids(entry: dict[str, Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    raw_instances = entry.get("loaded_instances")
    candidates: list[Any] = []
    if isinstance(raw_instances, list):
        candidates.extend(raw_instances)
    elif isinstance(raw_instances, dict):
        candidates.extend(raw_instances.values())

    for item in candidates:
        if isinstance(item, dict):
            for key in ("instance_id", "instanceId", "id", "instanceReference", "instance_reference", "identifier"):
                raw = item.get(key)
                if raw is None:
                    continue
                text = str(raw).strip()
                if text and text not in seen:
                    seen.add(text)
                    result.append(text)
        else:
            text = str(item).strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
    return result


def _build_unload_attempts(loaded_models: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    # Порядок важен: сначала пробуем самый короткий путь "выгрузить всё",
    # потом деградируем к instance/model точечным payload для разных API-диалектов.
    attempts: list[tuple[str, dict[str, Any]]] = [("all", {"all": True})]

    seen_payload: set[str] = set()
    model_ids: list[str] = []
    instance_ids: list[str] = []
    for model in loaded_models:
        model_id = _extract_model_id(model)
        if model_id:
            model_ids.append(model_id)
        instance_ids.extend(_extract_instance_ids(model))

    for instance_id in instance_ids:
        for key in ("instance_id", "instanceId", "instanceReference"):
            payload = {key: instance_id}
            marker = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if marker in seen_payload:
                continue
            seen_payload.add(marker)
            attempts.append((f"instance:{key}", payload))

    for model_id in model_ids:
        for key in ("model", "model_id", "modelId", "id"):
            payload = {key: model_id}
            marker = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if marker in seen_payload:
                continue
            seen_payload.add(marker)
            attempts.append((f"model:{key}", payload))

    return attempts


def _is_unload_success(res: HttpResult) -> bool:
    if not res.ok or res.status not in {200, 201, 202, 204}:
        return False
    if isinstance(res.payload, dict):
        if res.payload.get("ok") is False:
            return False
        if str(res.payload.get("status") or "").lower() in {"error", "failed"}:
            return False
    return True


def _run_http_unload(base: str, loaded_models: list[dict[str, Any]]) -> tuple[bool, str, list[UnloadAttempt]]:
    attempts_report: list[UnloadAttempt] = []
    attempts = _build_unload_attempts(loaded_models)
    endpoint = f"{base}/api/v1/models/unload"
    for route, payload in attempts:
        res = _http_json("POST", endpoint, body=payload, timeout=10)
        ok = _is_unload_success(res)
        attempts_report.append(
            UnloadAttempt(route=route, payload=payload, ok=ok, status=res.status, error=res.error)
        )
        if ok:
            return True, "", attempts_report
    if attempts_report:
        last = attempts_report[-1]
        return False, f"{last.route}:{last.error or ('status=' + str(last.status))}", attempts_report
    return False, "no_unload_attempts", attempts_report


def _run_cli_unload_all() -> tuple[bool, str]:
    lms_path = Path.home() / ".lmstudio" / "bin" / "lms"
    if not lms_path.exists():
        return False, "lms_cli_not_found"
    try:
        proc = subprocess.run(
            [str(lms_path), "unload", "--all"],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"lms_cli_exception:{exc}"

    if proc.returncode == 0:
        return True, ""
    err = (proc.stderr or proc.stdout or "").strip()
    return False, f"lms_cli_failed:{proc.returncode}:{err[:200]}"


def _parse_log_ts(line: str) -> datetime | None:
    match = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})", line.strip())
    if not match:
        return None
    try:
        # Логи LM Studio локальные; для расчёта idle достаточно текущей локальной зоны.
        local_dt = datetime.fromisoformat(f"{match.group(1)}T{match.group(2)}")
        return local_dt.astimezone()
    except Exception:
        return None


def _last_activity_from_logs(log_dir: Path) -> datetime | None:
    if not log_dir.exists():
        return None

    files = sorted(
        [p for p in log_dir.glob("*.log") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return None

    markers = (
        "POST to /v1/chat/completions",
        "Running chat completion",
        "Finished streaming response",
        "Prompt processing progress",
    )

    for path in files[:6]:
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for raw in reversed(lines[-8000:]):
            if not any(marker in raw for marker in markers):
                continue
            ts = _parse_log_ts(raw)
            if ts is not None:
                return ts

    # fallback: хотя бы mtime последнего log-файла
    try:
        return datetime.fromtimestamp(files[0].stat().st_mtime).astimezone()
    except Exception:
        return None


def _minutes_between(a: datetime, b: datetime) -> float:
    return max(0.0, (a - b).total_seconds() / 60.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Страховочный idle-unload guard для LM Studio.")
    parser.add_argument("--lm-url", default="", help="LM Studio URL (по умолчанию LM_STUDIO_URL или http://127.0.0.1:1234).")
    parser.add_argument("--logs-dir", default=str(DEFAULT_LOGS_DIR), help="Папка server-логов LM Studio.")
    parser.add_argument("--max-idle-minutes", type=float, default=45.0, help="Порог idle для принудительной выгрузки.")
    parser.add_argument("--dry-run", action="store_true", help="Только диагностика, без выгрузки.")
    parser.add_argument("--force", action="store_true", help="Выгрузить сразу, если есть загруженные модели.")
    args = parser.parse_args()

    lm_raw = (args.lm_url or "").strip() or str(os.getenv("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    base = _normalize_lm_base_url(lm_raw)

    models_res = _http_json("GET", f"{base}/api/v1/models", timeout=5)
    if not models_res.ok:
        models_res = _http_json("GET", f"{base}/v1/models", timeout=5)

    if not models_res.ok:
        print(f"❌ Не удалось получить список моделей LM Studio: {models_res.error}")
        return 1

    models = _extract_models(models_res.payload)
    loaded = [m for m in models if _is_loaded_model(m)]

    logs_dir = Path(args.logs_dir).expanduser()
    last_activity = _last_activity_from_logs(logs_dir)
    now_local = datetime.now().astimezone()
    idle_minutes = _minutes_between(now_local, last_activity) if last_activity else None

    should_unload = False
    reason = ""
    if loaded:
        if args.force:
            should_unload = True
            reason = "force=true"
        elif idle_minutes is not None and idle_minutes >= float(args.max_idle_minutes):
            should_unload = True
            reason = f"idle={idle_minutes:.1f}m >= {args.max_idle_minutes:.1f}m"
        elif idle_minutes is None:
            reason = "last_activity_unknown"
        else:
            reason = f"idle={idle_minutes:.1f}m < {args.max_idle_minutes:.1f}m"

    unloaded = False
    unload_error = ""
    unload_mode = ""
    unload_attempts_payload: list[dict[str, Any]] = []
    if should_unload and not args.dry_run:
        unloaded, unload_error, attempts_report = _run_http_unload(base, loaded)
        unload_attempts_payload = [
            {
                "route": item.route,
                "payload": item.payload,
                "ok": item.ok,
                "status": item.status,
                "error": item.error,
            }
            for item in attempts_report
        ]
        if unloaded:
            unload_mode = "http"
        else:
            cli_ok, cli_err = _run_cli_unload_all()
            unloaded = cli_ok
            unload_mode = "cli" if cli_ok else ""
            if not cli_ok:
                unload_error = f"{unload_error}; {cli_err}".strip("; ").strip()

    report = {
        "ok": (not should_unload) or args.dry_run or unloaded,
        "generated_at": _now_utc().isoformat(timespec="seconds"),
        "lm_base_url": base,
        "loaded_models_count": len(loaded),
        "loaded_model_ids": [str(m.get("id") or "") for m in loaded],
        "last_activity": last_activity.isoformat() if last_activity else None,
        "idle_minutes": round(float(idle_minutes), 3) if idle_minutes is not None else None,
        "max_idle_minutes": float(args.max_idle_minutes),
        "should_unload": bool(should_unload),
        "dry_run": bool(args.dry_run),
        "unloaded": bool(unloaded),
        "unload_mode": unload_mode,
        "unload_attempts": unload_attempts_payload,
        "reason": reason,
        "unload_error": unload_error,
    }

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _now_utc().strftime("%Y%m%d_%H%M%SZ")
    report_path = ARTIFACTS_DIR / f"lmstudio_idle_guard_{stamp}.json"
    latest_path = ARTIFACTS_DIR / "lmstudio_idle_guard_latest.json"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    report_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")

    print("🧊 LM Studio Idle Guard")
    print(f"- loaded_models: {len(loaded)}")
    print(f"- last_activity: {report['last_activity']}")
    print(f"- idle_minutes: {report['idle_minutes']}")
    print(f"- should_unload: {should_unload} ({reason})")
    print(f"- dry_run: {args.dry_run}")
    print(f"- unloaded: {unloaded}")
    print(f"- report: {report_path}")

    if should_unload and not args.dry_run and not unloaded:
        print(f"❌ Не удалось выгрузить модели: {unload_error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
