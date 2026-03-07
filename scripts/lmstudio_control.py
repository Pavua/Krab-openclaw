#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Утилита управления LM Studio для проекта Краб.

Зачем нужна:
- даёт предсказуемый CLI-слой поверх публичного HTTP API LM Studio;
- позволяет безопасно смотреть статус, загружать модель, выгружать все модели
  и применять базовые глобальные настройки без ручного кликанья по UI;
- используется `.command`-обёртками, чтобы пользователь мог запускать операции
  двойным кликом на macOS.

Что здесь принципиально не делаем:
- не пытаемся хрупко автоматизировать GUI-переключатели правой панели;
- не лезем в приватные внутренние форматы LM Studio, если можно использовать
  публичный API или понятный JSON-файл настроек.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_LM_STUDIO_URL = "http://127.0.0.1:1234"
DEFAULT_CONTEXT_LENGTH = 32184
DEFAULT_JIT_TTL_SECONDS = 3600
SETTINGS_PATH = Path.home() / "Library" / "Application Support" / "LM Studio" / "settings.json"


@dataclass(slots=True)
class HttpResult:
    """Результат HTTP-вызова к LM Studio."""

    ok: bool
    status: int
    payload: Any
    error: str = ""


def _normalize_base_url(raw_url: str) -> str:
    """Приводит LM Studio URL к базовому виду без `/v1` и `/api/v1`."""
    url = (raw_url or "").strip() or os.getenv("LM_STUDIO_URL", "").strip() or DEFAULT_LM_STUDIO_URL
    url = url.rstrip("/")
    for suffix in ("/api/v1", "/v1"):
        if url.endswith(suffix):
            return url[: -len(suffix)]
    return url


def _http_json(method: str, url: str, body: dict[str, Any] | None = None, timeout: float = 10.0) -> HttpResult:
    """Выполняет JSON-запрос и возвращает нормализованный результат."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(raw) if raw.strip() else None
            return HttpResult(ok=200 <= resp.status < 300, status=resp.status, payload=payload)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw.strip() else {"raw": raw}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return HttpResult(ok=False, status=exc.code, payload=payload, error=f"http_{exc.code}")
    except urllib.error.URLError as exc:
        return HttpResult(ok=False, status=0, payload=None, error=str(exc.reason))


def _fetch_models(base_url: str) -> HttpResult:
    """Читает список моделей через v1 API с совместимым fallback."""
    for path in ("/api/v1/models", "/v1/models"):
        result = _http_json("GET", f"{base_url}{path}")
        if result.ok:
            return result
    return result


def _extract_models(payload: Any) -> list[dict[str, Any]]:
    """Нормализует разные варианты ответов LM Studio в список моделей."""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        models = payload.get("models")
        if isinstance(models, list):
            return [item for item in models if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _extract_loaded_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Возвращает только реально загруженные модели/инстансы."""
    loaded: list[dict[str, Any]] = []
    for item in models:
        loaded_instances = item.get("loaded_instances")
        if isinstance(loaded_instances, list) and loaded_instances:
            loaded.append(item)
            continue
        if item.get("loaded") is True:
            loaded.append(item)
    return loaded


def _build_unload_attempts(models: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """Собирает каскад безопасных payload для unload всех моделей."""
    attempts: list[tuple[str, dict[str, Any]]] = [("all", {"all": True})]
    for item in models:
        for instance in item.get("loaded_instances") or []:
            if isinstance(instance, dict):
                instance_id = instance.get("instance_id") or instance.get("instanceReference")
                if instance_id:
                    attempts.append(("instance", {"instance_id": str(instance_id)}))
        model_id = item.get("id") or item.get("key") or item.get("model")
        if model_id:
            attempts.append(("model", {"model": str(model_id)}))
    return attempts


def unload_all_models(base_url: str) -> tuple[bool, list[dict[str, Any]], list[tuple[str, dict[str, Any], HttpResult]]]:
    """Пытается выгрузить все загруженные модели через каскад payload."""
    models_result = _fetch_models(base_url)
    if not models_result.ok:
        return False, [], [("models", {}, models_result)]
    loaded = _extract_loaded_models(_extract_models(models_result.payload))
    if not loaded:
        return True, [], []

    attempts_log: list[tuple[str, dict[str, Any], HttpResult]] = []
    endpoints = [f"{base_url}/api/v1/models/unload", f"{base_url}/v1/models/unload"]
    for endpoint in endpoints:
        for label, payload in _build_unload_attempts(loaded):
            result = _http_json("POST", endpoint, payload, timeout=30.0)
            attempts_log.append((label, payload, result))
            if result.ok and label == "all":
                return True, loaded, attempts_log
        # Если дошли сюда, возможно часть моделей уже выгрузили поинстансно.
        refreshed = _fetch_models(base_url)
        if refreshed.ok and not _extract_loaded_models(_extract_models(refreshed.payload)):
            return True, loaded, attempts_log
    return False, loaded, attempts_log


def load_model(base_url: str, model: str, ttl: int) -> list[tuple[str, dict[str, Any], HttpResult]]:
    """Пытается загрузить модель через основной и legacy-эндпоинты."""
    attempts: list[tuple[str, dict[str, Any], HttpResult]] = []
    payloads = [
        ("v1", f"{base_url}/api/v1/models/load", {"model": model}),
        ("legacy", f"{base_url}/v1/models/load", {"model": model, "ttl": ttl}),
    ]
    for label, endpoint, payload in payloads:
        result = _http_json("POST", endpoint, payload, timeout=600.0)
        attempts.append((label, payload, result))
        if result.ok:
            return attempts
    return attempts


def _load_settings(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    """Читает настройки LM Studio с диска."""
    if not path.exists():
        raise FileNotFoundError(f"Не найден файл настроек LM Studio: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def apply_defaults(settings: dict[str, Any], context_length: int, ttl_seconds: int) -> dict[str, Any]:
    """Применяет безопасные дефолты к глобальным настройкам LM Studio."""
    updated = json.loads(json.dumps(settings))
    updated["defaultContextLength"] = {"type": "max", "value": int(context_length)}

    developer = updated.setdefault("developer", {})
    jit_ttl = developer.setdefault("jitModelTTL", {})
    jit_ttl["enabled"] = int(ttl_seconds) > 0
    jit_ttl["ttlSeconds"] = int(ttl_seconds)

    # Оставляем явную конфигурацию параметров перед загрузкой, чтобы пользователь
    # видел, с чем реально стартует модель, даже если defaultContextLength изменён.
    ui = updated.setdefault("ui", {})
    ui["configureLoadParamsBeforeLoad"] = True

    return updated


def _write_settings(settings: dict[str, Any], path: Path = SETTINGS_PATH) -> Path:
    """Сохраняет настройки и делает рядом backup, чтобы откат был простым."""
    backup_path = path.with_suffix(path.suffix + ".bak")
    if path.exists():
        shutil.copy2(path, backup_path)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return backup_path


def format_status_report(base_url: str, settings: dict[str, Any], models_payload: Any) -> str:
    """Собирает компактный человекочитаемый отчёт."""
    models = _extract_models(models_payload)
    loaded = _extract_loaded_models(models)
    lines = [
        "🧠 LM Studio Status",
        f"- URL: {base_url}",
        f"- settings.json: {SETTINGS_PATH}",
        f"- defaultContextLength: {settings.get('defaultContextLength', {}).get('value', 'n/a')}",
        f"- jitModelTTL.enabled: {settings.get('developer', {}).get('jitModelTTL', {}).get('enabled', 'n/a')}",
        f"- jitModelTTL.ttlSeconds: {settings.get('developer', {}).get('jitModelTTL', {}).get('ttlSeconds', 'n/a')}",
        f"- total models visible: {len(models)}",
        f"- loaded models: {len(loaded)}",
    ]
    for item in loaded:
        model_id = item.get("id") or item.get("key") or item.get("model") or "<unknown>"
        instances = item.get("loaded_instances") or []
        lines.append(f"  - {model_id} (instances: {len(instances) if isinstance(instances, list) else 1})")
    return "\n".join(lines)


def _cmd_status(args: argparse.Namespace) -> int:
    base_url = _normalize_base_url(args.lm_url)
    settings = _load_settings()
    models_result = _fetch_models(base_url)
    if not models_result.ok:
        print(f"❌ Не удалось получить список моделей: {models_result.error} (status={models_result.status})")
        return 2
    print(format_status_report(base_url, settings, models_result.payload))
    return 0


def _cmd_set_defaults(args: argparse.Namespace) -> int:
    settings = _load_settings()
    updated = apply_defaults(settings, context_length=args.context, ttl_seconds=args.ttl_seconds)
    backup = _write_settings(updated)
    print("✅ Базовые настройки LM Studio обновлены.")
    print(f"- context: {args.context}")
    print(f"- jit ttl: {args.ttl_seconds}")
    print(f"- backup: {backup}")
    return 0


def _cmd_load(args: argparse.Namespace) -> int:
    base_url = _normalize_base_url(args.lm_url)
    attempts = load_model(base_url, args.model, args.ttl)
    for label, payload, result in attempts:
        print(f"- {label}: status={result.status} ok={result.ok} payload={payload}")
        if result.ok:
            print(f"✅ Модель загружена: {args.model}")
            return 0
    print(f"❌ Не удалось загрузить модель: {args.model}")
    return 3


def _cmd_unload_all(args: argparse.Namespace) -> int:
    base_url = _normalize_base_url(args.lm_url)
    ok, loaded, attempts = unload_all_models(base_url)
    if not loaded:
        print("ℹ️ Загруженных моделей нет.")
        return 0
    for label, payload, result in attempts:
        print(f"- {label}: status={result.status} ok={result.ok} payload={payload}")
    if ok:
        print("✅ Все модели выгружены.")
        return 0
    print("❌ Не удалось гарантированно выгрузить все модели.")
    return 4


def build_parser() -> argparse.ArgumentParser:
    """Строит CLI-парсер."""
    parser = argparse.ArgumentParser(description="Управление LM Studio для проекта Краб.")
    parser.add_argument("--lm-url", default="", help="Базовый URL LM Studio, например http://127.0.0.1:1234")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Показать статус LM Studio и глобальные defaults.")
    status.set_defaults(func=_cmd_status)

    set_defaults = subparsers.add_parser("set-defaults", help="Записать безопасные defaults в settings.json.")
    set_defaults.add_argument("--context", type=int, default=DEFAULT_CONTEXT_LENGTH, help="Новый default context length.")
    set_defaults.add_argument(
        "--ttl-seconds",
        type=int,
        default=DEFAULT_JIT_TTL_SECONDS,
        help="TTL для developer.jitModelTTL.ttlSeconds.",
    )
    set_defaults.set_defaults(func=_cmd_set_defaults)

    load = subparsers.add_parser("load", help="Загрузить модель в LM Studio.")
    load.add_argument("--model", required=True, help="ID модели, например nvidia/nemotron-3-nano")
    load.add_argument("--ttl", type=int, default=-1, help="TTL для legacy /v1/models/load")
    load.set_defaults(func=_cmd_load)

    unload_all = subparsers.add_parser("unload-all", help="Выгрузить все модели из LM Studio.")
    unload_all.set_defaults(func=_cmd_unload_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
