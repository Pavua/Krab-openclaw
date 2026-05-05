"""Wave 23-A: Vertex AI direct SDK bypass для google-vertex/* models.

Минует OpenClaw broken transport и paid AI Studio API key — вместо этого
использует Google Cloud Vertex AI (€848 credits до 2027-03 + €67 истекающие).

Поддерживаемые модели:
- google-vertex/gemini-2.5-pro
- google-vertex/gemini-2.5-flash
(3.x preview на Vertex не доступны — fallback на CLI OAuth, Wave 22-A)

Auth: ADC (~/.config/gcloud/application_default_credentials.json) с
quota_project_id=caramel-anvil-492816-t5.

Симметрично src/integrations/google_genai_direct.py (Wave 18-B):
использует тот же google.genai package, но с vertexai=True вместо api_key.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from ..core.logger import get_logger
from ._bypass_perf import record_bypass_call
from ._bypass_sentry import add_bypass_breadcrumb

logger = get_logger(__name__)

# Префикс провайдера для Vertex AI моделей
VERTEX_PREFIX = "google-vertex/"
DEFAULT_PROJECT = "caramel-anvil-492816-t5"
DEFAULT_LOCATION = "global"  # Wave 23-B: gemini-3.1 + другие preview работают только в global


def is_vertex_enabled() -> bool:
    """Включён ли Vertex bypass. Default ON (opt-out через env=0)."""
    return str(os.environ.get("KRAB_VERTEX_BYPASS_ENABLED", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_vertex_model(model: str) -> bool:
    """True если модель имеет префикс google-vertex/."""
    return bool(model) and model.startswith(VERTEX_PREFIX)


def _strip_prefix(model: str) -> str:
    """'google-vertex/gemini-2.5-pro' → 'gemini-2.5-pro'."""
    if model.startswith(VERTEX_PREFIX):
        return model[len(VERTEX_PREFIX) :]
    return model


async def complete_via_vertex(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float = 0.7,
    max_output_tokens: int = 8192,
    project: str | None = None,
    location: str | None = None,
) -> str:
    """Прямой Vertex AI call. Возвращает str (text response).

    Симметрично complete_direct из google_genai_direct: sync google.genai
    под капотом — оборачиваем в asyncio.to_thread.

    Args:
        model: e.g. 'google-vertex/gemini-2.5-pro' или bare 'gemini-2.5-pro'
        messages: список {"role": "user"|"assistant"|"system", "content": str}
        temperature: temperature для генерации (default 0.7)
        max_output_tokens: лимит токенов в ответе (default 8192)
        project: GCP project override (default из env / DEFAULT_PROJECT)
        location: Vertex location override (default из env / DEFAULT_LOCATION)

    Returns:
        text ответа (str). Пустая строка если ответа нет.

    Raises:
        ImportError: если google.genai SDK не установлен
        Exception: пробрасывает любые ошибки SDK / ADC выше
    """
    # Lazy import — избегаем тяжелого импорта при загрузке модуля
    from google import genai as _genai
    from google.genai.types import GenerateContentConfig

    proj = project or os.environ.get("KRAB_VERTEX_PROJECT") or DEFAULT_PROJECT
    loc = location or os.environ.get("KRAB_VERTEX_LOCATION") or DEFAULT_LOCATION
    bare_model = _strip_prefix(model)
    # Breadcrumb: старт Vertex bypass — project + location для post-mortem (Wave 30-B)
    add_bypass_breadcrumb(
        bypass_kind="vertex",
        event="engaged",
        model=bare_model,
        extra={"project": proj, "location": loc},
    )

    # ADC ожидает GOOGLE_CLOUD_PROJECT для quota project resolution
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        os.environ["GOOGLE_CLOUD_PROJECT"] = proj

    # Собираем prompt из messages — Vertex принимает простую строку или contents-array.
    # Здесь делаем простой склейку с role-префиксами (mirror стиля Wave 22-A).
    prompt_parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            # multimodal payload — берём только текстовые куски
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        prefix = {
            "system": "[Контекст]: ",
            "user": "[Пользователь]: ",
            "assistant": "[Ассистент]: ",
        }.get(str(role), f"[{role}]: ")
        prompt_parts.append(f"{prefix}{content}")
    prompt = "\n\n".join(prompt_parts)

    def _sync_call() -> str:
        # vertexai=True — переключает Client на Vertex AI вместо AI Studio
        client = _genai.Client(vertexai=True, project=proj, location=loc)
        cfg = GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        resp = client.models.generate_content(
            model=bare_model,
            contents=prompt,
            config=cfg,
        )
        return (resp.text or "").strip()

    # Wave 31-A: замер latency bypass call
    _perf_start = time.time()
    _perf_success = False
    _perf_response_len = 0
    _perf_error_type: str | None = None

    try:
        text = await asyncio.to_thread(_sync_call)
    except Exception as exc:
        # Breadcrumb: Vertex SDK error — error_type + project + region для диагностики (Wave 30-B)
        add_bypass_breadcrumb(
            bypass_kind="vertex",
            event="failure",
            model=bare_model,
            extra={
                "error_type": type(exc).__name__,
                "error": str(exc)[:200],
                "project": proj,
                "location": loc,
            },
            level="warning",
        )
        _perf_error_type = type(exc).__name__
        raise
    else:
        _perf_success = True
        _perf_response_len = len(text)
    finally:
        # Wave 31-A: записываем latency в JSONL (graceful)
        record_bypass_call(
            kind="vertex",
            model=model,
            duration_sec=time.time() - _perf_start,
            success=_perf_success,
            response_len=_perf_response_len,
            error_type=_perf_error_type,
        )

    if not text:
        # Wave 18-I-style retry с thinking_budget=0 здесь НЕ нужен — Vertex
        # 2.5 модели не имеют thinking enabled by default (3.x на Vertex недоступны).
        logger.warning("vertex_empty_response_no_retry", model=bare_model)

    logger.info(
        "google_vertex_direct_complete_done",
        model=bare_model,
        project=proj,
        location=loc,
        length=len(text),
    )
    # Breadcrumb: успешный Vertex bypass (Wave 30-B)
    add_bypass_breadcrumb(
        bypass_kind="vertex",
        event="success",
        model=bare_model,
        extra={"project": proj, "location": loc, "response_len": len(text)},
    )
    return text
