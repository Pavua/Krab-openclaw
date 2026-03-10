#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Безопасная синхронизация canary-модели в runtime registry OpenClaw.

Зачем нужен отдельный скрипт:
- `compat_probe` принципиально read-only и не должен сам править runtime;
- autoswitch не должен invent-ить модели, которых нет в registry;
- для `GPT-5.4 first` нужен промежуточный этап: зарегистрировать кандидата
  в registry, но не трогать production primary/fallbacks до live-проверки.

Что делает:
1) Добавляет target-модель в provider catalog внутри:
   - `~/.openclaw/agents/main/agent/models.json`
   - `~/.openclaw/openclaw.json`
2) Не меняет текущий primary/fallback chain.
3) Пытается клонировать shape provider-entry из уже существующей модели того же
   провайдера, чтобы не гадать про поля compat/api/context.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_TARGET_MODEL = "openai-codex/gpt-5.4"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_model_key(provider: str, model: str) -> tuple[str, str]:
    provider_raw = str(provider or "").strip().lower()
    raw_model = str(model or "").strip()
    if not raw_model:
        return "", ""
    if "/" in raw_model:
        head, tail = raw_model.split("/", 1)
        return head.strip().lower(), tail.strip()
    return provider_raw, raw_model


def _friendly_name(provider: str, model_id: str) -> str:
    provider_raw = str(provider or "").strip().lower()
    model_raw = str(model_id or "").strip()
    if provider_raw == "openai-codex":
        return f"ChatGPT {model_raw.upper()}" if model_raw.startswith("gpt-") else f"Codex {model_raw}"
    if provider_raw == "openai":
        return f"OpenAI {model_raw}"
    return model_raw


def _build_model_entry(
    *,
    provider: str,
    model_id: str,
    reasoning: bool,
    template: dict[str, Any] | None,
) -> dict[str, Any]:
    source = dict(template or {})
    source["id"] = model_id
    source["name"] = str(source.get("name") or _friendly_name(provider, model_id))
    source["reasoning"] = bool(reasoning)
    if not isinstance(source.get("input"), list):
        source["input"] = ["text"]
    if not isinstance(source.get("cost"), dict):
        source["cost"] = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
    if int(source.get("contextWindow") or 0) <= 0:
        source["contextWindow"] = 128000
    if int(source.get("maxTokens") or 0) <= 0:
        source["maxTokens"] = 16384
    return source


def ensure_model_in_provider_catalog(
    payload: dict[str, Any],
    *,
    provider: str,
    model_id: str,
    reasoning: bool,
    provider_root: tuple[str, ...],
) -> dict[str, Any]:
    """
    Гарантирует наличие target-модели в provider catalog.

    provider_root:
    - для `models.json` это `("providers",)`
    - для `openclaw.json` это `("models", "providers")`
    """
    current: Any = payload
    for segment in provider_root:
        if not isinstance(current, dict):
            return {"changed": False, "reason": "provider_root_not_dict"}
        current = current.setdefault(segment, {})
    providers = current if isinstance(current, dict) else {}
    if not isinstance(providers, dict):
        return {"changed": False, "reason": "providers_not_dict"}

    provider_cfg = providers.get(provider)
    if not isinstance(provider_cfg, dict):
        provider_cfg = {}
        providers[provider] = provider_cfg

    models = provider_cfg.get("models")
    if not isinstance(models, list):
        models = []
        provider_cfg["models"] = models

    target_lower = model_id.strip().lower()
    for item in models:
        if isinstance(item, dict) and str(item.get("id") or "").strip().lower() == target_lower:
            changed = False
            if bool(item.get("reasoning", False)) != bool(reasoning):
                item["reasoning"] = bool(reasoning)
                changed = True
            return {
                "changed": changed,
                "reason": "already_present" if not changed else "reasoning_updated",
                "models_count": len(models),
            }

    template = next((item for item in models if isinstance(item, dict)), None)
    models.append(
        _build_model_entry(
            provider=provider,
            model_id=model_id,
            reasoning=reasoning,
            template=template,
        )
    )
    return {
        "changed": True,
        "reason": "added",
        "models_count": len(models),
    }


def sync_registry(
    *,
    target_model: str,
    reasoning: bool,
    models_json: Path,
    openclaw_json: Path,
) -> dict[str, Any]:
    provider, model_id = _normalize_model_key("", target_model)
    if not provider or not model_id:
        return {"ok": False, "error": "invalid_target_model"}

    models_payload = _read_json(models_json)
    openclaw_payload = _read_json(openclaw_json)

    models_report = ensure_model_in_provider_catalog(
        models_payload,
        provider=provider,
        model_id=model_id,
        reasoning=reasoning,
        provider_root=("providers",),
    )
    openclaw_report = ensure_model_in_provider_catalog(
        openclaw_payload,
        provider=provider,
        model_id=model_id,
        reasoning=reasoning,
        provider_root=("models", "providers"),
    )

    if models_report.get("changed"):
        _write_json(models_json, models_payload)
    if openclaw_report.get("changed"):
        _write_json(openclaw_json, openclaw_payload)

    return {
        "ok": True,
        "target_model": f"{provider}/{model_id}",
        "reasoning": bool(reasoning),
        "models_json": {
            "path": str(models_json),
            **models_report,
        },
        "openclaw_json": {
            "path": str(openclaw_json),
            **openclaw_report,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Добавляет canary-модель в runtime registry OpenClaw без изменения production routing."
    )
    parser.add_argument("--model", default=DEFAULT_TARGET_MODEL, help="Целевая модель, например openai-codex/gpt-5.4")
    parser.add_argument(
        "--reasoning",
        choices=("on", "off"),
        default="on",
        help="Какой reasoning-флаг записать в registry для target-модели.",
    )
    parser.add_argument(
        "--models-json",
        default=str(Path.home() / ".openclaw" / "agents" / "main" / "agent" / "models.json"),
    )
    parser.add_argument(
        "--openclaw-json",
        default=str(Path.home() / ".openclaw" / "openclaw.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = sync_registry(
        target_model=str(args.model or "").strip(),
        reasoning=str(args.reasoning or "on").strip().lower() == "on",
        models_json=Path(args.models_json).expanduser(),
        openclaw_json=Path(args.openclaw_json).expanduser(),
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
