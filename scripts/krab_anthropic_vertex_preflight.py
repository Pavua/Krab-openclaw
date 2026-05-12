#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 104: ежедневная Anthropic Vertex preflight + auto-resume по quota.

Цель: Session 45 closed billing leak — переключили Gemini на Vertex, а Anthropic
Vertex quota всё ещё pending Google Sales POC (cases #70886393, #70886496 open).
Wave 65-D blacklist'нул claude-sonnet-4-5 в routing. Этот скрипт раз в день
дёргает minimal Anthropic Vertex call. Если quota approved (200) — снимаем
blacklist + Telegram-notify owner.

Логика:
1. Через anthropic.AnthropicVertex (ADC + quota project caramel-anvil-492816-t5,
   region us-east5) делаем messages.create(model=claude-sonnet-4-5, max_tokens=1).
2. Классификация результата:
   - HTTP 200 / success → ok
   - HTTP 403 / PERMISSION_DENIED / NotFoundError → blocked
   - HTTP 429 / RESOURCE_EXHAUSTED → blocked
   - другие 4xx/5xx / Exception → unknown
3. Persist `~/.openclaw/krab_runtime_state/anthropic_vertex_status.json`:
   {timestamp, vertex_quota_status, error, transitioned}.
4. На переходе blocked→ok:
   - log `anthropic_vertex_quota_approved_auto_detected`,
   - снимаем Wave 65-D blacklist (state file flip),
   - Telegram-notify owner.

Env (из .env):
- KRAB_ANTHROPIC_VERTEX_PROJECT (default caramel-anvil-492816-t5),
- KRAB_ANTHROPIC_VERTEX_REGION (default us-east5),
- KRAB_ANTHROPIC_VERTEX_PROBE_MODEL (default claude-sonnet-4-5),
- KRAB_ANTHROPIC_VERTEX_AUTO_RESUME (default 1 — снимать blacklist),
- KRAB_ANTHROPIC_VERTEX_ALERT_TELEGRAM (default 1),
- OPENCLAW_TELEGRAM_BOT_TOKEN, OWNER_USER_IDS.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# ─── .env loader (LaunchAgent сценарий) ─────────────────────────────────────


def _load_dotenv() -> None:
    """Читает .env в os.environ для LaunchAgent-запуска без shell."""
    env_file = os.environ.get(
        "ENV_FILE",
        str(Path(__file__).parent.parent / ".env"),
    )
    path = Path(env_file)
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv()


# ─── Конфигурация ────────────────────────────────────────────────────────────

PROJECT: str = os.environ.get("KRAB_ANTHROPIC_VERTEX_PROJECT", "caramel-anvil-492816-t5")
REGION: str = os.environ.get("KRAB_ANTHROPIC_VERTEX_REGION", "us-east5")
PROBE_MODEL: str = os.environ.get("KRAB_ANTHROPIC_VERTEX_PROBE_MODEL", "claude-sonnet-4-5")
AUTO_RESUME: bool = os.environ.get("KRAB_ANTHROPIC_VERTEX_AUTO_RESUME", "1").strip() in {
    "1",
    "true",
    "yes",
    "on",
}
ALERT_TELEGRAM: bool = os.environ.get("KRAB_ANTHROPIC_VERTEX_ALERT_TELEGRAM", "1").strip() in {
    "1",
    "true",
    "yes",
    "on",
}

TG_TOKEN: str | None = os.environ.get("OPENCLAW_TELEGRAM_BOT_TOKEN")
_OWNER_LIST: str = os.environ.get("OWNER_USER_IDS", "")
TG_OWNER: str = (
    _OWNER_LIST.split(",")[0].strip() if _OWNER_LIST else os.environ.get("OWNER_NOTIFY_CHAT_ID", "")
)

DEFAULT_STATE_DIR = Path.home() / ".openclaw" / "krab_runtime_state"
STATE_DIR: Path = Path(os.environ.get("KRAB_ANTHROPIC_VERTEX_STATE_DIR", str(DEFAULT_STATE_DIR)))
STATUS_FILE: Path = STATE_DIR / "anthropic_vertex_status.json"
BLACKLIST_FILE: Path = STATE_DIR / "anthropic_vertex_blacklist.json"
LOG_FILE: Path = STATE_DIR / "anthropic_vertex_preflight.log"

USER_AGENT = "krab-anthropic-vertex-preflight/wave-104"


# ─── Логирование ─────────────────────────────────────────────────────────────


def _log_to(file: Path, msg: str) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with file.open("a") as fh:
        fh.write(f"[{ts}] {msg}\n")


def log_info(msg: str) -> None:
    _log_to(LOG_FILE, msg)


def log_err(msg: str) -> None:
    _log_to(LOG_FILE, f"ERR {msg}")
    print(f"ERR {msg}", file=sys.stderr, flush=True)


# ─── Статус-классификация ───────────────────────────────────────────────────


def classify_exception(exc: BaseException) -> tuple[str, str]:
    """Маппит Anthropic/Vertex exception → (status, short_error).

    status ∈ {ok, blocked, unknown}.
    """
    cls_name = type(exc).__name__
    text = str(exc)
    lowered = text.lower()

    # Anthropic SDK типы: PermissionDeniedError, RateLimitError, etc.
    if "permissiondenied" in cls_name.lower() or "permission_denied" in lowered:
        return "blocked", f"{cls_name}: {text[:200]}"
    if "ratelimit" in cls_name.lower() or "resource_exhausted" in lowered:
        return "blocked", f"{cls_name}: {text[:200]}"
    if "notfound" in cls_name.lower():
        # 404 на claude-sonnet-4-5 в Vertex region обычно = quota/доступ
        return "blocked", f"{cls_name}: {text[:200]}"

    # HTTP-style status codes в тексте
    if "403" in text or "429" in text:
        return "blocked", f"{cls_name}: {text[:200]}"
    if "quota" in lowered:
        return "blocked", f"{cls_name}: {text[:200]}"

    return "unknown", f"{cls_name}: {text[:200]}"


# ─── Vertex probe ───────────────────────────────────────────────────────────


def probe_anthropic_vertex(
    *,
    project: str = PROJECT,
    region: str = REGION,
    model: str = PROBE_MODEL,
    client_factory: Any = None,
) -> dict[str, Any]:
    """Делает minimal Anthropic-via-Vertex call.

    Args:
        client_factory: callable() → AnthropicVertex-like client (для тестов).
            None → реальный anthropic.AnthropicVertex.

    Returns:
        {status: ok|blocked|unknown, error: str | None, model, region, project}.
    """
    if client_factory is None:

        def _default_factory() -> Any:
            from anthropic import AnthropicVertex  # noqa: PLC0415

            return AnthropicVertex(region=region, project_id=project)

        client_factory = _default_factory

    try:
        client = client_factory()
        resp = client.messages.create(
            model=model,
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
        # Anthropic SDK: resp.content список. Любой не-Exception ответ = ok.
        _ = resp.content if hasattr(resp, "content") else None
        return {
            "status": "ok",
            "error": None,
            "model": model,
            "region": region,
            "project": project,
        }
    except Exception as exc:  # noqa: BLE001 — нам нужна полная классификация
        status, short = classify_exception(exc)
        return {
            "status": status,
            "error": short,
            "model": model,
            "region": region,
            "project": project,
        }


# ─── Persistence ────────────────────────────────────────────────────────────


def load_status(path: Path = STATUS_FILE) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log_err(f"status_load error_type={type(exc).__name__} error={exc}")
        return None


def save_status(payload: dict[str, Any], path: Path = STATUS_FILE) -> None:
    """Атомарно пишет status snapshot."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def remove_blacklist(path: Path = BLACKLIST_FILE) -> bool:
    """Снимает Wave 65-D blacklist. True если файл был удалён."""
    if not path.exists():
        # Backwards-compat: пишем явный allow-marker, чтобы model_router увидел.
        path.parent.mkdir(parents=True, exist_ok=True)
        marker = {
            "blacklist": [],
            "removed_at": datetime.now(timezone.utc).isoformat(),
            "reason": "wave_104_auto_detected_quota_approved",
        }
        try:
            path.write_text(json.dumps(marker, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            log_err(f"blacklist_write error_type={type(exc).__name__} error={exc}")
            return False
        return True
    try:
        path.unlink()
        return True
    except OSError as exc:
        log_err(f"blacklist_unlink error_type={type(exc).__name__} error={exc}")
        return False


# ─── Telegram ───────────────────────────────────────────────────────────────


def send_telegram_alert(
    text: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 10.0,
) -> bool:
    if not TG_TOKEN or not TG_OWNER:
        log_err("telegram_alert missing_creds")
        return False
    payload = {
        "chat_id": TG_OWNER,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=timeout)
    try:
        resp = client.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json=payload)
        if resp.status_code != 200:
            log_err(f"telegram_alert status={resp.status_code}")
            return False
        return True
    except httpx.RequestError as exc:
        log_err(f"telegram_alert error_type={type(exc).__name__} error={exc}")
        return False
    finally:
        if owns_client and client is not None:
            client.close()


# ─── Main flow ──────────────────────────────────────────────────────────────


def run_preflight(
    *,
    client_factory: Any = None,
    status_path: Path = STATUS_FILE,
    blacklist_path: Path = BLACKLIST_FILE,
    auto_resume: bool = AUTO_RESUME,
    alert_telegram: bool = ALERT_TELEGRAM,
    telegram_sender: Any = None,
) -> dict[str, Any]:
    """Полный цикл: probe → classify → persist → detect transition → resume.

    Returns: snapshot persisted в status_path.
    """
    probe = probe_anthropic_vertex(client_factory=client_factory)
    prev = load_status(status_path)
    prev_status = prev.get("vertex_quota_status") if prev else None
    new_status = probe["status"]

    transitioned = prev_status == "blocked" and new_status == "ok"

    snapshot: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "vertex_quota_status": new_status,
        "error": probe.get("error"),
        "model": probe.get("model"),
        "region": probe.get("region"),
        "project": probe.get("project"),
        "previous_status": prev_status,
        "transitioned_blocked_to_ok": transitioned,
        "blacklist_removed": False,
        "telegram_sent": False,
    }

    if transitioned:
        log_info(
            f"anthropic_vertex_quota_approved_auto_detected "
            f"model={probe.get('model')} region={probe.get('region')}"
        )
        if auto_resume:
            removed = remove_blacklist(blacklist_path)
            snapshot["blacklist_removed"] = removed
            log_info(f"blacklist_removed={removed} path={blacklist_path}")

        if alert_telegram:
            txt = (
                "<b>Anthropic Vertex quota approved</b>\n"
                f"model: <code>{probe.get('model')}</code>\n"
                f"region: <code>{probe.get('region')}</code>\n"
                f"project: <code>{probe.get('project')}</code>\n"
                "Wave 65-D blacklist auto-removed. Routing resumed."
            )
            sender = telegram_sender or send_telegram_alert
            try:
                snapshot["telegram_sent"] = bool(sender(txt))
            except Exception as exc:  # noqa: BLE001
                log_err(f"telegram_sender error_type={type(exc).__name__} error={exc}")
                snapshot["telegram_sent"] = False
    else:
        log_info(
            f"preflight_done status={new_status} prev={prev_status} "
            f"error={probe.get('error') or '-'}"
        )

    save_status(snapshot, status_path)
    return snapshot


def main() -> int:
    snapshot = run_preflight()
    print(json.dumps(snapshot, indent=2, ensure_ascii=False))
    # Exit 0 в любом случае — отсутствие quota это ожидаемое состояние,
    # не failure. Sentry/LaunchAgent должны видеть зелёный.
    return 0


if __name__ == "__main__":
    sys.exit(main())
