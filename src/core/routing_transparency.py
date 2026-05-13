# -*- coding: utf-8 -*-
"""
Wave 244: routing transparency — где реально resolved model для Krab requests.

Проблема, которую решает модуль
-------------------------------
В /admin/models пользователь видит кнопку "primary" с конкретной моделью (например
``mlx-local-kv4/gemma-4-26b``), а в Telegram Krab отвечает через какой-то
``codex-cli/gpt-5.5``. Причина — split routing:

- ``mlx-local-kv4/*`` модели → MLX direct backend (``http://127.0.0.1:8088``),
  выбор Krab'а реально используется.
- ``lm-studio-local/*`` модели → LM Studio direct (``http://127.0.0.1:1234``).
- Остальные (cloud, codex-cli, gemini-cli) → OpenClaw Gateway, у которого
  собственный routing matrix (paid_gemini_guard + fallback chain). Krab лишь
  передаёт preference, фактический pick делает Gateway.

Этот модуль собирает три источника правды в один snapshot, чтобы UI и debug
endpoints могли честно показать пользователю, что происходит:

1. ``active_model.json`` — что выбрал owner в admin/models (Krab preference).
2. Резолв backend URL по prefix модели — куда Krab отправляет HTTP.
3. ``openclaw_client.get_last_runtime_route()`` — какая модель реально была
   использована в последнем LLM запросе (post-Gateway pick).

API
---
- ``get_actual_routing_state() -> dict`` — main entrypoint. Возвращает shape:
  ``{ok, picked, will_send_to, actually_used, warnings}``.
- ``read_active_model_file() -> dict`` — pure helper для чтения JSON файла.
- ``resolve_backend_for_model(model_id) -> dict`` — pure mapping prefix → URL.
- ``classify_model_resolution(model_id) -> str`` — direct/openclaw/unknown.

Используется в:
- ``models_admin_router`` для GET ``/api/admin/routing-active`` (Wave 244).
- Будущие debug pages (например ``/admin/why-this-model``).

Принципы
--------
- Pure functions: ни одна не выполняет HTTP (за здоровьем backends отвечают
  отдельные probes — этот модуль только агрегирует).
- Best-effort: любые I/O ошибки даунгрейдятся в пустые поля + добавляются
  в ``warnings`` список, чтобы UI мог показать "не смогли прочитать X".
- No openclaw.json mutation, no plist, no archive.db.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Канонический путь до active_model.json — пишется и читается через
# `set_active_model`/`get_active_model` в `src/core/active_model_routing.py`
# (когда тот модуль появится). Здесь дублируем константу namespace'а, чтобы
# не вводить hard зависимость от ещё несуществующего модуля.
_ACTIVE_MODEL_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "active_model.json"

# Дефолтные backend URL — выровнены с models_admin_router и mlx_local_discovery.
_DEFAULT_MLX_LOCAL_URL = "http://127.0.0.1:8088"
_DEFAULT_LM_STUDIO_URL = "http://127.0.0.1:1234"
_DEFAULT_OPENCLAW_URL = "http://127.0.0.1:18789"

# Префиксы model_id, которые мы считаем "direct" (Krab сам бьётся в backend).
# Всё остальное идёт через OpenClaw Gateway и его собственный routing.
_DIRECT_PREFIXES: frozenset[str] = frozenset(
    {
        "mlx-local-kv4",
        "lm-studio-local",
        "lm-studio",  # legacy short prefix (без `-local` suffix)
        "lmstudio",
        "local",
    }
)


def _provider_prefix(model_id: str) -> str:
    """Возвращает provider prefix model_id (часть до первого ``/``).

    Для bare id без slash возвращает пустую строку — caller сам решит, как
    это обрабатывать (обычно как unknown).
    """
    raw = str(model_id or "").strip()
    if "/" in raw:
        return raw.split("/", 1)[0]
    return ""


def classify_model_resolution(model_id: str) -> str:
    """Классифицирует модель: ``direct`` / ``openclaw`` / ``unknown``.

    - ``direct`` — Krab сам отправит HTTP запрос в локальный backend (MLX,
      LM Studio). Выбор owner'а realistically используется.
    - ``openclaw`` — модель идёт через OpenClaw Gateway, его routing matrix
      может перевыбрать модель/провайдера. Owner preference — лишь подсказка.
    - ``unknown`` — empty или malformed id.
    """
    raw = str(model_id or "").strip()
    if not raw:
        return "unknown"
    prefix = _provider_prefix(raw)
    if not prefix:
        # Bare id без slash — традиционно local LM Studio.
        return "direct"
    if prefix in _DIRECT_PREFIXES:
        return "direct"
    return "openclaw"


def resolve_backend_for_model(model_id: str) -> dict[str, Any]:
    """Возвращает где Krab будет искать backend для данной модели.

    Returns dict:
        {
            "resolution": "direct" | "openclaw" | "unknown",
            "backend_url": str,             # base URL без trailing slash
            "backend_kind": "mlx-local" | "lm-studio" | "openclaw-gateway",
            "note": str,                    # человекочитаемый комментарий
        }
    """
    raw = str(model_id or "").strip()
    resolution = classify_model_resolution(raw)

    if resolution == "unknown":
        return {
            "resolution": "unknown",
            "backend_url": "",
            "backend_kind": "",
            "note": "Модель не указана.",
        }

    prefix = _provider_prefix(raw)

    if prefix == "mlx-local-kv4":
        url = (os.getenv("MLX_LOCAL_BACKEND_URL") or _DEFAULT_MLX_LOCAL_URL).rstrip("/")
        return {
            "resolution": "direct",
            "backend_url": url,
            "backend_kind": "mlx-local",
            "note": "Krab отправит запрос напрямую в MLX backend (без OpenClaw).",
        }

    if prefix in {"lm-studio", "lm-studio-local", "lmstudio", "local"} or not prefix:
        # Берём LM_STUDIO_URL если задан, иначе дефолт.
        from src.config import config as _cfg  # noqa: PLC0415

        url = str(getattr(_cfg, "LM_STUDIO_URL", "") or "").rstrip("/") or _DEFAULT_LM_STUDIO_URL
        return {
            "resolution": "direct",
            "backend_url": url,
            "backend_kind": "lm-studio",
            "note": "Krab отправит запрос напрямую в LM Studio (без OpenClaw).",
        }

    # Всё остальное — Gateway. Конкретный pick делает OpenClaw, не Krab.
    from src.config import config as _cfg  # noqa: PLC0415

    url = str(getattr(_cfg, "OPENCLAW_URL", "") or "").rstrip("/") or _DEFAULT_OPENCLAW_URL
    return {
        "resolution": "openclaw",
        "backend_url": url,
        "backend_kind": "openclaw-gateway",
        "note": (
            "Cloud/CLI модель идёт через OpenClaw Gateway. Gateway применяет "
            "свой paid_gemini_guard + fallback chain — фактическая модель "
            "может отличаться от выбранной."
        ),
    }


def read_active_model_file(path: Path | None = None) -> dict[str, Any]:
    """Читает ``active_model.json`` (что owner picked в /admin/models).

    Возвращает dict с ключами ``model``, ``switched_at`` (unix), ``switched_by``,
    ``reason``. При отсутствии или невалидном JSON — пустой dict.
    """
    target = path or _ACTIVE_MODEL_PATH
    try:
        payload = json.loads(Path(target).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        logger.debug(
            "active_model_read_failed",
            path=str(target),
            error=str(exc),
        )
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        "model": str(payload.get("model") or "").strip(),
        "switched_at": payload.get("switched_at"),
        "switched_by": str(payload.get("switched_by") or "").strip(),
        "reason": str(payload.get("reason") or "").strip(),
    }


def _iso_from_unix(ts: Any) -> str | None:
    """Конвертирует unix timestamp в ISO-8601 UTC. None при failure."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _read_last_runtime_route() -> dict[str, Any]:
    """Best-effort вытягивает get_last_runtime_route() у singleton клиента.

    Импорт ленивый — на стороне тестов клиент можно патчить, а в окружениях
    без gateway мы не хотим падать на import time.
    """
    try:
        from src.openclaw_client import openclaw_client as _oc  # noqa: PLC0415

        return dict(_oc.get_last_runtime_route() or {})
    except Exception as exc:  # noqa: BLE001
        logger.debug("openclaw_client_read_failed", error=str(exc))
        return {}


def get_actual_routing_state() -> dict[str, Any]:
    """Главный API: возвращает snapshot фактического routing state.

    Shape:
        {
            "ok": True,
            "picked": {
                "model": "...",         # из active_model.json (owner choice)
                "switched_at": "...",   # ISO-8601 или None
                "switched_by": "...",
                "reason": "...",
            },
            "will_send_to": {
                "resolution": "direct" | "openclaw" | "unknown",
                "backend_url": "...",
                "backend_kind": "mlx-local" | "lm-studio" | "openclaw-gateway",
                "note": "...",
            },
            "actually_used": {
                "model": "...",         # из last_runtime_route (post-Gateway)
                "provider": "...",
                "channel": "...",
                "status": "...",
                "at": "...",            # ISO-8601 или None
            },
            "warnings": [str, ...]      # человекочитаемые предупреждения
        }
    """
    warnings: list[str] = []

    # 1. Что owner picked.
    picked_raw = read_active_model_file()
    picked_model = picked_raw.get("model") or ""
    if not picked_model:
        warnings.append(
            "active_model.json пуст или нечитаем — owner ещё ни разу не "
            "выбирал primary через /admin/models."
        )

    picked = {
        "model": picked_model,
        "switched_at": _iso_from_unix(picked_raw.get("switched_at")),
        "switched_by": picked_raw.get("switched_by") or "",
        "reason": picked_raw.get("reason") or "",
    }

    # 2. Куда Krab будет слать.
    will_send_to = resolve_backend_for_model(picked_model)

    # 3. Что реально использовалось в последнем запросе.
    last_route = _read_last_runtime_route()
    actually_used_model = str(last_route.get("model") or "").strip()
    actually_used = {
        "model": actually_used_model,
        "provider": str(last_route.get("provider") or "").strip(),
        "channel": str(last_route.get("channel") or "").strip(),
        "status": str(last_route.get("status") or "").strip(),
        "at": _iso_from_unix(last_route.get("timestamp")),
    }

    # 4. Diagnostics — расхождения между picked и actually used.
    if will_send_to.get("resolution") == "openclaw" and picked_model:
        warnings.append(
            "⚠️ Cloud-модель: финальный выбор делает OpenClaw Gateway "
            "(paid_gemini_guard + fallback chain). Для детерминированного "
            "Krab primary используй mlx-local-kv4/* или lm-studio-local/*."
        )
    if picked_model and actually_used_model and picked_model != actually_used_model:
        warnings.append(
            f"Picked model '{picked_model}' не совпадает с last actually used "
            f"'{actually_used_model}' — Gateway перевыбрал провайдера."
        )

    return {
        "ok": True,
        "picked": picked,
        "will_send_to": will_send_to,
        "actually_used": actually_used,
        "warnings": warnings,
    }


__all__ = [
    "classify_model_resolution",
    "get_actual_routing_state",
    "read_active_model_file",
    "resolve_backend_for_model",
]
