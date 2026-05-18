#!/usr/bin/env python3
"""Krab local primary bypass smoke test (S64 W10).

End-to-end: switches primary к lm-studio-local/gemma → sends test message
via test_ping endpoint → measures latency → verifies log markers →
restores primary к codex-cli/gpt-5.5.

Usage:
    python scripts/krab_bypass_smoke_test.py [--text "unique test text"]
    python scripts/krab_bypass_smoke_test.py --no-restore  # leave at local
    python scripts/krab_bypass_smoke_test.py --panel http://127.0.0.1:8080

Exit codes:
    0 — all checks green
    1 — one or more checks failed
    2 — panel unreachable / preflight failed
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

# ── Defaults ────────────────────────────────────────────────────────────────

DEFAULT_PANEL = "http://127.0.0.1:8080"
DEFAULT_TARGET = "lm-studio-local/gemma-4-26b-a4b-it@4bit"
DEFAULT_RESTORE = "codex-cli/gpt-5.5"
DEFAULT_LOG = Path(__file__).resolve().parent.parent / "logs" / "krab_launchd.out.log"
LOG_MARKER = "local_primary_bypass_ok"
HTTP_TIMEOUT = 60.0

# ── Pretty print ────────────────────────────────────────────────────────────


def ok(msg: str) -> None:
    print(f"\033[32mOK\033[0m  {msg}")


def fail(msg: str) -> None:
    print(f"\033[31mFAIL\033[0m {msg}")


def info(msg: str) -> None:
    print(f"\033[36m..\033[0m  {msg}")


# ── HTTP helpers ────────────────────────────────────────────────────────────


def _web_key() -> str:
    return os.getenv("WEB_API_KEY", "").strip()


def _auth_params() -> dict[str, str]:
    key = _web_key()
    return {"token": key} if key else {}


def get_current_primary(panel: str) -> str:
    """Read active model from /api/models/registry."""
    resp = httpx.get(f"{panel}/api/models/registry", timeout=HTTP_TIMEOUT, params=_auth_params())
    resp.raise_for_status()
    data = resp.json()
    return str((data.get("current") or {}).get("model") or "")


def switch_model(panel: str, model_id: str, reason: str = "smoke_test") -> dict[str, Any]:
    """POST /api/admin/model/switch."""
    provider = model_id.split("/", 1)[0] if "/" in model_id else ""
    payload = {
        "provider": provider,
        "model": model_id,
        "reason": reason,
        "by": "smoke_test",
    }
    resp = httpx.post(
        f"{panel}/api/admin/model/switch",
        json=payload,
        timeout=HTTP_TIMEOUT,
        params=_auth_params(),
    )
    resp.raise_for_status()
    return resp.json()


def test_ping(panel: str, model_id: str) -> dict[str, Any]:
    """POST /api/admin/model/test_ping — real probe, returns latency_ms."""
    resp = httpx.post(
        f"{panel}/api/admin/model/test_ping",
        json={"model_id": model_id},
        timeout=HTTP_TIMEOUT,
        params=_auth_params(),
    )
    resp.raise_for_status()
    return resp.json()


def check_log_markers(
    log_path: Path, marker: str = LOG_MARKER, since_ts: float | None = None
) -> tuple[bool, str]:
    """Grep log for marker. Returns (found, last_match_line)."""
    if not log_path.exists():
        return False, f"log_not_found:{log_path}"
    try:
        with log_path.open("rb") as fh:
            # Tail last 200 KB — достаточно для свежих маркеров.
            try:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 200 * 1024))
            except OSError:
                pass
            tail = fh.read().decode("utf-8", errors="replace")
        matches = [ln for ln in tail.splitlines() if marker in ln]
        if not matches:
            return False, ""
        return True, matches[-1][:200]
    except OSError as exc:
        return False, f"log_read_error:{exc}"


def check_verifier_state(panel: str) -> dict[str, Any]:
    """GET /api/admin/local-draft-verifier-stats."""
    resp = httpx.get(
        f"{panel}/api/admin/local-draft-verifier-stats",
        timeout=HTTP_TIMEOUT,
        params=_auth_params(),
    )
    resp.raise_for_status()
    return resp.json()


# ── Main flow ───────────────────────────────────────────────────────────────


def run_smoke(
    panel: str,
    target: str,
    restore_to: str | None,
    log_path: Path,
    test_text: str,
) -> int:
    info(f"panel={panel} target={target} restore={'<skip>' if restore_to is None else restore_to}")
    failures = 0
    saved_primary = ""

    # 1) Preflight + save current primary
    try:
        saved_primary = get_current_primary(panel)
        ok(f"current primary={saved_primary or '<empty>'}")
    except Exception as exc:  # noqa: BLE001
        fail(f"preflight failed: {exc}")
        return 2

    # 2) Switch to local
    try:
        sw = switch_model(panel, target, reason=f"smoke:{test_text}")
        ok(f"switched → {sw.get('active') or target} (action={sw.get('action')})")
    except Exception as exc:  # noqa: BLE001
        fail(f"switch failed: {exc}")
        failures += 1

    # 3) Test ping
    started = time.time()
    try:
        ping = test_ping(panel, target)
        latency = int(ping.get("latency_ms") or 0)
        preview = (ping.get("response_preview") or "")[:60]
        ok(f"ping ok latency={latency}ms preview={preview!r}")
    except Exception as exc:  # noqa: BLE001
        fail(f"test_ping failed: {exc}")
        failures += 1

    # 4) Log marker check (best-effort — sample_rate может быть < 1.0)
    found, line = check_log_markers(log_path, since_ts=started)
    if found:
        ok(f"log marker found: {line}")
    else:
        # Не считаем за hard fail — verifier sampling может пропустить probe.
        info(f"log marker '{LOG_MARKER}' not found in {log_path.name} (best-effort)")

    # 5) Verifier state
    try:
        v = check_verifier_state(panel)
        stats = v.get("stats") or {}
        ok(
            "verifier enabled="
            f"{v.get('enabled')} total_24h={stats.get('total_verified_24h')} "
            f"mean_score={stats.get('mean_score')}"
        )
    except Exception as exc:  # noqa: BLE001
        fail(f"verifier state check failed: {exc}")
        failures += 1

    # 6) Restore
    if restore_to is not None:
        try:
            switch_model(panel, restore_to, reason="smoke_test_restore")
            ok(f"restored primary → {restore_to}")
        except Exception as exc:  # noqa: BLE001
            fail(f"restore failed: {exc} — saved={saved_primary}")
            failures += 1
    else:
        info("--no-restore: leaving primary at local target")

    return 0 if failures == 0 else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--panel", default=DEFAULT_PANEL, help="Owner panel base URL")
    p.add_argument("--target", default=DEFAULT_TARGET, help="local model id to switch to")
    p.add_argument(
        "--restore-to",
        default=DEFAULT_RESTORE,
        help="model id to restore after test (default: codex-cli/gpt-5.5)",
    )
    p.add_argument(
        "--no-restore",
        action="store_true",
        help="leave primary at local target (skip restore step)",
    )
    p.add_argument("--log", default=str(DEFAULT_LOG), help="path to krab runtime log")
    p.add_argument(
        "--text",
        default=f"smoke-{int(time.time())}",
        help="unique test text (timestamp suffix avoids repetition_guard)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    restore_to = None if args.no_restore else args.restore_to
    return run_smoke(
        panel=args.panel.rstrip("/"),
        target=args.target,
        restore_to=restore_to,
        log_path=Path(args.log),
        test_text=args.text,
    )


if __name__ == "__main__":
    sys.exit(main())
