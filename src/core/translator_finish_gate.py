# -*- coding: utf-8 -*-
"""
translator_finish_gate.py — truthful automation-layer для блока `translator finish gate`.

Что это:
- компактный helper для сборки machine-readable snapshot по ближайшему translator milestone;
- единая логика для разбора `pytest`, `devicectl` и launch-attempt статусов;
- foundation для `.command`, ops-артефактов и будущего handoff без пересказа по памяти.

Зачем нужен:
- сейчас узкий хвост переводчика размазан между gateway-тестами, iOS build и ручным on-device ретестом;
- без общего snapshot легко перепутать "автоматическая часть зелёная" и "весь milestone уже закрыт";
- этот модуль даёт честное разделение: что уже подтверждено на `USER3`, а что всё ещё требует ручного прогона.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def pick_python_bin(project_root: Path) -> str:
    """Выбирает python-бинарь локального проекта без догадок по чужим окружениям."""
    # Единый venv (Py 3.13) в приоритете; legacy .venv — фолбек.
    candidates = (
        project_root / "venv" / "bin" / "python",
        project_root / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "python3"


def trim_command_output(text: str, *, max_lines: int = 40) -> str:
    """Обрезает шумные stdout/stderr, оставляя хвост для handoff и ops JSON."""
    lines = [line.rstrip() for line in str(text or "").splitlines()]
    if len(lines) <= max_lines:
        return "\n".join(lines).strip()
    return "\n".join(lines[-max_lines:]).strip()


def parse_pytest_summary(stdout: str) -> dict[str, Any]:
    """Извлекает краткий итог pytest без привязки к конкретному цветному формату."""
    text = ANSI_ESCAPE_RE.sub("", str(stdout or ""))
    passed_match = re.search(r"(?P<count>\d+)\s+passed", text)
    failed_match = re.search(r"(?P<count>\d+)\s+failed", text)
    dotted_passed = 0
    if not passed_match:
        for raw_line in reversed(text.splitlines()):
            line = raw_line.strip()
            if not line:
                continue
            if "." in line and re.fullmatch(r"[.\s\[\]%0-9]+", line):
                dotted_passed = line.count(".")
                break
    return {
        "passed_count": int(passed_match.group("count")) if passed_match else dotted_passed,
        "failed_count": int(failed_match.group("count")) if failed_match else 0,
        "summary_line": trim_command_output(text, max_lines=3),
    }


def parse_devicectl_apps_output(stdout: str, bundle_id: str) -> dict[str, Any]:
    """Ищет нужное приложение в табличном выводе `devicectl device info apps`."""
    target_bundle = str(bundle_id or "").strip()
    for raw_line in str(stdout or "").splitlines():
        line = raw_line.strip()
        if not line or target_bundle not in line:
            continue
        parts = [item.strip() for item in re.split(r"\s{2,}", line) if item.strip()]
        if len(parts) < 4:
            continue
        return {
            "installed": True,
            "app_name": parts[0],
            "bundle_id": parts[1],
            "version": parts[2],
            "bundle_version": parts[3],
            "raw_line": line,
        }
    return {
        "installed": False,
        "app_name": "",
        "bundle_id": target_bundle,
        "version": "",
        "bundle_version": "",
        "raw_line": "",
    }


def classify_launch_attempt(*, returncode: int, stdout: str, stderr: str) -> dict[str, Any]:
    """Классифицирует launch-attempt так, чтобы `locked` не выглядел как кодовый регресс."""
    combined = "\n".join(part for part in (stdout, stderr) if str(part or "").strip()).strip()
    lowered = combined.lower()
    if returncode == 0:
        return {
            "status": "launched",
            "blocked_by_device_lock": False,
            "summary": "Приложение запущено на устройстве.",
            "detail": trim_command_output(combined),
        }
    if "locked" in lowered or "could not be unlocked" in lowered:
        return {
            "status": "locked",
            "blocked_by_device_lock": True,
            "summary": "CLI-launch упёрся в блокировку устройства, а не в кодовый регресс.",
            "detail": trim_command_output(combined),
        }
    if (
        "not installed" in lowered
        or "application failed to launch" in lowered
        and "bundleidentifier" in lowered
    ):
        return {
            "status": "launch_failed",
            "blocked_by_device_lock": False,
            "summary": "Приложение не удалось запустить; нужен разбор launch-path.",
            "detail": trim_command_output(combined),
        }
    return {
        "status": "error",
        "blocked_by_device_lock": False,
        "summary": "Launch attempt завершился ошибкой и требует ручного разбора.",
        "detail": trim_command_output(combined),
    }


def build_translator_finish_gate_snapshot(
    *,
    project_root: Path,
    gateway_repo: Path,
    ios_project_path: Path,
    build_app_path: Path,
    device_name: str,
    device_udid: str,
    bundle_id: str,
    runtime_lite: dict[str, Any] | None = None,
    translator_readiness: dict[str, Any] | None = None,
    runtime_snapshot: dict[str, Any] | None = None,
    previous_device_status: dict[str, Any] | None = None,
    pytest_result: dict[str, Any] | None = None,
    build_result: dict[str, Any] | None = None,
    install_result: dict[str, Any] | None = None,
    apps_result: dict[str, Any] | None = None,
    launch_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Собирает единый truthful snapshot для translator finish gate."""
    runtime_payload = dict(runtime_lite or {}) if isinstance(runtime_lite, dict) else {}
    readiness_payload = (
        dict(translator_readiness or {}) if isinstance(translator_readiness, dict) else {}
    )
    runtime_state = dict(runtime_snapshot or {}) if isinstance(runtime_snapshot, dict) else {}
    previous_status = (
        dict(previous_device_status or {}) if isinstance(previous_device_status, dict) else {}
    )
    pytest_payload = dict(pytest_result or {}) if isinstance(pytest_result, dict) else {}
    build_payload = dict(build_result or {}) if isinstance(build_result, dict) else {}
    install_payload = dict(install_result or {}) if isinstance(install_result, dict) else {}
    apps_payload = dict(apps_result or {}) if isinstance(apps_result, dict) else {}
    launch_payload = dict(launch_result or {}) if isinstance(launch_result, dict) else {}

    route = (
        dict((runtime_payload.get("last_runtime_route") or {}))
        if isinstance(runtime_payload.get("last_runtime_route"), dict)
        else {}
    )
    account_runtime = (
        dict((readiness_payload.get("account_runtime") or {}))
        if isinstance(readiness_payload.get("account_runtime"), dict)
        else {}
    )
    runtime_truth = (
        dict(readiness_payload.get("runtime") or {})
        if isinstance(readiness_payload.get("runtime"), dict)
        else {}
    )
    telegram_runtime = (
        dict((runtime_truth.get("telegram_userbot_state") or {}))
        if isinstance(runtime_truth.get("telegram_userbot_state"), dict)
        else {}
    )
    translator_runtime = (
        dict((telegram_runtime.get("translator_profile") or {}))
        if isinstance(telegram_runtime.get("translator_profile"), dict)
        else {}
    )

    pytest_ok = bool(pytest_payload.get("ok"))
    build_ok = bool(build_payload.get("ok"))
    install_ok = bool(install_payload.get("ok")) if install_payload else False
    app_installed = bool(apps_payload.get("installed"))
    launch_status = str(launch_payload.get("status") or "skipped")
    automated_gate_ready = (
        pytest_ok
        and build_ok
        and bool(runtime_payload.get("ok"))
        and bool(readiness_payload.get("ok"))
        and str(account_runtime.get("current_route_model") or route.get("model") or "").strip()
        == "openai-codex/gpt-5.4"
        and app_installed
    )

    if launch_status == "locked":
        manual_status = "pending_device_unlock"
        next_step = "Разблокировать iPhone 14 Pro Max, открыть KrabVoice и выполнить короткий ru->es retest."
    elif launch_status == "launched":
        manual_status = "pending_audio_retest"
        next_step = "На уже запущенном приложении выполнить короткий ru->es retest и убедиться, что speech cancellation больше не всплывает."
    else:
        manual_status = "pending_launch_path"
        next_step = "Проверить launch-path приложения на устройстве и только потом повторять ручной ru->es retest."

    status = "automated_gate_ready" if automated_gate_ready else "blocked"
    if automated_gate_ready and manual_status != "pending_audio_retest":
        status = "automated_gate_ready_manual_step_pending"
    elif automated_gate_ready and manual_status == "pending_audio_retest":
        status = "manual_retest_ready"

    return {
        "ok": automated_gate_ready,
        "status": status,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_root": str(project_root),
        "gateway_repo": str(gateway_repo),
        "account": {
            "user": str(account_runtime.get("operator_id") or ""),
            "account_id": str(account_runtime.get("account_id") or ""),
            "mode": str(account_runtime.get("account_mode") or ""),
        },
        "runtime": {
            "health_ok": bool(runtime_payload.get("ok")),
            "translator_readiness_ok": bool(readiness_payload.get("ok")),
            "current_route_model": str(
                account_runtime.get("current_route_model") or route.get("model") or ""
            ).strip(),
            "current_route_channel": str(
                account_runtime.get("current_route_channel") or route.get("channel") or ""
            ).strip(),
            "voice_gateway_configured": bool(runtime_payload.get("voice_gateway_configured")),
            "scheduler_enabled": bool(runtime_payload.get("scheduler_enabled")),
            "translator_language_pair": str(translator_runtime.get("language_pair") or ""),
            "translator_translation_mode": str(translator_runtime.get("translation_mode") or ""),
            "translator_voice_strategy": str(translator_runtime.get("voice_strategy") or ""),
        },
        "gateway_regression": {
            "pytest_ok": pytest_ok,
            "summary": pytest_payload,
        },
        "ios_companion": {
            "device_name": device_name,
            "device_udid": device_udid,
            "bundle_id": bundle_id,
            "ios_project_path": str(ios_project_path),
            "build_app_path": str(build_app_path),
            "build": build_payload,
            "install": install_payload,
            "installed_app": apps_payload,
            "launch_attempt": launch_payload,
            "previous_on_device_truth": {
                "session_id": str(previous_status.get("session_id") or ""),
                "translation_mode": str(previous_status.get("translation_mode") or ""),
                "health_check_visible_status": str(
                    previous_status.get("health_check_visible_status") or ""
                ),
                "notes_ru": str(previous_status.get("notes_ru") or ""),
                "followup_gateway_commit": str(
                    previous_status.get("followup_gateway_commit") or ""
                ),
            },
        },
        "manual_retest": {
            "status": manual_status,
            "required": True,
            "next_step": next_step,
            "checklist": [
                "Убедиться, что на iPhone выставлены `translation_mode=ru_es_duplex`, `source_lang=ru`, `target_lang=es`.",
                "Запустить `Старт`, сказать 10-15 секунд русский текст с латинскими токенами вроде `health-check`, затем `Стоп`.",
                "Сделать повторный `Старт/Стоп` короткой фразой и проверить, что `Recognition request was canceled` больше не показывается.",
                "Если всё зелёно, обновить ops/handoff truth и только после этого считать translator finish gate закрытым.",
            ],
        },
        "artifacts": {
            "previous_device_status_path": str(
                project_root
                / "artifacts"
                / "ops"
                / "iphone_companion_on_device_status_user3_latest.json"
            ),
            "recommended_output_path": str(
                project_root / "artifacts" / "ops" / "translator_finish_gate_user3_latest.json"
            ),
        },
    }


__all__ = [
    "build_translator_finish_gate_snapshot",
    "classify_launch_attempt",
    "parse_devicectl_apps_output",
    "parse_pytest_summary",
    "pick_python_bin",
    "trim_command_output",
]
