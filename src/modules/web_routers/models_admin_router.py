# -*- coding: utf-8 -*-
"""
Models admin router — Wave 144 (Session 53).

Визуальный picker моделей в Owner Panel ``:8080``. Дополняет
``model_router`` агрегирующим endpoint'ом ``GET /api/models/registry``
(provider-grouped реестр с health badges) и self-contained HTML страницей
``GET /admin/models``, на которой через polling каждые 10 секунд видно:

- текущий активный routing (provider/model/channel/since);
- доступные модели сгруппированные по провайдерам с health-бейджами:
  ``healthy``, ``quarantined`` (Wave 94), ``quota_exhausted`` (Wave 62-G
  codex quota state), ``loaded`` / ``not_loaded`` (для LM Studio).
- кнопки действий: Set primary (POST /api/admin/model/switch), Test ping
  (GET /api/model/status), Load/Unload (POST /api/model/local/load-default
  / /api/model/local/unload).

В отличие от ``/api/model/catalog`` (legacy slot-oriented контракт), это
тонкий слой именно над провайдерами и health — UI читает в одну круговую
дугу. Switch endpoint делегирует ``model_manager.set_model`` /
``set_provider`` — это тот же канал, что и ``/api/model/switch``, без
дублирования логики.

Endpoint'ы:
- GET  /api/models/registry        — provider-grouped реестр + health.
- POST /api/admin/model/switch     — body ``{provider, model}``; auth через
                                     ``ctx.assert_write_access``.
- GET  /admin/models                — HTML страница (inline).

Контракт ``/api/models/registry``::

    {
      "ok": true,
      "current": {
        "provider": "codex-cli",
        "model": "codex-cli/gpt-5.5",
        "channel": "cli_subprocess",
        "since": "2026-05-12T20:23:59+00:00",
        "status": "pending"
      },
      "providers": [
        {
          "id": "google-vertex",
          "label": "Google Vertex",
          "type": "cloud",
          "available": true,
          "models": [
            {
              "id": "google-vertex/gemini-3-pro-preview",
              "label": "Gemini 3 Pro Preview",
              "status": "healthy" | "quarantined" | "quota_exhausted"
                       | "loaded" | "not_loaded",
              "status_detail": "...",
              "is_active": false,
              "actions": ["set_primary", "test_ping"]
            }
          ]
        }
      ],
      "history": [{"timestamp": "...", "from": "...", "to": "...",
                   "actor": "..."}]
    }

Контракт ``POST /api/admin/model/switch``::

    {"ok": true, "provider": "google-vertex",
     "model": "google-vertex/gemini-3-pro-preview", "active": "..."}
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from ._context import RouterContext

# ── Static provider/model catalog ───────────────────────────────────────────

# Реестр известных cloud-моделей по провайдерам. Источник — CLAUDE.md §Модели
# плюс реальные runtime провайдеры из ``cloud_inventory``. Lock-step с
# ``model_manager.is_local_model()`` (cloud → пути с slash + не из LM Studio).
_CLOUD_PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "google-vertex",
        "label": "Google Vertex",
        "type": "cloud",
        "models": [
            ("google-vertex/gemini-3-pro-preview", "Gemini 3 Pro Preview"),
            ("google-vertex/gemini-3-flash-preview", "Gemini 3 Flash Preview"),
            ("google-vertex/gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview"),
            ("google-vertex/gemini-2.5-pro", "Gemini 2.5 Pro"),
            ("google-vertex/gemini-2.5-flash", "Gemini 2.5 Flash"),
            ("google-vertex/gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite"),
        ],
    },
    {
        "id": "anthropic-vertex",
        "label": "Anthropic Vertex",
        "type": "cloud",
        "models": [
            ("anthropic-vertex/claude-sonnet-4-5", "Claude Sonnet 4.5"),
            ("anthropic-vertex/claude-opus-4", "Claude Opus 4"),
        ],
    },
    {
        "id": "codex-cli",
        "label": "Codex CLI (OpenAI)",
        "type": "cli",
        "models": [
            ("codex-cli/gpt-5.5", "GPT-5.5"),
            ("codex-cli/gpt-5.4", "GPT-5.4"),
            ("codex-cli/gpt-5", "GPT-5"),
            ("codex-cli/gpt-5-codex", "GPT-5 Codex"),
            ("codex-cli/gpt-5-mini", "GPT-5 mini"),
        ],
    },
    {
        "id": "google-gemini-cli",
        "label": "Google Gemini CLI",
        "type": "cli",
        "models": [
            ("google-gemini-cli/gemini-2.5-pro", "Gemini 2.5 Pro (CLI)"),
            ("google-gemini-cli/gemini-3-flash-preview", "Gemini 3 Flash (CLI)"),
            ("google-gemini-cli/gemini-2.5-flash", "Gemini 2.5 Flash (CLI)"),
        ],
    },
    {
        "id": "mlx-local-kv4",
        "label": "MLX KV4 (Local :8088)",
        "type": "local",
        "models": [
            ("mlx-local-kv4/gemma-4-26b", "Gemma-4-26B-A4B Heretic (Baseline)"),
            ("mlx-local-kv4/qwen3-4b-kv4", "Qwen3-4B Huihui (KV4, 105 tok/s)"),
            ("mlx-local-kv4/qwen3-14b-kv4", "Qwen3-14B Huihui v2 (KV4, 41.5 tok/s)"),
            ("mlx-local-kv4/llama-3.3-8b-kv4", "Llama-3.3-8B Abl 128K (KV4, 39.5 tok/s)"),
        ],
    },
]


# ── Helpers ─────────────────────────────────────────────────────────────────


def _provider_status_for_model(
    provider_id: str,
    *,
    quarantined: bool,
    codex_disabled: bool,
) -> tuple[str, str]:
    """Возвращает (status, detail) для cloud провайдера."""
    pid = (provider_id or "").lower()
    if pid == "codex-cli" and codex_disabled:
        return "quota_exhausted", "Все аккаунты codex-cli исчерпаны (Wave 62-G)"
    if quarantined:
        return "quarantined", "Провайдер в quarantine (Wave 94, >=5 fail/10 min)"
    return "healthy", ""


def _resolve_active_provider(model_id: str) -> str:
    """Извлекает provider prefix из ``model_id`` (часть до первого ``/``).

    Для голых local model id (без slash) возвращает ``lm-studio``.
    """
    raw = str(model_id or "").strip()
    if not raw:
        return ""
    if "/" in raw:
        return raw.split("/", 1)[0]
    return "lm-studio"


def _format_history_entries(entries: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Sanitize/copy history entries для UI (legacy black_box format).

    Поддерживает два формата:
    1. Wave 144 black_box: ``{timestamp, from, to, actor}``.
    2. Wave 145 ``model_switch_history``: ``{ts, by, from_provider, from_model,
       to_provider, to_model, reason, success}`` — конвертируется в UI shape.
    """
    if not entries:
        return []
    safe: list[dict[str, Any]] = []
    for entry in entries[-10:]:
        if not isinstance(entry, dict):
            continue
        # Wave 145 формат — компактный display string собирается на сервере.
        if "ts" in entry or "to_model" in entry:
            from_part = (
                str(entry.get("from_model") or "") or str(entry.get("from_provider") or "") or "?"
            )
            to_part = str(entry.get("to_model") or "") or str(entry.get("to_provider") or "") or "?"
            safe.append(
                {
                    "timestamp": str(entry.get("ts") or ""),
                    "from": from_part,
                    "to": to_part,
                    "actor": str(entry.get("by") or "unknown"),
                    "reason": str(entry.get("reason") or ""),
                    "success": bool(entry.get("success", True)),
                }
            )
            continue
        # Legacy black_box формат.
        safe.append(
            {
                "timestamp": str(entry.get("timestamp") or ""),
                "from": str(entry.get("from") or ""),
                "to": str(entry.get("to") or ""),
                "actor": str(entry.get("actor") or ""),
            }
        )
    return safe


def _log_history_entry(
    *,
    by: str,
    from_provider: str,
    from_model: str,
    to_provider: str,
    to_model: str,
    reason: str,
    success: bool,
) -> None:
    """Wave 145: записывает switch в persistent history.

    Best-effort — любые ошибки store глотаются (logger.warning внутри store),
    чтобы failure в history не блокировал успешный switch.
    """
    try:
        from src.core.model_switch_history import model_switch_history

        model_switch_history.log_switch(
            by=by,
            from_provider=from_provider,
            from_model=from_model,
            to_provider=to_provider,
            to_model=to_model,
            reason=reason,
            success=success,
        )
    except Exception:  # noqa: BLE001
        # log_switch уже warning'ил внутри. Игнорируем чтобы UI не упал.
        pass


# ── Main factory ────────────────────────────────────────────────────────────


def build_models_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с model picker UI + registry/switch."""
    router = APIRouter(tags=["models-admin"])

    # ---------- GET /api/models/registry --------------------------------------
    @router.get("/api/models/registry")
    async def models_registry() -> dict[str, Any]:
        """Provider-grouped реестр с health overlay и текущим routing."""
        from src.core.openclaw_runtime_models import get_runtime_primary_model
        from src.core.provider_quarantine import provider_quarantine
        from src.model_manager import model_manager as _mm
        from src.openclaw_client import openclaw_client as _oc

        # --- Текущий routing ----------------------------------------------
        route = _oc.get_last_runtime_route() or {}
        active_model = str(getattr(_mm, "active_model_id", None) or route.get("model", ""))
        configured_primary = get_runtime_primary_model() or active_model
        active_provider = _resolve_active_provider(active_model or configured_primary)
        since_iso: str | None = None
        ts = route.get("timestamp")
        if ts is not None:
            try:
                since_iso = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            except (TypeError, ValueError, OSError):
                since_iso = None

        current = {
            "provider": str(route.get("provider") or active_provider or "unknown"),
            "model": active_model or configured_primary or "",
            "configured_primary": configured_primary or "",
            "channel": str(route.get("channel") or ""),
            "since": since_iso,
            "status": str(route.get("status") or "unknown"),
        }

        # --- Codex quota state (Wave 62-G) --------------------------------
        codex_disabled = False
        try:
            from src.integrations.codex_quota_state import is_codex_disabled

            codex_disabled = bool(is_codex_disabled())
        except Exception:  # noqa: BLE001
            codex_disabled = False

        # --- Quarantine snapshot (Wave 94) --------------------------------
        quarantine_map: dict[str, bool] = {}
        try:
            for entry in provider_quarantine.list_entries():
                pid = str(entry.get("provider") or "").strip().lower()
                if pid:
                    quarantine_map[pid] = bool(entry.get("quarantined"))
        except Exception:  # noqa: BLE001
            quarantine_map = {}

        # --- LM Studio live probe -----------------------------------------
        lm_section = await _build_lm_studio_section(ctx, active_model=active_model)

        # --- Cloud providers ----------------------------------------------
        providers: list[dict[str, Any]] = []
        for prov in _CLOUD_PROVIDERS:
            pid = str(prov["id"])
            quarantined = quarantine_map.get(pid.lower(), False)
            models: list[dict[str, Any]] = []
            for model_id, label in prov["models"]:
                status, detail = _provider_status_for_model(
                    pid,
                    quarantined=quarantined,
                    codex_disabled=codex_disabled,
                )
                is_active = bool(active_model and model_id == active_model) or bool(
                    configured_primary and model_id == configured_primary
                )
                models.append(
                    {
                        "id": model_id,
                        "label": label,
                        "status": status,
                        "status_detail": detail,
                        "is_active": is_active,
                        "actions": ["set_primary", "test_ping"],
                    }
                )
            providers.append(
                {
                    "id": pid,
                    "label": str(prov["label"]),
                    "type": str(prov["type"]),
                    "available": not quarantined,
                    "models": models,
                }
            )

        providers.append(lm_section)

        # --- History (Wave 145: persistent JSON store) --------------------
        # Приоритет: model_switch_history (структурированный schema) →
        # fallback на legacy black_box.tail_events если history пуст
        # (даёт прогрев новой странички миграционной historie из BlackBox).
        history_entries: list[dict[str, Any]] = []
        try:
            from src.core.model_switch_history import model_switch_history

            raw_history = model_switch_history.to_json_safe(limit=10)
            history_entries = _format_history_entries(list(raw_history))
        except Exception:  # noqa: BLE001
            history_entries = []
        if not history_entries:
            try:
                black_box = ctx.get_dep("black_box")
                if black_box is not None and hasattr(black_box, "tail_events"):
                    events = black_box.tail_events(kind="model_switch", limit=10) or []
                    history_entries = _format_history_entries(list(events))
            except Exception:  # noqa: BLE001
                history_entries = []

        return {
            "ok": True,
            "current": current,
            "providers": providers,
            "history": history_entries,
            "codex_accounts_exhausted": codex_disabled,
        }

    # ---------- POST /api/admin/model/switch ----------------------------------
    @router.post("/api/admin/model/switch")
    async def admin_model_switch(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict[str, Any]:
        """Переключает primary модель через picker UI.

        Body: ``{"provider": "google-vertex", "model": "<full id>"}``.
        Если ``model`` присутствует — приоритетнее: вызываем set_model.
        Если есть только ``provider`` ∈ {auto,local,cloud} — set_provider.
        """
        ctx.assert_write_access(x_krab_web_key, token)
        from src.model_manager import model_manager as _mm

        provider = str(payload.get("provider") or "").strip()
        model = str(payload.get("model") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        by = str(payload.get("by") or "owner_panel").strip() or "owner_panel"

        if not provider and not model:
            raise HTTPException(
                status_code=400,
                detail="provider_or_model_required",
            )

        # Снимаем previous state ДО switch чтобы записать в history.
        previous_model = str(getattr(_mm, "active_model_id", "") or "")
        previous_provider = _resolve_active_provider(previous_model)

        # Простейшая валидация против известного реестра + LM Studio probe.
        if model:
            known_cloud = {mid for prov in _CLOUD_PROVIDERS for mid, _ in prov["models"]}
            local_models = await _list_lm_studio_models(ctx)
            local_ids = {str(item.get("id") or "") for item in local_models if item.get("id")}
            if model not in known_cloud and model not in local_ids:
                # Разрешаем модели с допустимым slash-префиксом известного
                # provider — иначе сразу 400.
                if "/" in model:
                    prefix = model.split("/", 1)[0]
                    known_prefixes = {p["id"] for p in _CLOUD_PROVIDERS}
                    if prefix not in known_prefixes:
                        raise HTTPException(
                            status_code=400,
                            detail=f"model_unknown:{model}",
                        )
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"model_unknown:{model}",
                    )

        switch_success = False
        try:
            if model:
                _mm.set_model(model)
                action = "set_model"
            else:
                lowered = provider.lower()
                if lowered not in {"auto", "local", "cloud"}:
                    raise HTTPException(
                        status_code=400,
                        detail="provider_must_be_auto_local_cloud_when_model_omitted",
                    )
                _mm.set_provider(lowered)
                action = "set_provider"
            switch_success = True
        except ValueError as exc:
            # Wave 145: даже при failure пишем в history для диагностики.
            _log_history_entry(
                by=by,
                from_provider=previous_provider,
                from_model=previous_model,
                to_provider=provider or _resolve_active_provider(model),
                to_model=model,
                reason=reason or f"value_error:{exc}",
                success=False,
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        black_box = ctx.get_dep("black_box")
        if black_box is not None and hasattr(black_box, "log_event"):
            try:
                black_box.log_event(
                    "model_switch",
                    f"action={action} provider={provider or '-'} model={model or '-'}",
                )
            except Exception:  # noqa: BLE001
                pass

        active = str(getattr(_mm, "active_model_id", model or provider) or "")
        # Wave 145: persistent history log с фактическим active id (после
        # set_model active = model, после set_provider = "mode:cloud" и т.п.).
        _log_history_entry(
            by=by,
            from_provider=previous_provider,
            from_model=previous_model,
            to_provider=provider or _resolve_active_provider(active),
            to_model=model or active,
            reason=reason or f"action={action}",
            success=switch_success,
        )
        return {
            "ok": True,
            "action": action,
            "provider": provider,
            "model": model,
            "active": active,
        }

    # ---------- GET /admin/models ---------------------------------------------
    @router.get("/admin/models", response_class=HTMLResponse)
    async def admin_models_page() -> HTMLResponse:
        """HTML страница model picker."""
        return HTMLResponse(_MODELS_PAGE_HTML, headers={"Cache-Control": "no-store"})

    return router


# ── LM Studio helpers ───────────────────────────────────────────────────────


async def _list_lm_studio_models(ctx: RouterContext) -> list[dict[str, Any]]:
    """Возвращает список LM Studio моделей (через resolve_local_runtime_truth)."""
    helper = ctx.deps.get("resolve_local_runtime_truth_helper")
    router_obj = ctx.deps.get("router")
    if helper is None or router_obj is None:
        return []
    try:
        truth = helper(router_obj)
        import asyncio as _asyncio

        if _asyncio.iscoroutine(truth):
            truth = await truth
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(truth, dict):
        return []
    loaded = [str(m) for m in (truth.get("loaded_models") or []) if str(m or "").strip()]
    active = str(truth.get("active_model") or "").strip()
    if active and active not in loaded:
        loaded.append(active)
    return [{"id": mid, "loaded": True, "active": mid == active} for mid in loaded]


async def _build_lm_studio_section(ctx: RouterContext, *, active_model: str) -> dict[str, Any]:
    """Собирает provider-секцию LM Studio с live статусами."""
    available = True
    runtime_url = ""
    loaded_active = ""
    loaded_models: list[str] = []
    error = ""
    helper = ctx.deps.get("resolve_local_runtime_truth_helper")
    router_obj = ctx.deps.get("router")
    truth: dict[str, Any] = {}

    if helper is not None and router_obj is not None:
        try:
            value = helper(router_obj)
            import asyncio as _asyncio

            if _asyncio.iscoroutine(value):
                value = await value
            if isinstance(value, dict):
                truth = value
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            available = False

    if truth:
        available = bool(truth.get("runtime_reachable"))
        runtime_url = str(truth.get("runtime_url") or "")
        loaded_active = str(truth.get("active_model") or "")
        loaded_models = [
            str(m).strip() for m in (truth.get("loaded_models") or []) if str(m or "").strip()
        ]
        if truth.get("error"):
            error = str(truth.get("error") or "")

    # Базовый список моделей — известные алиасы из конфига + всё что probe
    # вернул. Хардкодим минимальный набор популярных моделей, чтобы UI был
    # полезен даже когда LM Studio offline.
    known_locals = [
        "gemma-4-26b-a4b-it-optiq",
        "gemma-4-26b-a4b-it-claude-opus-distilled-v2-mlx-mixed_4_6",
        "gemma-3-12b-it-qat",
        "qwen-3-32b-mlx",
    ]
    ids = list(dict.fromkeys([*loaded_models, *known_locals]))
    models: list[dict[str, Any]] = []
    for mid in ids:
        is_loaded = mid in loaded_models
        is_active = bool(active_model and mid == active_model) or (
            mid == loaded_active and bool(loaded_active)
        )
        models.append(
            {
                "id": mid,
                "label": mid,
                "status": "loaded" if is_loaded else "not_loaded",
                "status_detail": "Loaded в LM Studio" if is_loaded else "",
                "is_active": is_active,
                "actions": (["set_primary", "unload"] if is_loaded else ["set_primary", "load"]),
            }
        )

    return {
        "id": "lm-studio",
        "label": "LM Studio (local)",
        "type": "local",
        "available": available,
        "runtime_url": runtime_url,
        "error": error,
        "models": models,
    }


# ── Inline HTML template ────────────────────────────────────────────────────
# Все значения от сервера рендерятся через .textContent / DOM API без
# innerHTML — защищаемся от XSS через провайдер/модель строки (даже если
# в будущем они придут из live LM Studio probe или user input).

_MODELS_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Krab — Model Picker</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    --bg: #0d1117;
    --card: #161b22;
    --border: #30363d;
    --fg: #e6edf3;
    --muted: #8b949e;
    --accent: #58a6ff;
    --ok: #2ea043;
    --warn: #d29922;
    --err: #f85149;
  }
  body { background: var(--bg); color: var(--fg); margin: 0;
         font: 14px -apple-system, BlinkMacSystemFont, sans-serif; }
  header { padding: 16px 24px; border-bottom: 1px solid var(--border);
           display: flex; justify-content: space-between; align-items: center; }
  h1 { margin: 0; font-size: 18px; }
  nav.tabs a { color: var(--muted); text-decoration: none; margin-right: 18px;
               font-size: 13px; padding-bottom: 3px; }
  nav.tabs a:hover { color: var(--accent); }
  nav.tabs a.active { color: var(--accent); border-bottom: 2px solid var(--accent); }
  main { padding: 24px; max-width: 1200px; margin: auto; }
  .current { background: var(--card); border: 1px solid var(--border);
             border-radius: 8px; padding: 16px; margin-bottom: 24px; }
  .current-title { color: var(--muted); font-size: 12px; text-transform: uppercase;
                   letter-spacing: 1px; margin-bottom: 8px; }
  .current-model { font-size: 20px; font-weight: 600; color: var(--accent); }
  .meta { display: flex; gap: 16px; margin-top: 8px; color: var(--muted);
          font-size: 12px; flex-wrap: wrap; }
  .provider { background: var(--card); border: 1px solid var(--border);
              border-radius: 8px; padding: 16px; margin-bottom: 16px; }
  .provider-head { display: flex; justify-content: space-between;
                   align-items: center; margin-bottom: 12px; }
  .provider-label { font-weight: 600; font-size: 16px; }
  .provider-type { font-size: 11px; color: var(--muted); text-transform: uppercase;
                   padding: 2px 8px; border: 1px solid var(--border);
                   border-radius: 12px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 8px 12px; text-align: left;
           border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: normal; font-size: 11px;
       text-transform: uppercase; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px;
           font-size: 11px; font-weight: 600; }
  .b-healthy { background: rgba(46,160,67,0.15); color: var(--ok); }
  .b-loaded { background: rgba(46,160,67,0.15); color: var(--ok); }
  .b-not_loaded { background: rgba(139,148,158,0.15); color: var(--muted); }
  .b-quarantined { background: rgba(248,81,73,0.15); color: var(--err); }
  .b-quota_exhausted { background: rgba(210,153,34,0.15); color: var(--warn); }
  .active-marker { color: var(--accent); margin-left: 8px; font-weight: 600; }
  button { background: var(--card); color: var(--fg); border: 1px solid var(--border);
           padding: 4px 10px; border-radius: 6px; font-size: 12px; cursor: pointer;
           margin-right: 4px; }
  button:hover { border-color: var(--accent); color: var(--accent); }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .toast { position: fixed; bottom: 24px; right: 24px;
           background: var(--card); border: 1px solid var(--border);
           padding: 12px 16px; border-radius: 6px; max-width: 320px;
           opacity: 0; transition: opacity 0.3s; }
  .toast.show { opacity: 1; }
  .toast.err { border-color: var(--err); color: var(--err); }
  .toast.ok { border-color: var(--ok); color: var(--ok); }
  .history { background: var(--card); border: 1px solid var(--border);
             border-radius: 8px; padding: 16px; margin-top: 24px; }
  .history h2 { margin: 0 0 12px 0; font-size: 14px; color: var(--muted);
                text-transform: uppercase; letter-spacing: 1px; }
  .history-item { font-size: 12px; color: var(--muted); padding: 4px 0; }
  .empty { color: var(--muted); font-style: italic; padding: 12px 0; }
  code { font-family: ui-monospace, monospace; font-size: 12px; }
</style>
</head>
<body>
<header>
  <div style="display:flex; align-items:center; gap:18px;">
    <h1>Krab — Model Picker</h1>
    <nav class="tabs">
      <a href="/admin/models" class="active">Models</a>
      <a href="/admin/routing">Routing</a>
      <a href="/admin/ecosystem">Ecosystem</a>
      <a href="/admin/swarm">Swarm</a>
      <a href="/admin/costs">Costs</a>
      <a href="/admin/inbox">Inbox</a>
    </nav>
  </div>
  <div style="color: var(--muted); font-size: 12px;">
    Refresh: <span id="last-refresh">—</span>
  </div>
</header>
<main>
  <div class="current">
    <div class="current-title">Active routing</div>
    <div class="current-model" id="current-model">Загрузка…</div>
    <div class="meta">
      <div>Provider: <span id="current-provider">—</span></div>
      <div>Channel: <span id="current-channel">—</span></div>
      <div>Since: <span id="current-since">—</span></div>
      <div>Status: <span id="current-status">—</span></div>
    </div>
  </div>
  <div id="providers"></div>
  <div class="history">
    <h2>Recent switches</h2>
    <div id="history"></div>
  </div>
</main>
<div class="toast" id="toast"></div>
<script>
'use strict';
const WEB_KEY = new URLSearchParams(location.search).get('token') || '';

function showToast(msg, kind) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + (kind || '');
  setTimeout(() => { t.className = 'toast'; }, 3200);
}

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const k in attrs) {
      if (k === 'class') node.className = attrs[k];
      else if (k === 'text') node.textContent = attrs[k];
      else if (k.startsWith('on') && typeof attrs[k] === 'function') node[k] = attrs[k];
      else node.setAttribute(k, attrs[k]);
    }
  }
  if (children) {
    for (const c of children) {
      if (c) node.appendChild(c);
    }
  }
  return node;
}

function badgeNode(status) {
  return el('span', { class: 'badge b-' + status, text: status });
}

function renderProvider(prov) {
  const card = el('div', { class: 'provider' });
  const head = el('div', { class: 'provider-head' });
  const labelDiv = el('div', { class: 'provider-label', text: prov.label });
  if (!prov.available) {
    labelDiv.appendChild(document.createTextNode(' '));
    labelDiv.appendChild(el('span', { class: 'badge b-quarantined', text: 'unavailable' }));
  }
  head.appendChild(labelDiv);
  head.appendChild(el('div', { class: 'provider-type', text: prov.type }));
  card.appendChild(head);

  const table = el('table');
  const thead = el('thead');
  const headRow = el('tr');
  for (const h of ['Model ID', 'Label', 'Status', 'Actions']) {
    headRow.appendChild(el('th', { text: h }));
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = el('tbody');
  if (!prov.models || prov.models.length === 0) {
    const tr = el('tr');
    const td = el('td', { class: 'empty', text: 'No models', colspan: '4' });
    tr.appendChild(td);
    tbody.appendChild(tr);
  } else {
    for (const m of prov.models) {
      const tr = el('tr');
      const idTd = el('td');
      idTd.appendChild(el('code', { text: m.id }));
      if (m.is_active) {
        idTd.appendChild(el('span', { class: 'active-marker', text: '● active' }));
      }
      tr.appendChild(idTd);
      tr.appendChild(el('td', { text: m.label }));

      const statusTd = el('td');
      statusTd.appendChild(badgeNode(m.status));
      if (m.status_detail) {
        const detail = el('span', { text: ' ' + m.status_detail });
        detail.style.color = 'var(--muted)';
        detail.style.fontSize = '11px';
        detail.style.marginLeft = '8px';
        statusTd.appendChild(detail);
      }
      tr.appendChild(statusTd);

      const actTd = el('td');
      for (const a of (m.actions || [])) {
        if (a === 'set_primary') {
          actTd.appendChild(el('button', {
            text: 'Set primary',
            onclick: () => setPrimary(prov.id, m.id),
          }));
        } else if (a === 'test_ping') {
          actTd.appendChild(el('button', {
            text: 'Test ping',
            onclick: () => testPing(m.id),
          }));
        } else if (a === 'load') {
          actTd.appendChild(el('button', {
            text: 'Load',
            onclick: () => loadModel(m.id),
          }));
        } else if (a === 'unload') {
          actTd.appendChild(el('button', {
            text: 'Unload',
            onclick: () => unloadModel(m.id),
          }));
        }
      }
      tr.appendChild(actTd);
      tbody.appendChild(tr);
    }
  }
  table.appendChild(tbody);
  card.appendChild(table);
  return card;
}

async function refresh() {
  try {
    const resp = await fetch('/api/models/registry');
    const data = await resp.json();
    if (!data.ok) {
      showToast('Registry error: ' + (data.error || 'unknown'), 'err');
      return;
    }
    document.getElementById('current-model').textContent = data.current.model || '—';
    document.getElementById('current-provider').textContent = data.current.provider || '—';
    document.getElementById('current-channel').textContent = data.current.channel || '—';
    document.getElementById('current-since').textContent = data.current.since || '—';
    document.getElementById('current-status').textContent = data.current.status || '—';

    const provBox = document.getElementById('providers');
    provBox.textContent = '';
    for (const p of (data.providers || [])) {
      provBox.appendChild(renderProvider(p));
    }

    const histBox = document.getElementById('history');
    histBox.textContent = '';
    const hist = data.history || [];
    if (hist.length === 0) {
      histBox.appendChild(el('div', { class: 'empty', text: 'No recent switches' }));
    } else {
      // Newest first для UI — server возвращает FIFO chronological order.
      const sorted = hist.slice().reverse();
      for (const h of sorted) {
        const ts = h.timestamp || '';
        const arrow = (h.from || '?') + ' → ' + (h.to || '?');
        const actor = h.actor || 'unknown';
        const item = el('div', { class: 'history-item' });
        const okMark = (h.success === false) ? '❌ ' : '';
        item.appendChild(document.createTextNode(okMark + ts + ' — '));
        item.appendChild(el('code', { text: arrow }));
        item.appendChild(document.createTextNode(' by ' + actor));
        if (h.reason) {
          item.appendChild(document.createTextNode(' [' + h.reason + ']'));
        }
        histBox.appendChild(item);
      }
    }
    document.getElementById('last-refresh').textContent = new Date().toLocaleTimeString();
  } catch (exc) {
    showToast('Refresh failed: ' + exc, 'err');
  }
}

async function setPrimary(provider, model) {
  if (!confirm('Set primary to ' + model + '?')) return;
  try {
    const resp = await fetch('/api/admin/model/switch?token=' + encodeURIComponent(WEB_KEY), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Krab-Web-Key': WEB_KEY },
      body: JSON.stringify({ provider, model }),
    });
    const data = await resp.json();
    if (data.ok) {
      showToast('Switched to ' + model, 'ok');
      refresh();
    } else {
      showToast('Switch failed: ' + (data.detail || data.error || 'unknown'), 'err');
    }
  } catch (exc) {
    showToast('Switch error: ' + exc, 'err');
  }
}

async function testPing(model) {
  try {
    const resp = await fetch('/api/model/status');
    const data = await resp.json();
    const status = data.route && data.route.status || '?';
    showToast('Active: ' + (data.active_model || '?') + ' route=' + status, 'ok');
  } catch (exc) {
    showToast('Ping failed: ' + exc, 'err');
  }
}

async function loadModel(model) {
  try {
    const resp = await fetch('/api/model/local/load-default?token=' + encodeURIComponent(WEB_KEY), {
      method: 'POST',
      headers: { 'X-Krab-Web-Key': WEB_KEY },
    });
    const data = await resp.json();
    if (data.ok) {
      showToast('Load triggered: ' + (data.model || ''), 'ok');
      setTimeout(refresh, 2000);
    } else {
      showToast('Load failed: ' + (data.error || ''), 'err');
    }
  } catch (exc) {
    showToast('Load error: ' + exc, 'err');
  }
}

async function unloadModel(model) {
  try {
    const resp = await fetch('/api/model/local/unload?token=' + encodeURIComponent(WEB_KEY), {
      method: 'POST',
      headers: { 'X-Krab-Web-Key': WEB_KEY },
    });
    const data = await resp.json();
    if (data.ok) {
      showToast('Unloaded' + (data.unloaded ? ': ' + data.unloaded : ''), 'ok');
      setTimeout(refresh, 1500);
    } else {
      showToast('Unload failed', 'err');
    }
  } catch (exc) {
    showToast('Unload error: ' + exc, 'err');
  }
}

refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>
"""
