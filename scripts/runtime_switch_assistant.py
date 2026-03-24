#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
runtime_switch_assistant.py — безопасный assistant для переключения runtime между macOS-учётками.

Что делает:
1) Проверяет текущий ownership runtime.
2) Не даёт стартовать поверх чужого runtime.
3) Умеет безопасно "заморозить" текущую учётку: stop + fresh switchover/handoff.
4) Умеет вернуть runtime на `pablito`, если foreign runtime не мешает.

Зачем:
- пользователю не нужно помнить порядок check -> stop -> export -> start;
- helper сам удерживает безопасную последовательность и пишет свежие артефакты;
- при работе с нескольких учёток минимизируется ручная рутина и риск сломать live runtime.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_current_account_runtime import build_runtime_report


OPS_DIR = ROOT / "artifacts" / "ops"
ARTIFACTS_DIR = ROOT / "artifacts"
HANDOFF_API_URL = "http://127.0.0.1:8080/api/runtime/handoff"


def _auto_export_before_switch() -> dict[str, Any]:
    """Calls the runtime handoff API and saves a snapshot before account switch.

    Never raises — if Краб is not running or the call fails, returns exported=False
    with an error description so the caller can silently continue.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = ARTIFACTS_DIR / f"auto_handoff_{timestamp}.json"
    try:
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(HANDOFF_API_URL, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            raw = resp.read()
        data = json.loads(raw)
        dest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"exported": True, "path": str(dest), "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"exported": False, "path": str(dest), "error": str(exc)}


def _run_shell(path: Path) -> dict[str, Any]:
    """Запускает .command/.sh helper и возвращает structured результат."""
    try:
        proc = subprocess.run(
            [str(path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "path": str(path),
            "returncode": 1,
            "stdout": "",
            "stderr": str(exc),
        }
    return {
        "ok": proc.returncode == 0,
        "path": str(path),
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _steps_for_runtime_switch(mode: str) -> dict[str, Path]:
    """Возвращает канонические launchers для start/stop/export/switchover."""
    return {
        "start": ROOT / "Start Full Ecosystem.command",
        "stop": ROOT / "Stop Full Ecosystem.command",
        "handoff": ROOT / "Export Handoff Bundle.command",
        "switchover": ROOT / "Prepare Shared Repo Switchover.command",
        "publish_active": ROOT / "Publish Active Shared Worktree.command",
        "drift": ROOT / "Check Shared Repo Drift.command",
        "readiness": ROOT / "Check New Account Readiness.command",
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    """Строит короткий human-readable отчёт для последнего действия assistant'а."""
    action = str(payload.get("action") or "unknown")
    ok = bool(payload.get("ok"))
    runtime = dict(payload.get("runtime_before") or {})
    ownership = dict(runtime.get("ownership") or {})
    lines = [
        "# Runtime Switch Assistant",
        "",
        f"- Generated (UTC): `{datetime.now(timezone.utc).isoformat(timespec='seconds')}`",
        f"- action: `{action}`",
        f"- ok: `{ok}`",
        f"- current_user: `{((runtime.get('current_account') or {}).get('user') or 'unknown')}`",
        f"- runtime_verdict_before: `{ownership.get('verdict', 'unknown')}`",
        f"- foreign_runtime_detected: `{ownership.get('foreign_runtime_detected', False)}`",
        "",
        "## Recommendations",
    ]
    for item in payload.get("recommendations") or []:
        lines.append(f"- {item}")

    executed = payload.get("executed_steps") or []
    if executed:
        lines.extend(["", "## Executed steps"])
        for item in executed:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- `{item.get('name')}`: ok=`{item.get('ok')}` rc=`{item.get('returncode')}` path=`{item.get('path')}`"
            )
    return "\n".join(lines) + "\n"


def _finalize(payload: dict[str, Any]) -> dict[str, Any]:
    """Пишет latest JSON/MD артефакты assistant'а."""
    latest_json = OPS_DIR / "runtime_switch_assistant_latest.json"
    latest_md = OPS_DIR / "runtime_switch_assistant_latest.md"
    _write_json(latest_json, payload)
    _write_text(latest_md, _render_markdown(payload))
    payload["artifacts"] = {
        "latest_json": str(latest_json),
        "latest_md": str(latest_md),
    }
    _write_json(latest_json, payload)
    return payload


def action_status() -> dict[str, Any]:
    """Снимает status без побочных эффектов."""
    runtime_before = build_runtime_report()
    return _finalize(
        {
            "ok": True,
            "action": "status",
            "runtime_before": runtime_before,
            "executed_steps": [],
            "recommendations": list(runtime_before.get("recommendations") or []),
        }
    )


def action_switch_to_current() -> dict[str, Any]:
    """Поднимает runtime для текущей учётки, если не обнаружен foreign runtime."""
    runtime_before = build_runtime_report()
    ownership = dict(runtime_before.get("ownership") or {})
    steps = _steps_for_runtime_switch("switch")
    executed: list[dict[str, Any]] = []
    recommendations = list(runtime_before.get("recommendations") or [])

    if bool(ownership.get("foreign_runtime_detected")):
        recommendations.append("Assistant отказался запускать runtime поверх чужой активной учётки.")
        return _finalize(
            {
                "ok": False,
                "action": "switch_to_current",
                "runtime_before": runtime_before,
                "executed_steps": executed,
                "recommendations": recommendations,
            }
        )

    if str(ownership.get("verdict") or "") == "current_account_runtime_active":
        recommendations.append("Runtime уже принадлежит текущей учётке; дополнительный start не нужен.")
        return _finalize(
            {
                "ok": True,
                "action": "switch_to_current",
                "runtime_before": runtime_before,
                "executed_steps": executed,
                "recommendations": recommendations,
            }
        )

    for name in ("readiness", "drift", "switchover"):
        result = _run_shell(steps[name])
        result["name"] = name
        executed.append(result)
        if not result["ok"]:
            recommendations.append(f"Шаг `{name}` завершился с ошибкой; запуск runtime остановлен.")
            return _finalize(
                {
                    "ok": False,
                    "action": "switch_to_current",
                    "runtime_before": runtime_before,
                    "executed_steps": executed,
                    "recommendations": recommendations,
                }
            )

    start_result = _run_shell(steps["start"])
    start_result["name"] = "start"
    executed.append(start_result)
    runtime_after = build_runtime_report()
    if not start_result["ok"]:
        recommendations.append("Start Full Ecosystem завершился с ошибкой; смотри stdout/stderr в latest JSON.")
    else:
        recommendations.append("Runtime запущен для текущей учётки. Перед live-проверками перечитай ownership report.")
    return _finalize(
        {
            "ok": bool(start_result.get("ok")),
            "action": "switch_to_current",
            "runtime_before": runtime_before,
            "runtime_after": runtime_after,
            "executed_steps": executed,
            "recommendations": recommendations,
        }
    )


def action_freeze_current() -> dict[str, Any]:
    """Останавливает runtime текущей учётки и сразу собирает свежие handoff артефакты."""
    auto_export = _auto_export_before_switch()
    runtime_before = build_runtime_report()
    ownership = dict(runtime_before.get("ownership") or {})
    steps = _steps_for_runtime_switch("freeze")
    executed: list[dict[str, Any]] = []
    recommendations = list(runtime_before.get("recommendations") or [])

    if bool(ownership.get("foreign_runtime_detected")):
        recommendations.append("Foreign runtime уже обнаружен; freeze текущей учётки пропущен.")
        return _finalize(
            {
                "ok": False,
                "action": "freeze_current",
                "auto_export": auto_export,
                "runtime_before": runtime_before,
                "executed_steps": executed,
                "recommendations": recommendations,
            }
        )

    if str(ownership.get("verdict") or "") == "current_account_runtime_active":
        stop_result = _run_shell(steps["stop"])
        stop_result["name"] = "stop"
        executed.append(stop_result)
    else:
        recommendations.append("Runtime текущей учётки и так не активен; stop пропущен.")

    for name in ("switchover", "publish_active", "handoff"):
        result = _run_shell(steps[name])
        result["name"] = name
        executed.append(result)

    runtime_after = build_runtime_report()
    recommendations.append("Текущая учётка заморожена настолько, насколько это возможно без трогания чужого runtime.")
    return _finalize(
        {
            "ok": all(bool(item.get("ok")) for item in executed) if executed else True,
            "action": "freeze_current",
            "auto_export": auto_export,
            "runtime_before": runtime_before,
            "runtime_after": runtime_after,
            "executed_steps": executed,
            "recommendations": recommendations,
        }
    )


def action_return_to_pablito() -> dict[str, Any]:
    """Возвращает runtime на `pablito`, если сейчас нет foreign runtime."""
    auto_export = _auto_export_before_switch()
    runtime_before = build_runtime_report()
    current_user = str(((runtime_before.get("current_account") or {}).get("user")) or "").strip()
    ownership = dict(runtime_before.get("ownership") or {})
    steps = _steps_for_runtime_switch("return")
    executed: list[dict[str, Any]] = []
    recommendations = list(runtime_before.get("recommendations") or [])

    if current_user != "pablito":
        recommendations.append("Возврат на pablito можно выполнять только из учётки pablito.")
        return _finalize(
            {
                "ok": False,
                "action": "return_to_pablito",
                "auto_export": auto_export,
                "runtime_before": runtime_before,
                "executed_steps": executed,
                "recommendations": recommendations,
            }
        )

    if bool(ownership.get("foreign_runtime_detected")):
        recommendations.append("Обнаружен foreign runtime; сначала останови его из исходной учётки и только потом делай reclaim.")
        return _finalize(
            {
                "ok": False,
                "action": "return_to_pablito",
                "auto_export": auto_export,
                "runtime_before": runtime_before,
                "executed_steps": executed,
                "recommendations": recommendations,
            }
        )

    for name in ("readiness", "drift", "switchover"):
        result = _run_shell(steps[name])
        result["name"] = name
        executed.append(result)
        if not result["ok"]:
            recommendations.append(f"Шаг `{name}` завершился с ошибкой; reclaim остановлен.")
            return _finalize(
                {
                    "ok": False,
                    "action": "return_to_pablito",
                    "auto_export": auto_export,
                    "runtime_before": runtime_before,
                    "executed_steps": executed,
                    "recommendations": recommendations,
                }
            )

    start_result = _run_shell(steps["start"])
    start_result["name"] = "start"
    executed.append(start_result)
    handoff_result = _run_shell(steps["handoff"])
    handoff_result["name"] = "handoff"
    executed.append(handoff_result)
    runtime_after = build_runtime_report()
    recommendations.append("Если runtime поднялся на pablito, следующим шагом можно запускать Release Gate.command.")
    return _finalize(
        {
            "ok": all(bool(item.get("ok")) for item in executed),
            "action": "return_to_pablito",
            "auto_export": auto_export,
            "runtime_before": runtime_before,
            "runtime_after": runtime_after,
            "executed_steps": executed,
            "recommendations": recommendations,
        }
    )


def main() -> int:
    """CLI entrypoint assistant'а."""
    parser = argparse.ArgumentParser(description="Безопасный assistant для переключения runtime между macOS-учётками.")
    parser.add_argument(
        "action",
        choices=("status", "switch-to-current", "freeze-current", "return-to-pablito"),
        help="Какое действие выполнить.",
    )
    args = parser.parse_args()

    if args.action == "status":
        payload = action_status()
    elif args.action == "switch-to-current":
        payload = action_switch_to_current()
    elif args.action == "freeze-current":
        payload = action_freeze_current()
    else:
        payload = action_return_to_pablito()

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if bool(payload.get("ok")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
