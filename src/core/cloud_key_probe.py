# -*- coding: utf-8 -*-
"""
Пробник облачных ключей и статуса Gemini/OpenClaw.

Зачем нужен:
- Даёт единый контракт диагностики cloud-цепочки для backend/web.
- Проверяет формат ключей AI Studio (AIza...) и базовую доступность API.
- Возвращает безопасные (masked) поля для UI без утечки секретов.

Связи:
- Используется OpenClawClient для runtime-check/failover телеметрии.
- Используется web_app для endpoint /api/openclaw/cloud/runtime-check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
PREFERRED_GEMINI_PROBE_MODELS = (
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
)


@dataclass(slots=True)
class CloudProbeResult:
    """Нормализованный результат проверки облачного ключа/модели."""

    provider_status: str
    key_source: str
    key_tier: str
    semantic_error_code: str
    recovery_action: str
    http_status: int | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Сериализация в словарь для JSON-ответов."""
        return {
            "provider_status": self.provider_status,
            "key_source": self.key_source,
            "key_tier": self.key_tier,
            "semantic_error_code": self.semantic_error_code,
            "recovery_action": self.recovery_action,
            "http_status": self.http_status,
            "detail": self.detail,
        }


def mask_secret(value: str | None) -> str:
    """Маскирует секрет: показываем только префикс/суффикс."""
    if not value:
        return ""
    text = str(value).strip()
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


def is_ai_studio_key(value: str | None) -> bool:
    """Проверка формата AI Studio API key (ожидаем AIza...)."""
    if not value:
        return False
    text = str(value).strip()
    return text.startswith("AIza") and len(text) >= 30


def classify_gemini_http_error(status_code: int, body: str) -> tuple[str, str, str]:
    """
    Классификация ошибок Gemini в единый контракт.

    Возвращает: (provider_status, semantic_error_code, recovery_action)
    """
    body_low = (body or "").lower()

    if status_code == 200:
        return ("ok", "ok", "none")
    if status_code == 429:
        return ("quota", "quota_exceeded", "switch_tier")
    if status_code in (401, 403):
        if "api keys are not supported" in body_low:
            return ("auth", "unsupported_key_type", "switch_provider_or_key")
        return ("auth", "auth_invalid", "switch_provider_or_key")
    if status_code == 404 and ("no longer available" in body_low or "not found" in body_low):
        return ("error", "model_not_available", "switch_model")
    if status_code >= 500:
        return ("error", "provider_unavailable", "retry_later")
    return ("error", "provider_error", "switch_provider_or_local")


def _extract_generate_models(list_payload: dict[str, Any]) -> set[str]:
    """Достаёт список моделей, у которых доступен generateContent."""
    result: set[str] = set()
    for item in list_payload.get("models", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name.startswith("models/"):
            continue
        methods = item.get("supportedGenerationMethods") or []
        if isinstance(methods, list) and "generateContent" in methods:
            result.add(name.replace("models/", "", 1))
    return result


def _pick_probe_model(preferred_model: str, available_models: set[str]) -> str:
    """
    Выбирает рабочую модель для probe.

    Порядок:
    1) Явно переданная модель (если доступна).
    2) Из заранее заданного списка предпочтений.
    3) Любая доступная модель (стабильный порядок).
    4) fallback на preferred_model (когда list вернул пусто/непарсится).
    """
    candidate = str(preferred_model or "").strip().replace("models/", "", 1)
    if candidate and candidate in available_models:
        return candidate

    for model_id in PREFERRED_GEMINI_PROBE_MODELS:
        if model_id in available_models:
            return model_id

    if available_models:
        return sorted(available_models)[0]
    return candidate or DEFAULT_GEMINI_MODEL


async def probe_gemini_key(
    api_key: str | None,
    *,
    key_source: str,
    key_tier: str,
    timeout: float = 12.0,
    model: str = DEFAULT_GEMINI_MODEL,
) -> CloudProbeResult:
    """Проверяет ключ Gemini двумя шагами: list models + generate."""
    if not api_key:
        return CloudProbeResult(
            provider_status="missing",
            key_source=key_source,
            key_tier=key_tier,
            semantic_error_code="missing_api_key",
            recovery_action="configure_key",
        )

    if not is_ai_studio_key(api_key):
        return CloudProbeResult(
            provider_status="invalid",
            key_source=key_source,
            key_tier=key_tier,
            semantic_error_code="unsupported_key_type",
            recovery_action="replace_with_aistudio_key",
            detail="Ожидается API key формата AIza...",
        )

    params = {"key": api_key}
    list_url = "https://generativelanguage.googleapis.com/v1beta/models"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            list_resp = await client.get(list_url, params=params)
            list_body = list_resp.text[:600]
            list_status, list_code, list_action = classify_gemini_http_error(
                list_resp.status_code,
                list_body,
            )
            if list_resp.status_code != 200:
                return CloudProbeResult(
                    provider_status=list_status,
                    key_source=key_source,
                    key_tier=key_tier,
                    semantic_error_code=list_code,
                    recovery_action=list_action,
                    http_status=list_resp.status_code,
                    detail=list_body,
                )

            selected_model = model
            try:
                available_models = _extract_generate_models(list_resp.json())
                selected_model = _pick_probe_model(model, available_models)
            except (ValueError, TypeError, KeyError):
                selected_model = model

            gen_url = f"https://generativelanguage.googleapis.com/v1beta/models/{selected_model}:generateContent"
            gen_resp = await client.post(
                gen_url,
                params=params,
                json={"contents": [{"parts": [{"text": "ping"}]}]},
            )
            gen_body = gen_resp.text[:600]
            gen_status, gen_code, gen_action = classify_gemini_http_error(
                gen_resp.status_code,
                gen_body,
            )
            return CloudProbeResult(
                provider_status=gen_status,
                key_source=key_source,
                key_tier=key_tier,
                semantic_error_code=gen_code,
                recovery_action=gen_action,
                http_status=gen_resp.status_code,
                detail=gen_body if gen_resp.status_code != 200 else "",
            )
    except (httpx.HTTPError, OSError) as exc:
        return CloudProbeResult(
            provider_status="error",
            key_source=key_source,
            key_tier=key_tier,
            semantic_error_code="network_error",
            recovery_action="retry_or_fallback",
            detail=str(exc),
        )


def default_openclaw_models_path() -> Path:
    """Путь к source-of-truth модели OpenClaw (agent models.json)."""
    return Path.home() / ".openclaw" / "agents" / "main" / "agent" / "models.json"


def load_openclaw_models_json(path: Path | None = None) -> dict[str, Any]:
    """Читает models.json OpenClaw; при ошибке возвращает пустой словарь."""
    target = Path(path or default_openclaw_models_path())
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get_google_api_key_from_models(path: Path | None = None) -> str:
    """Возвращает google apiKey из OpenClaw models.json."""
    data = load_openclaw_models_json(path)
    return str((((data.get("providers") or {}).get("google") or {}).get("apiKey") or "")).strip()
