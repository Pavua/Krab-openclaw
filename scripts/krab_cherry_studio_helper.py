#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wave 242: Cherry Studio helper для Краба.

Что делает:
- `--list-all-models`: опрашивает LM Studio (:1234/v1/models),
  MLX RotorQuant (:8088/v1/models) и OpenClaw (:18789/v1/models),
  выводит единую таблицу всех моделей.
- `--export-cherry-config`: формирует Cherry Studio-friendly JSON
  снипет с 4 провайдерами (MCP Gateway, MLX Direct, LM Studio Sync,
  OpenClaw Smart). Юзер копирует в Cherry Studio → Add Custom Provider.
- `--save-to <path>`: сохраняет JSON по указанному пути; по умолчанию
  ~/.openclaw/krab_runtime_state/cherry_studio_config.json.

Принципы:
- НЕ трогаем `openclaw.json` / plist / `archive.db` / `web_app.py` /
  собственные настройки Cherry Studio. Только read-only опрос +
  запись reference-JSON в свою папку `krab_runtime_state`.
- httpx с короткими таймаутами (1.5 сек); если backend оффлайн —
  помечается как `offline`, скрипт не падает.

Использование:
    python3 scripts/krab_cherry_studio_helper.py --list-all-models
    python3 scripts/krab_cherry_studio_helper.py --export-cherry-config
    python3 scripts/krab_cherry_studio_helper.py --export-cherry-config \
        --save-to /tmp/cherry.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - окружение всегда содержит httpx
    httpx = None  # type: ignore[assignment]


# --- Константы конфигурации ----------------------------------------------------

LM_STUDIO_URL = "http://127.0.0.1:1234/v1"
MLX_DIRECT_URL = "http://127.0.0.1:8088/v1"
OPENCLAW_URL = "http://127.0.0.1:18789/v1"
KRAB_MCP_SSE_URL = "http://127.0.0.1:8080/api/mcp/sse"

# Дефолтный путь для сохранения reference-конфига
DEFAULT_SAVE_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "cherry_studio_config.json"


# --- Утилиты опроса backend'ов ------------------------------------------------


def _fetch_models(
    base_url: str,
    *,
    bearer: str | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout: float = 1.5,
) -> tuple[list[str], str | None]:
    """Запросить /models у OpenAI-compatible backend.

    Возвращает (models, error). Если error не None — backend оффлайн/ошибка.
    """
    if httpx is None:
        return [], "httpx_not_installed"
    headers: dict[str, str] = {}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if extra_headers:
        headers.update(extra_headers)
    try:
        resp = httpx.get(f"{base_url}/models", headers=headers, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - любые сетевые ошибки = offline
        return [], f"offline:{type(exc).__name__}"
    if resp.status_code != 200:
        return [], f"http_{resp.status_code}"
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        return [], "invalid_json"
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return [], "no_data_array"
    ids: list[str] = []
    for item in items:
        if isinstance(item, dict):
            mid = item.get("id")
            if isinstance(mid, str) and mid:
                ids.append(mid)
    return ids, None


def discover_all_backends(
    *,
    lm_studio_token: str | None = None,
    openclaw_token: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Опросить все 3 backend'a, вернуть словарь {backend: {url, models, error}}."""
    lm_models, lm_err = _fetch_models(LM_STUDIO_URL, bearer=lm_studio_token)
    mlx_models, mlx_err = _fetch_models(MLX_DIRECT_URL)
    oc_models, oc_err = _fetch_models(
        OPENCLAW_URL,
        bearer=openclaw_token,
        extra_headers={"x-openclaw-scopes": "operator.write"} if openclaw_token else None,
    )
    return {
        "lm_studio": {"url": LM_STUDIO_URL, "models": lm_models, "error": lm_err},
        "mlx_direct": {"url": MLX_DIRECT_URL, "models": mlx_models, "error": mlx_err},
        "openclaw": {"url": OPENCLAW_URL, "models": oc_models, "error": oc_err},
    }


# --- Форматирование вывода ----------------------------------------------------


def format_models_table(snapshot: dict[str, dict[str, Any]]) -> str:
    """Сформировать человекочитаемую таблицу из snapshot'a discover'a."""
    lines: list[str] = []
    total = 0
    for name, info in snapshot.items():
        models = info.get("models") or []
        err = info.get("error")
        url = info.get("url", "?")
        header = f"[{name}] {url}"
        if err:
            lines.append(f"{header}  -- {err}")
            continue
        lines.append(f"{header}  -- {len(models)} models")
        total += len(models)
        for mid in models:
            lines.append(f"  - {mid}")
    lines.append("")
    lines.append(f"TOTAL: {total} models across {len(snapshot)} backends")
    return "\n".join(lines)


# --- Cherry Studio JSON snippet -----------------------------------------------


def build_cherry_config(
    snapshot: dict[str, dict[str, Any]],
    *,
    openclaw_token: str | None = None,
    lm_studio_token: str | None = None,
) -> dict[str, Any]:
    """Собрать Cherry Studio-friendly JSON снипет с 4 провайдерами.

    Юзер копирует providers[] секцию в Cherry Studio → Settings →
    Model Service → Add Custom Provider (для каждого provider'a отдельно).
    """
    lm_models = snapshot.get("lm_studio", {}).get("models") or []
    mlx_models = snapshot.get("mlx_direct", {}).get("models") or []

    providers: list[dict[str, Any]] = []

    # 1. Krab MCP Gateway — SSE endpoint для MCP tools (не chat)
    providers.append(
        {
            "name": "Krab MCP Gateway",
            "type": "mcp_sse",  # информативно; Cherry Studio MCP UI отдельно
            "url": KRAB_MCP_SSE_URL,
            "auth": "bearer",
            "note": "MCP SSE endpoint; добавляется в Cherry Studio → MCP Servers, не в Model Service",
        }
    )

    # 2. MLX Direct — быстрый локальный inference
    providers.append(
        {
            "name": "MLX Direct",
            "type": "openai_compatible",
            "api_url": MLX_DIRECT_URL,
            "api_key": "",
            "models": mlx_models
            or ["/Volumes/4TB SSD/LMStudio_models/mlx-community/gemma-4-26B-A4B-it-OptiQ-4bit"],
            "note": "RotorQuant на :8088; пустой api_key; путь к модели — id",
        }
    )

    # 3. LM Studio Sync — Cherry Studio умеет sync button если есть /v1/models
    providers.append(
        {
            "name": "LM Studio Sync",
            "type": "openai_compatible",
            "api_url": LM_STUDIO_URL,
            "api_key": lm_studio_token or "$LM_STUDIO_API_KEY",
            "models": lm_models,
            "sync_supported": True,
            "note": "Нажми 'Sync' в Cherry Studio после добавления — подтянет все модели из LM Studio",
        }
    )

    # 4. OpenClaw Smart — routed endpoint с tools
    providers.append(
        {
            "name": "OpenClaw Smart",
            "type": "openai_compatible",
            "api_url": OPENCLAW_URL,
            "api_key": openclaw_token or "$OPENCLAW_TOKEN",
            "extra_headers": {"x-openclaw-scopes": "operator.write"},
            "models": ["openclaw/main", "openclaw/default"],
            "note": "Routed через gateway; tools (MCP/RAG/web_search) работают",
        }
    )

    return {
        "schema": "krab-cherry-studio-config/v1",
        "generated_by": "scripts/krab_cherry_studio_helper.py",
        "providers": providers,
    }


# --- I/O ----------------------------------------------------------------------


def save_config(config: dict[str, Any], path: Path) -> None:
    """Сохранить JSON в указанный path; создать parent dirs при необходимости."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


# --- CLI ----------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="krab_cherry_studio_helper",
        description="Cherry Studio config helper для Краба (Wave 242)",
    )
    parser.add_argument(
        "--list-all-models",
        action="store_true",
        help="Опросить все 3 backend'a и вывести таблицу моделей",
    )
    parser.add_argument(
        "--export-cherry-config",
        action="store_true",
        help="Сформировать JSON снипет для Cherry Studio",
    )
    parser.add_argument(
        "--save-to",
        type=str,
        default=None,
        help=f"Путь сохранения JSON (по умолчанию {DEFAULT_SAVE_PATH})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not (args.list_all_models or args.export_cherry_config):
        parser.print_help()
        return 0

    lm_token = os.getenv("LM_STUDIO_API_KEY") or None
    oc_token = os.getenv("OPENCLAW_GATEWAY_TOKEN") or os.getenv("OPENCLAW_TOKEN") or None

    snapshot = discover_all_backends(lm_studio_token=lm_token, openclaw_token=oc_token)

    if args.list_all_models:
        print(format_models_table(snapshot))

    if args.export_cherry_config:
        cfg = build_cherry_config(snapshot, openclaw_token=oc_token, lm_studio_token=lm_token)
        save_path = Path(args.save_to) if args.save_to else DEFAULT_SAVE_PATH
        save_config(cfg, save_path)
        print(f"\nCherry Studio config saved → {save_path}")
        print(json.dumps(cfg, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
