# -*- coding: utf-8 -*-
"""
Pre-release smoke для Krab.

Зачем:
1. Дать один вход для предрелизной проверки перед интеграцией/пушем.
2. Разделить проверки на обязательные (gate) и диагностические (advisory).
3. Сохранять отчет в artifacts/ops, чтобы можно было быстро приложить в handover.

Связь с проектом:
- использует актуальные guard-команды проекта (r20 merge gate, unit/runtime smoke);
- дополняет их runtime-диагностикой каналов OpenClaw и маршрута алертов.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "ops"
DEFAULT_HEALTH_LITE_URL = "http://127.0.0.1:8080/api/health/lite"
DEFAULT_CHANNELS_URL = "http://127.0.0.1:8080/api/openclaw/channels/status"


@dataclass
class StepResult:
    name: str
    required: bool
    ok: bool
    exit_code: int
    cmd: list[str]
    summary: str
    blocked: bool = False
    blocked_reason: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(cmd: list[str], timeout: int = 300) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    return proc.returncode, (proc.stdout or ""), (proc.stderr or "")


def _mk_result(
    name: str,
    cmd: list[str],
    required: bool,
    code: int,
    out: str,
    err: str,
    *,
    blocked: bool = False,
    blocked_reason: str = "",
) -> StepResult:
    ok = code == 0
    text = (out.strip() or err.strip() or f"exit={code}")
    summary = "\n".join(text.splitlines()[-6:]).strip()
    return StepResult(
        name=name,
        required=required,
        ok=ok,
        exit_code=code,
        cmd=cmd,
        summary=summary,
        blocked=blocked,
        blocked_reason=blocked_reason,
    )


def _fetch_json(url: str, timeout_sec: float = 3.0) -> tuple[dict[str, object], str]:
    """Безопасно читает локальный JSON endpoint для smoke-классификации."""
    req = request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", errors="replace")
        payload = json.loads(body)
    except (error.URLError, error.HTTPError, TimeoutError, ValueError) as exc:
        return {}, str(exc)
    if not isinstance(payload, dict):
        return {}, "invalid_json_payload"
    return payload, ""


def _listener_owner(port: int) -> str:
    """Возвращает owner listener-процесса на TCP-порту, если он есть."""
    if not shutil.which("lsof"):
        return ""
    code, out, _ = _run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], timeout=15)
    if code != 0 or not out.strip():
        return ""
    lines = [line for line in out.splitlines() if line.strip()]
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 3:
            return str(parts[2]).strip()
    return ""


def _detect_runtime_owner_mismatch() -> dict[str, object]:
    """
    Определяет, что текущая macOS-учётка не владеет живым runtime.

    Это не регрессия кода, а environment-blocker:
    - `:8080`/`:18789` живы;
    - текущий `$HOME/.openclaw` пустой/другой;
    - listener-процесс принадлежит другому macOS-user.
    """
    current_user = getpass.getuser()
    local_openclaw_path = Path.home() / ".openclaw" / "openclaw.json"
    local_openclaw_exists = local_openclaw_path.exists()
    health_payload, health_error = _fetch_json(DEFAULT_HEALTH_LITE_URL, timeout_sec=3.0)
    health_ok = bool(health_payload.get("ok")) if isinstance(health_payload, dict) else False
    web_owner = _listener_owner(8080)
    gateway_owner = _listener_owner(18789)
    live_owner = web_owner or gateway_owner

    owner_mismatch = False
    reasons: list[str] = []
    if live_owner and live_owner != current_user:
        owner_mismatch = True
        reasons.append(f"listener_owner={live_owner}")
    if health_ok and not local_openclaw_exists:
        owner_mismatch = True
        reasons.append(f"local_openclaw_missing={local_openclaw_path}")
    if health_error:
        reasons.append(f"health_error={health_error}")

    summary_parts = [
        f"current_user={current_user}",
        f"web_owner={web_owner or '-'}",
        f"gateway_owner={gateway_owner or '-'}",
        f"health_ok={str(health_ok).lower()}",
        f"local_openclaw_exists={str(local_openclaw_exists).lower()}",
    ]
    if reasons:
        summary_parts.append("reasons=" + ", ".join(reasons))

    return {
        "owner_mismatch": owner_mismatch,
        "current_user": current_user,
        "web_owner": web_owner,
        "gateway_owner": gateway_owner,
        "health_ok": health_ok,
        "health_error": health_error,
        "local_openclaw_path": str(local_openclaw_path),
        "local_openclaw_exists": local_openclaw_exists,
        "summary": " • ".join(summary_parts),
    }


def _blocked_result(
    name: str,
    cmd: list[str],
    required: bool,
    *,
    reason: str,
    summary: str,
) -> StepResult:
    """Формирует blocked-step без исполнения команды, если среда заведомо неподходящая."""
    return StepResult(
        name=name,
        required=required,
        ok=False,
        exit_code=2,
        cmd=cmd,
        summary=summary,
        blocked=True,
        blocked_reason=reason,
    )


def _truthful_channels_probe_result(*, required: bool) -> StepResult:
    """
    Проверяет truth каналов через web endpoint с fallback на CLI.

    Почему:
    - на временной учётке локальный `openclaw channels status --probe` может валиться
      по gateway token mismatch, хотя живой `:8080` уже знает правду о каналах;
    - для merge-gate важнее truthful runtime verdict, чем локальная auth-среда CLI.
    """
    payload, err = _fetch_json(DEFAULT_CHANNELS_URL, timeout_sec=6.0)
    if err == "" and isinstance(payload.get("channels"), list):
        failed: list[str] = []
        passed = 0
        skipped = 0
        for item in payload.get("channels") or []:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").strip().upper()
            name = str(item.get("name") or "unknown").strip()
            meta = str(item.get("meta") or "").strip()
            if status == "OK":
                passed += 1
                continue
            if "not configured" in meta.lower():
                skipped += 1
                continue
            failed.append(f"{name}: {status} {meta}".strip())
        ok = len(failed) == 0
        summary = (
            f"web truth: passed={passed} skipped={skipped} failed={len(failed)}"
            if ok
            else "\n".join(["web truth failed:"] + failed[:5])
        )
        return StepResult(
            name="channels_probe",
            required=required,
            ok=ok,
            exit_code=0 if ok else 1,
            cmd=["GET", DEFAULT_CHANNELS_URL],
            summary=summary,
        )

    if shutil.which("openclaw"):
        code, out, err_text = _run(["openclaw", "channels", "status", "--probe"], timeout=60)
        return _mk_result(
            "channels_probe",
            ["openclaw", "channels", "status", "--probe"],
            required,
            code,
            out,
            err_text,
        )

    return StepResult(
        name="channels_probe",
        required=required,
        ok=False,
        exit_code=127,
        cmd=["openclaw", "channels", "status", "--probe"],
        summary=f"channels status unavailable: web_error={err or 'unknown'}; openclaw CLI not found",
    )


def _python_bin() -> str:
    """
    Выбирает python для smoke так, чтобы в нём были базовые модули:
    - pytest (unit-гейты),
    - dotenv (autoswitch dry-run).

    Почему:
    после рефакторинга часть окружений содержит `.venv/bin/python` без pip/pytest,
    из-за чего pre-release smoke ложно падает до реальной проверки.
    """
    candidates: list[str] = []
    venv = ROOT / ".venv" / "bin" / "python"
    if venv.exists():
        candidates.append(str(venv))
    if sys.executable:
        candidates.append(sys.executable)
    for fallback in ("python3", "python"):
        resolved = shutil.which(fallback)
        if resolved:
            candidates.append(resolved)

    required_modules = ("pytest", "dotenv")
    seen: set[str] = set()
    for py in candidates:
        if py in seen:
            continue
        seen.add(py)
        try:
            code, _, _ = _run(
                [
                    py,
                    "-c",
                    "import importlib.util as u; "
                    f"mods={required_modules!r}; "
                    "ok=all(u.find_spec(m) is not None for m in mods); "
                    "raise SystemExit(0 if ok else 1)",
                ],
                timeout=20,
            )
        except Exception:
            continue
        if code == 0:
            return py

    # Без полного набора модулей возвращаем первый доступный python,
    # чтобы smoke хотя бы дал диагностируемый отчёт.
    return candidates[0] if candidates else (sys.executable or "python3")


def _script_exists(rel_path: str) -> bool:
    """Проверяет наличие скрипта внутри репозитория."""
    return (ROOT / rel_path).exists()


def main() -> int:
    parser = argparse.ArgumentParser(description="Krab pre-release smoke runner")
    parser.add_argument("--full", action="store_true", help="добавить полный smoke_test")
    parser.add_argument(
        "--strict-runtime",
        action="store_true",
        help="падать, если runtime-диагностика каналов неуспешна",
    )
    args = parser.parse_args()

    py = _python_bin()
    steps: list[StepResult] = []
    runtime_owner_probe = _detect_runtime_owner_mismatch()
    runtime_owner_mismatch = bool(runtime_owner_probe.get("owner_mismatch"))

    if runtime_owner_mismatch:
        steps.append(
            _blocked_result(
                "runtime_owner_context",
                [],
                False,
                reason="runtime_owner_mismatch",
                summary=str(runtime_owner_probe.get("summary") or "runtime owner mismatch"),
            )
        )

    # Обязательные гейты (адаптивно: только существующие инструменты).
    required_checks: list[tuple[str, list[str]]] = [
        (
            "syntax_core",
            [
                py,
                "-m",
                "py_compile",
                "src/openclaw_client.py",
                "src/model_manager.py",
                "src/userbot_bridge.py",
                "src/modules/web_app.py",
            ],
        ),
        (
            "unit_runtime_core",
            [
                py,
                "-m",
                "pytest",
                "-q",
                "tests/unit/test_openclaw_model_autoswitch.py",
                "tests/unit/test_web_app_runtime_endpoints.py",
            ],
        ),
    ]
    if _script_exists("scripts/r20_merge_gate.py") and args.full:
        required_checks.append(("r20_merge_gate_full", [py, "scripts/r20_merge_gate.py", "--full"]))

    for name, cmd in required_checks:
        code, out, err = _run(cmd, timeout=900)
        steps.append(_mk_result(name, cmd, True, code, out, err))

    # Диагностические проверки runtime (по умолчанию advisory).
    diagnostics: list[dict[str, object]] = []

    diagnostics.append(
        {
            "name": "autoswitch_dry_run",
            "cmd": [py, "scripts/openclaw_model_autoswitch.py", "--dry-run", "--profile", "current"],
            "required_on_strict": True,
            "owner_sensitive": True,
        }
    )
    if _script_exists("scripts/live_channel_smoke.py"):
        diagnostics.append(
            {
                "name": "live_channel_smoke",
                "cmd": [py, "scripts/live_channel_smoke.py", "--max-age-minutes", "60"],
                "required_on_strict": True,
                "owner_sensitive": False,
            }
        )
    if _script_exists("scripts/swarm_live_smoke.py"):
        diagnostics.append(
            {
                "name": "swarm_live_smoke_mock",
                "cmd": [py, "scripts/swarm_live_smoke.py", "--mode", "mock", "--rounds", "1"],
                "required_on_strict": False,
                "owner_sensitive": False,
            }
        )
    if _script_exists("scripts/e1e3_acceptance.py"):
        diagnostics.append(
            {
                "name": "e1e3_acceptance",
                "cmd": [py, "scripts/e1e3_acceptance.py", "--max-age-minutes", "60"],
                "required_on_strict": True,
                "owner_sensitive": True,
            }
        )
    if _script_exists("scripts/r20_merge_gate.py"):
        diagnostics.append(
            {
                "name": "r20_merge_gate",
                "cmd": [py, "scripts/r20_merge_gate.py"],
                "required_on_strict": False,
                "owner_sensitive": False,
            }
        )

    if shutil.which("openclaw"):
        diagnostics.append(
            {
                "name": "channels_probe",
                "cmd": ["openclaw", "channels", "status", "--probe"],
                "required_on_strict": True,
                "owner_sensitive": False,
                "custom_runner": "truthful_channels_probe",
            }
        )
    elif not runtime_owner_mismatch:
        steps.append(
            StepResult(
                name="channels_probe",
                required=bool(args.strict_runtime),
                ok=False,
                exit_code=127,
                cmd=["openclaw", "channels", "status", "--probe"],
                summary="openclaw CLI не найден в PATH",
            )
        )

    if (ROOT / "scripts" / "check_signal_alert_route.command").exists():
        diagnostics.append(
            {
                "name": "signal_alert_route",
                "cmd": ["./scripts/check_signal_alert_route.command"],
                "required_on_strict": False,
                "owner_sensitive": False,
            }
        )

    for item in diagnostics:
        name = str(item.get("name") or "").strip()
        cmd = list(item.get("cmd") or [])
        required = bool(args.strict_runtime and bool(item.get("required_on_strict")))
        owner_sensitive = bool(item.get("owner_sensitive"))
        custom_runner = str(item.get("custom_runner") or "").strip()
        if runtime_owner_mismatch and owner_sensitive:
            steps.append(
                _blocked_result(
                    name,
                    cmd,
                    required,
                    reason="runtime_owner_mismatch",
                    summary=f"Проверка заблокирована: {runtime_owner_probe.get('summary')}",
                )
            )
            continue
        if custom_runner == "truthful_channels_probe":
            steps.append(_truthful_channels_probe_result(required=required))
            continue
        code, out, err = _run(cmd, timeout=240)
        steps.append(_mk_result(name, cmd, required, code, out, err))

    blocked_required = [s for s in steps if s.required and s.blocked]
    blocked_advisory = [s for s in steps if (not s.required) and s.blocked]
    required_failed = [s for s in steps if s.required and not s.ok and not s.blocked]
    advisory_failed = [s for s in steps if (not s.required) and (not s.ok) and not s.blocked]

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = ARTIFACTS / f"pre_release_smoke_{stamp}.json"
    latest_path = ARTIFACTS / "pre_release_smoke_latest.json"

    payload = {
        "ok": len(required_failed) == 0 and len(blocked_required) == 0,
        "blocked": len(blocked_required) > 0,
        "generated_at": _now_iso(),
        "strict_runtime": bool(args.strict_runtime),
        "full": bool(args.full),
        "runtime_owner_probe": runtime_owner_probe,
        "required_failed": [s.name for s in required_failed],
        "blocked_required": [s.name for s in blocked_required],
        "advisory_failed": [s.name for s in advisory_failed],
        "blocked_advisory": [s.name for s in blocked_advisory],
        "steps": [
            {
                "name": s.name,
                "required": s.required,
                "ok": s.ok,
                "exit_code": s.exit_code,
                "cmd": s.cmd,
                "summary": s.summary,
                "blocked": s.blocked,
                "blocked_reason": s.blocked_reason,
            }
            for s in steps
        ],
    }
    report_text = json.dumps(payload, ensure_ascii=False, indent=2)
    report_path.write_text(report_text, encoding="utf-8")
    latest_path.write_text(report_text, encoding="utf-8")

    print("🧪 Pre-release Smoke")
    print(f"- required checks: {len([s for s in steps if s.required])}")
    print(f"- advisory checks: {len([s for s in steps if not s.required])}")
    print(f"- required failed: {len(required_failed)}")
    print(f"- required blocked: {len(blocked_required)}")
    print(f"- advisory failed: {len(advisory_failed)}")
    print(f"- advisory blocked: {len(blocked_advisory)}")
    print(f"- report: {report_path}")

    if required_failed:
        print("\n❌ Обязательные проверки не пройдены:")
        for item in required_failed:
            print(f"  - {item.name}: {item.summary}")
        return 1

    if blocked_required:
        print("\n⏸ Обязательные проверки заблокированы средой:")
        for item in blocked_required:
            print(f"  - {item.name}: {item.summary}")
        return 2

    if advisory_failed:
        print("\n⚠️ Диагностические предупреждения:")
        for item in advisory_failed:
            print(f"  - {item.name}: {item.summary}")

    if blocked_advisory:
        print("\nℹ️ Диагностические проверки пропущены из-за среды:")
        for item in blocked_advisory:
            print(f"  - {item.name}: {item.summary}")

    print("\n✅ Pre-release smoke завершен.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
