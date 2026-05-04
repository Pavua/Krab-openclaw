"""Direct Google Generative AI bypass для OpenClaw WebSocket transport regression.

OpenClaw 2026.5.2 имеет regression: WebSocket → openresponses HTTP path
ловит 500 internal error при Google provider. Этот модуль идёт напрямую
через google-genai SDK (как CLI local transport делает).

Activated только когда модель начинается с 'google/' AND env
KRAB_GOOGLE_DIRECT_BYPASS_ENABLED=1 (default ON, можно выключить через .env).

Симметрично OpenClawClient._openclaw_completion_once() — возвращает str текст ответа.

Использует google-genai (новый SDK, `google.genai`), не deprecated google-generativeai.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from ..core.logger import get_logger

logger = get_logger(__name__)


def _add_sentry_breadcrumb(category: str, message: str, level: str = "info", **data: Any) -> None:
    """Best-effort Sentry breadcrumb для bypass trace. Silent если sentry_sdk не установлен."""
    try:
        import sentry_sdk  # type: ignore[import-not-found]

        sentry_sdk.add_breadcrumb(
            category=f"krab.bypass.{category}",
            message=message,
            level=level,
            data=data,
        )
    except (ImportError, Exception):  # noqa: BLE001 — breadcrumbs не должны ронять hot-path
        pass


def is_google_direct_enabled() -> bool:
    """Включён ли bypass. Default ON (opt-out через env=0)."""
    return str(os.environ.get("KRAB_GOOGLE_DIRECT_BYPASS_ENABLED", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_google_model(model: str) -> bool:
    """True если model id указывает на Google direct API (НЕ google-gemini-cli).

    google/gemini-3-pro-preview → True
    google-gemini-cli/... → False (CLI провайдер, не direct)
    openai/gpt-5 → False
    """
    if not model:
        return False
    # google-antigravity и google-gemini-cli — не direct API
    if model.startswith("google-"):
        return False
    return model.startswith("google/")


def _strip_provider_prefix(model: str) -> str:
    """'google/gemini-3-pro-preview' → 'gemini-3-pro-preview'."""
    return model.split("/", 1)[1] if "/" in model else model


def _resolve_api_key() -> str | None:
    """Резолвит актуальный Gemini API ключ из config.

    Приоритет: paid (если GEMINI_PAID_KEY_ENABLED=1) → free → GEMINI_API_KEY.
    """
    try:
        from ..config import config

        # Paid key если явно включён
        if (
            str(os.environ.get("GEMINI_PAID_KEY_ENABLED", "0")).strip().lower()
            in {"1", "true", "yes"}
            and config.GEMINI_API_KEY_PAID
        ):
            return str(config.GEMINI_API_KEY_PAID)
        # Free key или fallback GEMINI_API_KEY
        return (
            str(
                config.GEMINI_API_KEY_FREE
                or config.GEMINI_API_KEY
                or os.environ.get("GEMINI_API_KEY")
                or ""
            )
            or None
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("google_genai_direct_key_resolve_failed", error=str(exc))
        return os.environ.get("GEMINI_API_KEY_FREE") or os.environ.get("GEMINI_API_KEY")


async def complete_direct(
    *,
    model: str,
    messages: list[dict[str, Any]],
    api_key: str | None = None,
    timeout_sec: float = 300.0,
    max_output_tokens: int | None = None,
) -> str:
    """Выполняет completion через Google Generative AI SDK напрямую.

    Принимает messages в OpenAI-совместимом формате (role/content)
    и конвертирует в Gemini Contents + system_instruction.

    Args:
        model: e.g. 'google/gemini-3-pro-preview' или 'gemini-3-pro-preview'
        messages: список {"role": "user"|"assistant"|"system", "content": str}
        api_key: override; если None — resolves через _resolve_api_key()
        timeout_sec: полный таймаут на completion
        max_output_tokens: лимит токенов в ответе

    Returns:
        text ответа (str). Пустая строка если ответа нет.

    Raises:
        RuntimeError: если SDK не установлен или нет API key
    """
    # Lazy import чтобы избежать heavy import при загрузке модуля
    try:
        from google import genai  # type: ignore[import]
        from google.genai import types as genai_types  # type: ignore[import]
    except ImportError as exc:
        logger.warning("google_genai_sdk_not_installed", error=str(exc))
        raise RuntimeError(
            "google-genai package не установлен в venv. "
            "Установить: pip install 'google-genai>=1.0.0'"
        ) from exc

    resolved_key = api_key or _resolve_api_key()
    if not resolved_key:
        raise RuntimeError(
            "Gemini API key недоступен (GEMINI_API_KEY_PAID/GEMINI_API_KEY_FREE/GEMINI_API_KEY)"
        )

    model_id = _strip_provider_prefix(model)

    # Разделяем system instructions и user/assistant messages
    system_instruction: str | None = None
    contents: list[dict[str, Any]] = []

    for msg in messages:
        role = str(msg.get("role") or "").strip().lower()
        content = msg.get("content") or ""

        # content может быть list (multimodal) — берём только текст для direct bypass
        if isinstance(content, list):
            text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and "text" in p]
            content = " ".join(text_parts)

        content_str = str(content).strip()

        if role == "system":
            # Gemini принимает system_instruction отдельно
            system_instruction = content_str
        elif role == "assistant":
            # Gemini называет роль "model" для ассистента
            contents.append({"role": "model", "parts": [{"text": content_str}]})
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": content_str}]})
        # else: неизвестная роль — пропускаем

    if not contents:
        logger.warning("google_genai_direct_no_messages", model=model_id)
        return ""

    logger.info(
        "google_genai_direct_complete_start",
        model=model_id,
        contents_count=len(contents),
        has_system=bool(system_instruction),
    )
    # Breadcrumb: старт bypass — для post-mortem trace (когда начался bypass и с какой моделью)
    _add_sentry_breadcrumb(
        "start",
        f"Google direct bypass для {model_id}",
        model=model_id,
        has_system=bool(system_instruction),
        contents_count=len(contents),
    )

    def _blocking_complete() -> str:
        """Синхронный вызов генерации в thread pool."""
        # Новый google-genai SDK: google.genai.Client
        client = genai.Client(api_key=resolved_key)

        # Wave 18-H fix: GenerateContentConfig принимает плоские поля (max_output_tokens,
        # system_instruction), а не вложенный generation_config — старый pattern из
        # legacy google-generativeai SDK ломался валидацией pydantic в google.genai.
        config_kwargs: dict[str, Any] = {}
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if max_output_tokens and max_output_tokens > 0:
            config_kwargs["max_output_tokens"] = max_output_tokens

        generate_config = (
            genai_types.GenerateContentConfig(**config_kwargs) if config_kwargs else None
        )

        response = client.models.generate_content(
            model=model_id,
            contents=contents,  # type: ignore[arg-type]
            config=generate_config,
        )

        # Извлекаем текст из response
        text = ""
        try:
            text = response.text or ""
        except Exception:  # noqa: BLE001
            # Иногда .text кидает исключение при blocked content
            try:
                parts = []
                for candidate in response.candidates:
                    for part in candidate.content.parts:
                        if hasattr(part, "text") and part.text:
                            parts.append(part.text)
                text = "".join(parts)
            except Exception:  # noqa: BLE001
                text = ""

        if not text.strip():
            # Wave 18-I: empty response — Gemini 3-pro/3.1-pro имеют thinking включён
            # по умолчанию. Короткие prompts (warmup 'ping') тратят весь output budget
            # на thinking и возвращают response.text=''. Retry с thinking отключённым.
            prompt_tokens = getattr(
                getattr(response, "usage_metadata", None), "prompt_token_count", None
            )
            thoughts_tokens = getattr(
                getattr(response, "usage_metadata", None), "thoughts_token_count", None
            )
            logger.warning(
                "google_genai_direct_empty_text_retrying_no_thinking",
                model=model_id,
                prompt_token_count=prompt_tokens,
                thoughts_token_count=thoughts_tokens,
            )
            # Breadcrumb: пустой ответ → retry с thinking_budget=0
            # (Wave 18-I: Gemini 3-pro тратит весь output budget на thinking при коротких prompts)
            _add_sentry_breadcrumb(
                "empty_retry",
                "Empty response — retrying с thinking_budget=0",
                model=model_id,
                thoughts_tokens=thoughts_tokens,
                prompt_tokens=prompt_tokens,
            )

            # Проверяем доступность ThinkingConfig в установленной версии SDK
            thinking_config_cls = getattr(genai_types, "ThinkingConfig", None)
            if thinking_config_cls is None:
                # Старая версия SDK без ThinkingConfig — graceful degrade
                logger.warning(
                    "google_genai_direct_thinking_config_unavailable",
                    model=model_id,
                    sdk_version="unknown",
                )
                return text

            # Retry с thinking_budget=0 чтобы модель не тратила токены на думание
            config_kwargs_no_think = dict(config_kwargs)
            config_kwargs_no_think["thinking_config"] = thinking_config_cls(thinking_budget=0)
            no_think_config = genai_types.GenerateContentConfig(**config_kwargs_no_think)

            response2 = client.models.generate_content(
                model=model_id,
                contents=contents,  # type: ignore[arg-type]
                config=no_think_config,
            )
            try:
                text = response2.text or ""
            except Exception:  # noqa: BLE001
                text = ""

        return text

    # Замеряем полную latency bypass-вызова для Prometheus
    _t0 = time.monotonic()

    try:
        text = await asyncio.wait_for(
            asyncio.to_thread(_blocking_complete),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        _elapsed = time.monotonic() - _t0
        logger.warning("google_genai_direct_timeout", model=model_id, timeout_sec=timeout_sec)
        # Breadcrumb: таймаут bypass — видно в Sentry trace без отдельного event
        _add_sentry_breadcrumb(
            "error",
            f"Bypass timeout после {round(_elapsed, 2)}s",
            level="warning",
            model=model_id,
            error="TimeoutError",
            latency_sec=round(_elapsed, 2),
        )
        # Таймаут — записываем как error (timeout variant)
        try:
            from ..core.prometheus_metrics import record_google_bypass_call

            record_google_bypass_call(model=model, outcome="error", latency_sec=_elapsed)
        except Exception:  # noqa: BLE001
            pass
        return ""
    except Exception as exc:  # noqa: BLE001
        _elapsed = time.monotonic() - _t0
        logger.warning("google_genai_direct_error", model=model_id, error=str(exc))
        # Breadcrumb: исключение bypass — error_type помогает сортировать по причине
        _add_sentry_breadcrumb(
            "error",
            f"Bypass failed: {type(exc).__name__}",
            level="warning",
            model=model_id,
            error=str(exc),
            error_type=type(exc).__name__,
            latency_sec=round(_elapsed, 2),
        )
        # Исключение — записываем outcome=error
        try:
            from ..core.prometheus_metrics import record_google_bypass_call

            record_google_bypass_call(model=model, outcome="error", latency_sec=_elapsed)
        except Exception:  # noqa: BLE001
            pass
        raise

    _elapsed = time.monotonic() - _t0

    logger.info(
        "google_genai_direct_complete_done",
        model=model_id,
        response_len=len(text),
    )
    # Breadcrumb: успешный bypass — latency + response_len для post-mortem анализа
    _add_sentry_breadcrumb(
        "success",
        "Bypass completed",
        model=model_id,
        latency_sec=round(_elapsed, 2),
        response_len=len(text),
        is_empty=not text.strip(),
    )

    # Записываем метрики: success если text non-empty, empty иначе
    try:
        from ..core.prometheus_metrics import record_google_bypass_call

        _outcome = "success" if text.strip() else "empty"
        record_google_bypass_call(model=model, outcome=_outcome, latency_sec=_elapsed)
    except Exception:  # noqa: BLE001
        pass

    return text


async def health_check_direct(*, api_key: str | None = None, timeout_sec: float = 10.0) -> bool:
    """Quick health probe: 'ping' → ok=True если получен ответ."""
    try:
        result = await complete_direct(
            model="google/gemini-2.5-flash",
            messages=[{"role": "user", "content": "ping"}],
            api_key=api_key,
            timeout_sec=timeout_sec,
            max_output_tokens=10,
        )
        return bool(result and result.strip())
    except Exception as exc:  # noqa: BLE001
        logger.warning("google_genai_direct_health_failed", error=str(exc))
        return False
