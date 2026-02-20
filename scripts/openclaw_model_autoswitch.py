#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenClaw Model Autoswitch.

Что это:
Утилита, которая синхронизирует default model OpenClaw с фактическим
состоянием LM Studio:
- если локальная модель загружена -> default = lmstudio/local;
- если локальная модель не загружена -> default = google/gemini-2.5-flash.

Зачем:
Для iMessage/WhatsApp/Signal каналов OpenClaw, где важно не отдавать
"400 No models loaded", а автоматически уходить в cloud без ручных действий.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen


def _lm_root() -> str:
    raw = str(os.getenv("LM_STUDIO_URL", "http://127.0.0.1:1234/v1")).strip().rstrip("/")
    if raw.endswith("/api/v1"):
        raw = raw[: -len("/api/v1")]
    elif raw.endswith("/v1"):
        raw = raw[: -len("/v1")]
    return raw.rstrip("/")


def _load_env_from_project() -> None:
    """
    Подгружает .env рядом с проектом, если переменные не выставлены в процессе.
    Нужно для запуска через GUI/.command, где env часто не наследуется.
    """
    project_root = Path(__file__).resolve().parents[1]
    env_path = project_root / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _fetch_json(url: str, timeout: float = 2.5) -> Any:
    req = Request(url=url, method="GET")
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except (URLError, HTTPError, TimeoutError, json.JSONDecodeError):
        return None


def _extract_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("models", "data", "result", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _model_id(entry: dict[str, Any]) -> str:
    for key in ("key", "id", "modelId", "identifier", "name"):
        value = entry.get(key)
        if value:
            return str(value).strip()
    return ""


def _is_loaded(entry: dict[str, Any]) -> bool:
    loaded_instances = entry.get("loaded_instances")
    if isinstance(loaded_instances, list) and len(loaded_instances) > 0:
        return True
    if isinstance(entry.get("loaded"), bool):
        return bool(entry.get("loaded"))
    states: list[str] = []
    for key in ("state", "status", "availability"):
        value = entry.get(key)
        if value is not None:
            states.append(str(value).strip().lower())
    for state in states:
        if state in {"ready", "loaded", "active", "running", "online"}:
            return True
        if state in {"unloaded", "not_loaded", "not loaded", "idle_unloaded", "evicted", "offline"}:
            return False
    return False


def _detect_lm_loaded() -> tuple[bool, str, str]:
    lms_bin = Path.home() / ".lmstudio" / "bin" / "lms"
    if lms_bin.exists():
        code, out, err = _run([str(lms_bin), "ps"])
        text = (out or err or "").strip()
        if code == 0:
            lowered = text.lower()
            if "no models are currently loaded" in lowered:
                return False, "", "lms_ps"
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            for line in lines:
                if line.lower().startswith("model"):
                    continue
                if "/" in line:
                    # Берем первый токен, похожий на model id.
                    model_id = line.split()[0].strip()
                    return True, model_id, "lms_ps"

    root = _lm_root()
    for endpoint in (f"{root}/api/v1/models", f"{root}/v1/models"):
        payload = _fetch_json(endpoint)
        entries = _extract_entries(payload)
        for item in entries:
            if _is_loaded(item):
                return True, _model_id(item), endpoint
    return False, "", ""


def _run(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _read_openclaw_models_status() -> dict[str, Any]:
    code, out, err = _run(["openclaw", "models", "status", "--json"])
    if code != 0:
        raise RuntimeError(f"openclaw models status failed: {err or out or code}")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid openclaw models status json: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("openclaw models status returned non-dict payload")
    return payload


def _normalize_model_id(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _set_default_model(model_id: str, dry_run: bool) -> bool:
    if dry_run:
        return True
    code, out, err = _run(["openclaw", "models", "set", model_id])
    if code != 0:
        raise RuntimeError(f"openclaw models set {model_id} failed: {err or out or code}")
    return True


def _set_fallbacks(fallbacks: list[str], dry_run: bool) -> bool:
    if dry_run:
        return True
    code, out, err = _run(["openclaw", "models", "fallbacks", "clear"])
    if code != 0:
        raise RuntimeError(f"openclaw fallbacks clear failed: {err or out or code}")
    for model_id in fallbacks:
        code, out, err = _run(["openclaw", "models", "fallbacks", "add", model_id])
        if code != 0:
            raise RuntimeError(f"openclaw fallback add {model_id} failed: {err or out or code}")
    return True


def _tick(args: argparse.Namespace) -> dict[str, Any]:
    lm_loaded, loaded_model_id, loaded_source = _detect_lm_loaded()
    status = _read_openclaw_models_status()

    current_default = _normalize_model_id(str(status.get("resolvedDefault") or status.get("defaultModel") or ""))
    current_fallbacks = [
        _normalize_model_id(item)
        for item in status.get("fallbacks", [])
        if _normalize_model_id(item)
    ]

    if lm_loaded:
        desired_default = args.local_default
        desired_fallbacks = [args.cloud_default, args.cloud_fallback]
    else:
        desired_default = args.cloud_default
        desired_fallbacks = [args.cloud_fallback, args.local_default]

    changed_default = current_default != desired_default
    changed_fallbacks = current_fallbacks != desired_fallbacks

    if changed_default:
        _set_default_model(desired_default, dry_run=args.dry_run)
    if changed_fallbacks:
        _set_fallbacks(desired_fallbacks, dry_run=args.dry_run)

    return {
        "ok": True,
        "lm_loaded": lm_loaded,
        "lm_loaded_model": loaded_model_id,
        "lm_probe_source": loaded_source,
        "current_default": current_default,
        "desired_default": desired_default,
        "current_fallbacks": current_fallbacks,
        "desired_fallbacks": desired_fallbacks,
        "changed_default": changed_default,
        "changed_fallbacks": changed_fallbacks,
        "dry_run": bool(args.dry_run),
        "applied": bool(changed_default or changed_fallbacks),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Autoswitch default model OpenClaw по состоянию LM Studio.")
    parser.add_argument("--watch", action="store_true", help="Циклический режим с интервалом.")
    parser.add_argument("--interval-sec", type=int, default=60, help="Интервал watch-цикла.")
    parser.add_argument("--dry-run", action="store_true", help="Только показать изменения, без применения.")
    parser.add_argument("--local-default", default="lmstudio/local", help="Default model, когда LM Studio loaded.")
    parser.add_argument("--cloud-default", default="google/gemini-2.5-flash", help="Default model, когда LM не loaded.")
    parser.add_argument("--cloud-fallback", default="openai/gpt-4o-mini", help="Cloud fallback model.")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    _load_env_from_project()

    if not shutil.which("openclaw"):
        print(json.dumps({"ok": False, "error": "openclaw_cli_not_found"}, ensure_ascii=False))
        return 1

    while True:
        try:
            payload = _tick(args)
            print(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            if not args.watch:
                return 1
        if not args.watch:
            return 0
        time.sleep(max(15, int(args.interval_sec)))


if __name__ == "__main__":
    import shutil

    raise SystemExit(main())
