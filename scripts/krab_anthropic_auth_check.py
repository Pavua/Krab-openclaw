#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 55-B: Проверка авторизации и доступности anthropic-vertex моделей.

Скрипт делает три вещи:
1) Проверяет gcloud application-default access-token (валидность ADC).
2) Читает anthropic-vertex модели из ~/.openclaw/agents/main/agent/models.json.
3) Делает test-call через AnthropicVertex SDK к каждой модели.

Итог — JSON на stdout:
  {
    "gcloud_token_valid": true,
    "project": "caramel-anvil-492816-t5",
    "region": "us-east5",
    "models": [
      {"id": "anthropic-vertex/claude-sonnet-4-6", "auth_ok": true, "latency_ms": 1234, "error": null},
      ...
    ],
    "all_ok": true
  }

Exit 0 если all_ok, 1 при любом сбое.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Путь к runtime models.json (переопределяется в тестах)
_MODELS_JSON_PATH: Path = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "models.json"

# Дефолтные параметры Vertex (совпадают с anthropic_vertex_direct.py)
_DEFAULT_PROJECT = "caramel-anvil-492816-t5"
_DEFAULT_REGION = "us-east5"

# Тестовый prompt — минимальный, не вызывает лишних токенов
_PROBE_PROMPT = "Reply with the single word: OK"
_PROBE_MAX_TOKENS = 16


def check_gcloud_token() -> bool:
    """Проверяет валидность ADC через gcloud print-access-token.

    Returns:
        True если токен получен без ошибок (не пустой).
    """
    try:
        result = subprocess.run(
            ["gcloud", "auth", "application-default", "print-access-token"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        token = result.stdout.strip()
        return result.returncode == 0 and bool(token) and len(token) > 20
    except Exception:
        return False


def get_anthropic_vertex_models(models_path: Path | None = None) -> list[str]:
    """Читает список anthropic-vertex моделей из models.json.

    Args:
        models_path: путь к файлу (по умолчанию _MODELS_JSON_PATH).

    Returns:
        Список строк вида 'anthropic-vertex/claude-sonnet-4-6'.
        Пустой список если провайдер не найден или файл недоступен.
    """
    path = models_path or _MODELS_JSON_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    provider = data.get("providers", {}).get("anthropic-vertex", {})
    models_raw: list[dict[str, Any]] = provider.get("models", [])
    return [f"anthropic-vertex/{m['id']}" for m in models_raw if m.get("id")]


def _probe_model_sync(
    bare_model: str,
    *,
    project: str,
    region: str,
) -> tuple[bool, float, str | None]:
    """Sync probe одной модели через AnthropicVertex SDK.

    Returns:
        (auth_ok, latency_ms, error_str | None)
    """
    try:
        from anthropic import AnthropicVertex  # noqa: PLC0415
    except ImportError:
        return False, 0.0, "anthropic[vertex] SDK не установлен"

    t0 = time.monotonic()
    try:
        client = AnthropicVertex(region=region, project_id=project)
        resp = client.messages.create(
            model=bare_model,
            max_tokens=_PROBE_MAX_TOKENS,
            messages=[{"role": "user", "content": _PROBE_PROMPT}],
        )
        latency_ms = (time.monotonic() - t0) * 1000
        # Проверяем что ответ не пустой
        if resp.content and resp.content[0].text:
            return True, latency_ms, None
        return False, latency_ms, "Пустой ответ от модели"
    except Exception as exc:
        latency_ms = (time.monotonic() - t0) * 1000
        return False, latency_ms, str(exc)[:300]


async def probe_model_async(
    model_id: str,
    *,
    project: str,
    region: str,
) -> dict[str, Any]:
    """Async wrapper для probe одной модели.

    Args:
        model_id: полное имя модели ('anthropic-vertex/claude-sonnet-4-6').
        project: GCP project ID.
        region: Vertex AI region.

    Returns:
        Словарь {"id": str, "auth_ok": bool, "latency_ms": float, "error": str|None}.
    """
    bare = model_id.removeprefix("anthropic-vertex/")
    auth_ok, latency_ms, error = await asyncio.to_thread(
        _probe_model_sync, bare, project=project, region=region
    )
    return {
        "id": model_id,
        "auth_ok": auth_ok,
        "latency_ms": round(latency_ms, 1),
        "error": error,
    }


async def run_check(
    *,
    models_path: Path | None = None,
    project: str | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """Основная проверка: ADC + пробинг каждой модели.

    Args:
        models_path: переопределение пути к models.json (для тестов).
        project: GCP project override.
        region: Vertex AI region override.

    Returns:
        Итоговый dict с ключами gcloud_token_valid, models, all_ok.
    """
    proj = project or os.environ.get("KRAB_ANTHROPIC_VERTEX_PROJECT") or _DEFAULT_PROJECT
    reg = region or os.environ.get("KRAB_ANTHROPIC_VERTEX_REGION") or _DEFAULT_REGION

    # 1) Проверка gcloud ADC токена
    token_valid = await asyncio.to_thread(check_gcloud_token)

    # 2) Список моделей из runtime конфига
    model_ids = get_anthropic_vertex_models(models_path)

    # 3) Probe каждой модели (последовательно — не спамим Vertex)
    model_results: list[dict[str, Any]] = []
    if token_valid and model_ids:
        for mid in model_ids:
            result = await probe_model_async(mid, project=proj, region=reg)
            model_results.append(result)
    elif not token_valid:
        # Токен невалиден — нет смысла делать SDK-вызовы
        for mid in model_ids:
            model_results.append(
                {
                    "id": mid,
                    "auth_ok": False,
                    "latency_ms": 0.0,
                    "error": "gcloud ADC токен невалиден или истёк",
                }
            )
    else:
        # Нет моделей в конфиге — ничего пробировать
        pass

    all_ok = token_valid and bool(model_results) and all(r["auth_ok"] for r in model_results)

    return {
        "gcloud_token_valid": token_valid,
        "project": proj,
        "region": reg,
        "models": model_results,
        "all_ok": all_ok,
    }


def format_check_result(data: dict[str, Any]) -> str:
    """Форматирует результат проверки в читаемый Telegram-текст.

    Args:
        data: результат run_check().

    Returns:
        Строка с эмодзи-статусами для каждой модели.
    """
    lines: list[str] = ["🔐 **Anthropic Vertex Auth Check**", "─────────────────"]

    token_icon = "✅" if data.get("gcloud_token_valid") else "❌"
    lines.append(
        f"{token_icon} gcloud ADC токен: {'valid' if data.get('gcloud_token_valid') else 'expired/missing'}"
    )
    lines.append(f"  project: {data.get('project', 'unknown')}")
    lines.append(f"  region: {data.get('region', 'unknown')}")

    models = data.get("models", [])
    if not models:
        lines.append("⚠️ Модели не найдены в models.json")
    else:
        lines.append("─────────────────")
        for m in models:
            icon = "✅" if m.get("auth_ok") else "❌"
            lat = m.get("latency_ms", 0)
            err = m.get("error")
            model_name = m.get("id", "unknown")
            if m.get("auth_ok"):
                lines.append(f"{icon} {model_name} ({lat:.0f}ms)")
            else:
                lines.append(f"{icon} {model_name}: {err}")

    lines.append("─────────────────")
    all_ok = data.get("all_ok", False)
    lines.append("✅ Всё OK" if all_ok else "❌ Есть сбои — см. детали выше")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Krab anthropic-vertex auth check")
    parser.add_argument("--json", action="store_true", help="Вывести raw JSON вместо текста")
    parser.add_argument(
        "--project",
        default=None,
        help=f"GCP project ID (default: {_DEFAULT_PROJECT})",
    )
    parser.add_argument(
        "--region",
        default=None,
        help=f"Vertex region (default: {_DEFAULT_REGION})",
    )
    args = parser.parse_args()

    result = asyncio.run(run_check(project=args.project, region=args.region))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_check_result(result))

    sys.exit(0 if result.get("all_ok") else 1)
