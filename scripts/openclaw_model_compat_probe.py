#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Read-only compatibility probe для target-модели OpenClaw.

Зачем нужен:
1) У Краба целевой primary = `GPT-5.4`, но одной видимости модели в Codex
   недостаточно: нужно отдельно подтвердить, что именно runtime OpenClaw
   знает эту модель, умеет авторизоваться и реально отвечает через gateway.
2) Promotion в production нельзя делать "на веру". Этот probe даёт честный
   статус `READY / DEGRADED / BLOCKED` без записи в runtime-конфиг.
3) Probe повторно использует уже существующий gateway-контракт
   `/v1/chat/completions`, чтобы не плодить отдельные самодельные каналы.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv


DEFAULT_TARGET_PRIMARY_MODEL = "openai-codex/gpt-5.4"
DEFAULT_GATEWAY_BASE_URL = "http://127.0.0.1:18789"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _unique_non_empty(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        raw = str(item or "").strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        out.append(raw)
    return out


def _normalize_model_key(provider: str, model_id: str, known_providers: set[str] | None = None) -> str:
    provider_raw = str(provider or "").strip().lower()
    model_raw = str(model_id or "").strip()
    if not model_raw:
        return ""
    if model_raw.startswith(f"{provider_raw}/"):
        return model_raw
    if "/" in model_raw:
        first = model_raw.split("/", 1)[0].strip().lower()
        if known_providers and first in known_providers:
            return model_raw
    if provider_raw:
        return f"{provider_raw}/{model_raw}"
    return model_raw


def _model_matches(model_key: str, candidate: str) -> bool:
    left = str(model_key or "").strip().lower()
    right = str(candidate or "").strip().lower()
    if not left or not right:
        return False
    if left == right:
        return True
    left_tail = left.split("/", 1)[1] if "/" in left else left
    right_tail = right.split("/", 1)[1] if "/" in right else right
    return left_tail == right_tail


def _runtime_registry_models(runtime_models_payload: dict[str, Any]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    providers = runtime_models_payload.get("providers") if isinstance(runtime_models_payload, dict) else {}
    if not isinstance(providers, dict):
        return [], {}

    known_providers = set(str(key or "").strip().lower() for key in providers.keys() if str(key or "").strip())
    models: list[str] = []
    meta: dict[str, dict[str, Any]] = {}
    for provider_name, provider_cfg in providers.items():
        model_items = provider_cfg.get("models") if isinstance(provider_cfg, dict) else None
        if not isinstance(model_items, list):
            continue
        for item in model_items:
            if not isinstance(item, dict):
                continue
            normalized = _normalize_model_key(str(provider_name or ""), str(item.get("id") or ""), known_providers)
            if not normalized:
                continue
            models.append(normalized)
            meta[normalized] = dict(item)
    return _unique_non_empty(models), meta


def _matching_auth_entries(auth_payload: dict[str, Any], provider: str) -> tuple[list[tuple[str, dict[str, Any]]], list[tuple[str, dict[str, Any]]]]:
    provider_raw = str(provider or "").strip().lower()
    profiles_raw = auth_payload.get("profiles") if isinstance(auth_payload.get("profiles"), dict) else {}
    usage_raw = auth_payload.get("usageStats") if isinstance(auth_payload.get("usageStats"), dict) else {}

    matching_profiles: list[tuple[str, dict[str, Any]]] = []
    for profile_key, profile_value in profiles_raw.items():
        if not isinstance(profile_value, dict):
            continue
        profile_provider = str(profile_value.get("provider") or "").strip().lower()
        if profile_provider == provider_raw or str(profile_key).strip().lower().startswith(f"{provider_raw}:"):
            matching_profiles.append((str(profile_key), profile_value))

    matching_usage: list[tuple[str, dict[str, Any]]] = []
    for usage_key, usage_value in usage_raw.items():
        if not isinstance(usage_value, dict):
            continue
        if str(usage_key).strip().lower().startswith(f"{provider_raw}:"):
            matching_usage.append((str(usage_key), usage_value))

    return matching_profiles, matching_usage


def _provider_health(auth_payload: dict[str, Any], provider: str) -> dict[str, Any]:
    profiles, usage_entries = _matching_auth_entries(auth_payload, provider)
    disabled_reason = ""
    disabled_until = 0
    failure_counts: dict[str, int] = {}
    error_count = 0

    for _, usage in usage_entries:
        reason = str(usage.get("disabledReason") or "").strip()
        if reason and not disabled_reason:
            disabled_reason = reason
        disabled_until = max(disabled_until, int(usage.get("disabledUntil") or 0))
        error_count = max(error_count, int(usage.get("errorCount") or 0))
        raw_failures = usage.get("failureCounts")
        if isinstance(raw_failures, dict):
            for key, value in raw_failures.items():
                name = str(key or "").strip()
                if not name:
                    continue
                failure_counts[name] = failure_counts.get(name, 0) + int(value or 0)

    return {
        "provider": str(provider or "").strip().lower(),
        "has_profile": bool(profiles),
        "profiles": [key for key, _ in profiles],
        "disabled": bool(disabled_reason),
        "disabled_reason": disabled_reason,
        "disabled_until": disabled_until,
        "failure_counts": failure_counts,
        "error_count": error_count,
    }


def _broken_models_from_gateway_log(gateway_log_path: Path) -> list[str]:
    if not gateway_log_path.exists():
        return []
    try:
        lines = gateway_log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    out: list[str] = []
    for line in lines[-400:]:
        for candidate in re.findall(r'Model "([^"]+)" not found', line):
            out.append(str(candidate).strip())
        for candidate in re.findall(r"model `([^`]+)` does not exist", line, flags=re.IGNORECASE):
            out.append(str(candidate).strip())
    return _unique_non_empty(out)


def _read_runtime_gateway_token(openclaw_payload: dict[str, Any]) -> str:
    """
    Забирает реальный gateway token из runtime-конфига.
    """
    if not isinstance(openclaw_payload, dict):
        return ""
    for path in (
        ("gateway", "auth", "token"),
        ("gateway", "token"),
        ("auth", "token"),
    ):
        current: Any = openclaw_payload
        for segment in path:
            current = current.get(segment) if isinstance(current, dict) else None
        token = str(current or "").strip()
        if token:
            return token
    return ""


async def _probe_gateway_non_invasive(base_url: str, token: str, model: str) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {
        "model": model,
        "messages": [],
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            response = await client.post(f"{base_url.rstrip('/')}/v1/chat/completions", json=payload)
        body = response.text[:1200]
        content_type = str(response.headers.get("content-type", "") or "").lower()
        if response.status_code in {401, 403}:
            return {"ok": False, "status": response.status_code, "error": "auth_error", "body": body}
        if "application/json" not in content_type:
            return {"ok": False, "status": response.status_code, "error": "non_json_endpoint", "body": body}
        try:
            data = response.json()
        except Exception as exc:
            return {"ok": False, "status": response.status_code, "error": f"json_decode_error:{exc}", "body": body}
        err = data.get("error") if isinstance(data, dict) else {}
        err_type = str(err.get("type") or "").strip()
        err_msg = str(err.get("message") or "").strip()
        if response.status_code == 400 and err_type == "invalid_request_error":
            return {"ok": True, "status": 400, "error": "ok_controlled_400", "body": err_msg[:500]}
        return {"ok": False, "status": response.status_code, "error": f"unexpected_response:{err_type or 'unknown'}", "body": body}
    except Exception as exc:
        return {"ok": False, "status": 0, "error": "network_error", "body": str(exc)}


async def _probe_gateway_chat(
    base_url: str,
    token: str,
    model: str,
    *,
    reasoning: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": "Ответь ровно словом ping без пояснений.",
            }
        ],
        "stream": False,
        "max_tokens": max(1, int(max_output_tokens or 32)),
        "reasoning": str(reasoning or "off").strip().lower() or "off",
    }
    try:
        async with httpx.AsyncClient(timeout=25.0, headers=headers) as client:
            response = await client.post(f"{base_url.rstrip('/')}/v1/chat/completions", json=payload)
        body = response.text[:1200]
        if response.status_code != 200:
            return {"ok": False, "status": response.status_code, "error": "http_error", "body": body}
        try:
            data = response.json()
        except Exception as exc:
            return {"ok": False, "status": response.status_code, "error": f"json_decode_error:{exc}", "body": body}
        choices = data.get("choices") if isinstance(data, dict) else []
        message = (choices[0].get("message") or {}) if isinstance(choices, list) and choices else {}
        text = str(message.get("content") or "").strip()
        usage = data.get("usage") if isinstance(data, dict) else {}
        stats = data.get("stats") if isinstance(data, dict) else {}
        return {
            "ok": bool(text),
            "status": 200,
            "error": "" if text else "empty_message",
            "assistant_text": text[:500],
            "usage": usage if isinstance(usage, dict) else {},
            "stats": stats if isinstance(stats, dict) else {},
        }
    except Exception as exc:
        return {"ok": False, "status": 0, "error": "network_error", "body": str(exc)}


async def main_async(
    *,
    model: str,
    reasoning: str,
    skip_reasoning: bool,
    openclaw_json: Path,
    models_json: Path,
    auth_profiles_json: Path,
    gateway_log: Path,
    base_url: str,
) -> dict[str, Any]:
    runtime_config = _read_json(openclaw_json)
    runtime_models = _read_json(models_json)
    auth_profiles = _read_json(auth_profiles_json)
    registry_models, registry_meta = _runtime_registry_models(runtime_models)
    broken_models = _broken_models_from_gateway_log(gateway_log)

    target_model = str(model or DEFAULT_TARGET_PRIMARY_MODEL).strip()
    target_provider = target_model.split("/", 1)[0].strip().lower() if "/" in target_model else ""
    provider_health = _provider_health(auth_profiles, target_provider) if target_provider else {}
    registry_entry_key = next((item for item in registry_models if _model_matches(target_model, item)), "")
    registry_entry = registry_meta.get(registry_entry_key, {})

    blocked_reason = ""
    if not registry_entry_key:
        blocked_reason = "target_model_not_in_runtime_registry"
    elif provider_health.get("disabled"):
        blocked_reason = "target_provider_disabled"
    elif any(_model_matches(target_model, item) for item in broken_models):
        blocked_reason = "target_model_marked_broken"

    token = _read_runtime_gateway_token(runtime_config) or str(
        os.getenv("OPENCLAW_GATEWAY_TOKEN", os.getenv("OPENCLAW_TOKEN", os.getenv("OPENCLAW_API_KEY", ""))) or ""
    ).strip()
    base = str(base_url or os.getenv("OPENCLAW_BASE_URL", DEFAULT_GATEWAY_BASE_URL) or DEFAULT_GATEWAY_BASE_URL).strip()

    result: dict[str, Any] = {
        "status": "BLOCKED" if blocked_reason else "DEGRADED",
        "target_model": target_model,
        "target_provider": target_provider,
        "target_in_runtime_registry": bool(registry_entry_key),
        "runtime_registry_model": registry_entry_key,
        "runtime_registry_reasoning": bool(registry_entry.get("reasoning", False)),
        "provider_health": provider_health,
        "broken_models": broken_models,
        "gateway_base_url": base,
        "token_present": bool(token),
        "checked_at_epoch": int(time.time()),
    }

    if blocked_reason:
        result.update(
            {
                "ok": False,
                "status": "BLOCKED",
                "reason": blocked_reason,
                "non_invasive_probe": {"ok": None, "status": "skipped", "error": "blocked_before_probe"},
                "chat_probe": {"ok": None, "status": "skipped", "error": "blocked_before_probe"},
                "reasoning_probe": {"ok": None, "status": "skipped", "error": "blocked_before_probe"},
                "promotion_ready": False,
            }
        )
        return result

    non_invasive_probe = await _probe_gateway_non_invasive(base, token, target_model)
    chat_probe = await _probe_gateway_chat(
        base,
        token,
        target_model,
        reasoning="off",
        max_output_tokens=32,
    )

    if skip_reasoning:
        reasoning_probe = {"ok": None, "status": "skipped", "error": "skip_reasoning_requested"}
    else:
        reasoning_probe = await _probe_gateway_chat(
            base,
            token,
            target_model,
            reasoning=str(reasoning or "high").strip().lower() or "high",
            max_output_tokens=64,
        )

    reasoning_ok = bool(reasoning_probe.get("ok")) if not skip_reasoning else True
    ready = bool(non_invasive_probe.get("ok")) and bool(chat_probe.get("ok")) and reasoning_ok

    result.update(
        {
            "ok": ready,
            "status": "READY" if ready else "DEGRADED",
            "reason": "compat_probe_passed" if ready else "compat_probe_failed",
            "non_invasive_probe": non_invasive_probe,
            "chat_probe": chat_probe,
            "reasoning_probe": reasoning_probe,
            "promotion_ready": ready,
        }
    )
    return result


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Read-only compatibility probe для target-модели OpenClaw")
    parser.add_argument("--model", default=str(os.getenv("OPENCLAW_TARGET_PRIMARY_MODEL", DEFAULT_TARGET_PRIMARY_MODEL) or DEFAULT_TARGET_PRIMARY_MODEL))
    parser.add_argument("--reasoning", default="high", help="Какой reasoning-режим проверять на втором шаге.")
    parser.add_argument("--skip-reasoning", action="store_true", help="Пропустить reasoning probe и проверить только базовый chat.")
    parser.add_argument("--openclaw-json", default=str(Path.home() / ".openclaw" / "openclaw.json"))
    parser.add_argument("--models-json", default=str(Path.home() / ".openclaw" / "agents" / "main" / "agent" / "models.json"))
    parser.add_argument("--auth-profiles-json", default=str(Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"))
    parser.add_argument("--gateway-log", default=str(Path.home() / ".openclaw" / "logs" / "gateway.err.log"))
    parser.add_argument("--base-url", default=str(os.getenv("OPENCLAW_BASE_URL", DEFAULT_GATEWAY_BASE_URL) or DEFAULT_GATEWAY_BASE_URL))
    args = parser.parse_args()

    result = asyncio.run(
        main_async(
            model=str(args.model or "").strip(),
            reasoning=str(args.reasoning or "high").strip(),
            skip_reasoning=bool(args.skip_reasoning),
            openclaw_json=Path(args.openclaw_json).expanduser(),
            models_json=Path(args.models_json).expanduser(),
            auth_profiles_json=Path(args.auth_profiles_json).expanduser(),
            gateway_log=Path(args.gateway_log).expanduser(),
            base_url=str(args.base_url or "").strip(),
        )
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
