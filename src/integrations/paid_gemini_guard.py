"""Wave 67: hard runtime guard для блокировки paid AI Studio (Gemini) requests.

Context: до Wave 66 Krab утекал paid AI Studio API key (€40/неделя). Wave 66 fix
переводит rerank/direct-bypass на preferred Vertex AI с graceful fallback на AI Studio.
Wave 67 — defence in depth: hard guard ловит ЛЮБОЙ outgoing HTTP request к
``generativelanguage.googleapis.com`` и блокирует если флаг ON.

Env gates:
    KRAB_BLOCK_PAID_GEMINI_AI_STUDIO=1     (default) — raise PaidGeminiGuardError
    KRAB_BLOCK_PAID_GEMINI_AI_STUDIO=warn  — logs warning, passes through
    KRAB_BLOCK_PAID_GEMINI_AI_STUDIO=0     — disabled (no interception)
    KRAB_PAID_GEMINI_ALLOW_LIST=<csv>      — explicit model paths allow-list

Allowed by default:
    * Gemma модели (gemma-*) — Wave 25-E, free tier через AI Studio normal.
    * Vertex endpoints (aiplatform.googleapis.com) — never intercepted.

Hook:
    register_paid_gemini_guard() once at startup.
    Реализовано через monkey-patch httpx.BaseClient.__init__ для глобального
    охвата (все AsyncClient/Client инстансы, включая ad-hoc внутри third-party).
"""

from __future__ import annotations

import os
import traceback
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

logger = structlog.get_logger(__name__)


# Хост, который мы перехватываем. Только paid AI Studio.
_PAID_HOST = "generativelanguage.googleapis.com"

# Env var, контролирующий поведение guard.
_ENV_GATE = "KRAB_BLOCK_PAID_GEMINI_AI_STUDIO"
_ENV_ALLOW_LIST = "KRAB_PAID_GEMINI_ALLOW_LIST"

# Module-level флаг: был ли patch уже применён (idempotency).
_patched: bool = False
# Сохраняем оригинальный __init__ для восстановления + сквозного вызова.
_orig_async_init: Any = None
_orig_sync_init: Any = None


class PaidGeminiGuardError(RuntimeError):
    """Raised когда guard блокирует paid AI Studio request.

    Args:
        url: URL запроса, который был перехвачен
        model: модель из URL (если удалось распарсить), иначе пустая строка
    """

    def __init__(self, url: str, model: str = "") -> None:
        self.url = url
        self.model = model
        super().__init__(
            f"paid_gemini_guard: blocked request to {url} "
            f"(model={model!r}). "
            "Wave 67 guard: paid AI Studio Gemini blocked by default. "
            "Use Vertex AI bypass (Wave 23-A) instead. "
            f"To override (debugging only): {_ENV_GATE}=warn|0"
        )


def _guard_mode() -> str:
    """Текущий режим guard: 'block' | 'warn' | 'off'."""
    raw = str(os.environ.get(_ENV_GATE, "1")).strip().lower()
    if raw in {"0", "false", "no", "off", "disabled"}:
        return "off"
    if raw == "warn":
        return "warn"
    # Всё остальное (1/true/yes/on/...) трактуется как block.
    return "block"


def _allow_list() -> tuple[str, ...]:
    """CSV из env → tuple. Пустой если не задан."""
    raw = os.environ.get(_ENV_ALLOW_LIST, "")
    if not raw:
        return ()
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def _is_paid_gemini_url(url: str) -> bool:
    """True если URL ведёт к paid AI Studio host."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001 — некорректный URL — не наш кейс
        return False
    return parsed.hostname == _PAID_HOST


def _extract_model_from_url(url: str) -> str:
    """Парсит .../v1beta/models/<model>:generateContent → '<model>'.

    Возвращает пустую строку если не удалось извлечь.
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return ""
    path = parsed.path or ""
    # Path вида /v1beta/models/gemini-3-pro-preview:generateContent
    marker = "/models/"
    idx = path.find(marker)
    if idx < 0:
        return ""
    tail = path[idx + len(marker) :]
    # Отрезаем method suffix (:generateContent / :streamGenerateContent / ...)
    if ":" in tail:
        tail = tail.split(":", 1)[0]
    # Отрезаем хвост (e.g. /...). models/ может быть просто listing.
    if "/" in tail:
        tail = tail.split("/", 1)[0]
    return tail.strip()


def _is_gemma_model(model: str) -> bool:
    """True если модель — Gemma (gemma- prefix). Wave 25-E: AI Studio free tier."""
    if not model:
        return False
    return model.lower().startswith("gemma-")


def _is_allowed_model(model: str) -> bool:
    """Проверка allow-list (Gemma + explicit env CSV)."""
    if _is_gemma_model(model):
        return True
    if not model:
        return False
    allow = _allow_list()
    if not allow:
        return False
    # Точное совпадение модели или substring (e.g. "gemini-2.5-flash" matches
    # config с "gemini-2.5" wildcard-like).
    return any(model == entry or entry in model for entry in allow)


def _build_traceback_hint(depth: int = 8) -> str:
    """Краткий traceback frame для post-mortem (где в codebase это пришло)."""
    # Берём текущий stack (без guard'овых фреймов).
    frames = traceback.extract_stack()
    # Отфильтровываем guard собственный stack и httpx internals (не интересно).
    skip_paths = (
        "paid_gemini_guard.py",
        "site-packages/httpx",
        "httpcore",
    )
    rel = [
        f"{f.filename}:{f.lineno} {f.name}"
        for f in frames
        if not any(s in (f.filename or "") for s in skip_paths)
    ]
    return " <- ".join(rel[-depth:]) if rel else ""


def _trigger(url: str) -> None:
    """Внутренний trigger: парсит URL, проверяет allow-list, raise/warn по mode."""
    mode = _guard_mode()
    if mode == "off":
        return  # disabled — ничего не делаем

    model = _extract_model_from_url(url)

    # Allow-list (Gemma + KRAB_PAID_GEMINI_ALLOW_LIST) — пропускаем.
    if _is_allowed_model(model):
        logger.debug(
            "paid_gemini_guard_allowed",
            url=url,
            model=model,
            reason="allow_list",
        )
        return

    trace_hint = _build_traceback_hint()

    if mode == "warn":
        logger.warning(
            "paid_gemini_guard_warning",
            url=url,
            model=model,
            mode=mode,
            trace=trace_hint,
            detail="paid AI Studio request detected; pass-through (warn mode)",
        )
        return

    # mode == "block" — log + raise.
    logger.error(
        "paid_gemini_guard_triggered",
        url=url,
        model=model,
        mode=mode,
        trace=trace_hint,
        detail="blocking paid AI Studio request (Wave 67); use Vertex AI bypass",
    )
    raise PaidGeminiGuardError(url=url, model=model)


def _request_hook_sync(request: httpx.Request) -> None:
    """httpx sync request hook — вызывается перед каждым request.

    Если URL ведёт к paid AI Studio host — _trigger() (raise или warn).
    """
    try:
        url_str = str(request.url)
    except Exception:  # noqa: BLE001
        return
    if _is_paid_gemini_url(url_str):
        _trigger(url_str)


async def _request_hook_async(request: httpx.Request) -> None:
    """httpx async request hook — pass-through к sync impl."""
    _request_hook_sync(request)


def _install_hooks_on_client(client: Any) -> None:
    """Добавляет наши hooks в существующий client (AsyncClient/Client).

    httpx event_hooks — это dict с ключами 'request' и 'response', где значение
    — list callables. Мы append'им свои, сохраняя пользовательские (если были).
    """
    try:
        hooks = client.event_hooks or {}
    except Exception:  # noqa: BLE001
        return

    # Дедуплицируем: если уже добавлен — пропускаем (защита от повторного init).
    request_hooks = list(hooks.get("request") or [])
    is_async = isinstance(client, httpx.AsyncClient)
    target_hook = _request_hook_async if is_async else _request_hook_sync

    already_installed = any(
        getattr(h, "__name__", "") == target_hook.__name__ for h in request_hooks
    )
    if already_installed:
        return

    request_hooks.append(target_hook)
    hooks["request"] = request_hooks
    client.event_hooks = hooks


def _patched_async_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
    """Wrapper для httpx.AsyncClient.__init__: после ориг init добавляет hooks."""
    assert _orig_async_init is not None
    _orig_async_init(self, *args, **kwargs)
    try:
        _install_hooks_on_client(self)
    except Exception as exc:  # noqa: BLE001 — guard не должен ронять создание клиента
        logger.warning("paid_gemini_guard_install_failed", error=str(exc))


def _patched_sync_init(self: httpx.Client, *args: Any, **kwargs: Any) -> None:
    """Wrapper для httpx.Client.__init__: после ориг init добавляет hooks."""
    assert _orig_sync_init is not None
    _orig_sync_init(self, *args, **kwargs)
    try:
        _install_hooks_on_client(self)
    except Exception as exc:  # noqa: BLE001
        logger.warning("paid_gemini_guard_install_failed", error=str(exc))


def register_paid_gemini_guard() -> bool:
    """Устанавливает hard runtime guard через monkey-patch httpx.

    Идемпотентно: повторный вызов — no-op.

    Returns:
        True если patch применён (или уже был применён ранее), False при сбое.
    """
    global _patched, _orig_async_init, _orig_sync_init

    if _patched:
        return True

    try:
        _orig_async_init = httpx.AsyncClient.__init__
        _orig_sync_init = httpx.Client.__init__
        httpx.AsyncClient.__init__ = _patched_async_init  # type: ignore[method-assign]
        httpx.Client.__init__ = _patched_sync_init  # type: ignore[method-assign]
        _patched = True
        logger.info(
            "paid_gemini_guard_registered",
            mode=_guard_mode(),
            allow_list=_allow_list(),
            host=_PAID_HOST,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("paid_gemini_guard_register_failed", error=str(exc))
        return False


def unregister_paid_gemini_guard() -> None:
    """Откатывает monkey-patch (используется в тестах для изоляции)."""
    global _patched, _orig_async_init, _orig_sync_init

    if not _patched:
        return

    if _orig_async_init is not None:
        httpx.AsyncClient.__init__ = _orig_async_init  # type: ignore[method-assign]
    if _orig_sync_init is not None:
        httpx.Client.__init__ = _orig_sync_init  # type: ignore[method-assign]

    _orig_async_init = None
    _orig_sync_init = None
    _patched = False
    logger.debug("paid_gemini_guard_unregistered")


# --------------------------------------------------------------------------
# Re-exports для тестов / интроспекции.
# --------------------------------------------------------------------------

__all__ = [
    "PaidGeminiGuardError",
    "register_paid_gemini_guard",
    "unregister_paid_gemini_guard",
]
