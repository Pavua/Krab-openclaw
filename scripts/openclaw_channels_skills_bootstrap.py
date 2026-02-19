#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bootstrap-аудит каналов и скиллов OpenClaw для экосистемы Krab.

Зачем:
1. Зафиксировать, какие каналы/скиллы уже готовы, а какие блокируются зависимостями;
2. Дать воспроизводимый one-click отчёт для настройки OpenClaw на macOS (M-серия);
3. Безопасно применять только базовые anti-regression настройки без риска для userbot-контура.

Связь с проектом:
- Работает в связке с docs/OPENCLAW_DASHBOARD_PLAYBOOK_RU.md;
- Поддерживает policy "не дублировать функционал OpenClaw внутри Krab";
- Используется из openclaw_channels_skills_bootstrap.command.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "artifacts" / "openclaw_bootstrap"

PRIORITY_CHANNELS = ["telegram", "imessage", "discord", "slack", "signal"]

PRIORITY_SKILLS = [
    "coding-agent",
    "summarize",
    "openai-whisper",
    "openai-whisper-api",
    "github",
    "gh-issues",
    "weather",
    "imsg",
    "discord",
    "slack",
    "voice-call",
    "bluebubbles",
]

# Must-have для текущей стратегии интеграции Krab + OpenClaw.
PROJECT_MUST_HAVE_SKILLS = [
    "coding-agent",
    "summarize",
    "openai-whisper-api",
    "github",
    "gh-issues",
]

CHANNEL_ENV_HINTS: dict[str, list[str]] = {
    "telegram": ["OPENCLAW_TELEGRAM_BOT_TOKEN"],
    "discord": ["OPENCLAW_DISCORD_BOT_TOKEN"],
    "slack": ["OPENCLAW_SLACK_BOT_TOKEN", "OPENCLAW_SLACK_APP_TOKEN"],
    "signal": ["OPENCLAW_SIGNAL_NUMBER", "OPENCLAW_SIGNAL_HTTP_URL"],
    "imessage": ["OPENCLAW_IMSG_CLI_PATH"],
}


@dataclass
class CmdResult:
    """Результат shell-команды."""

    cmd: str
    code: int
    out: str
    err: str


@dataclass
class JsonResult:
    """Результат JSON-команды."""

    ok: bool
    payload: dict[str, Any]
    error: str


def _run(cmd: list[str], timeout: int = 25) -> CmdResult:
    """Выполняет команду и возвращает stdout/stderr без исключений наверх."""
    try:
        completed = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CmdResult(
            cmd=" ".join(cmd),
            code=completed.returncode,
            out=(completed.stdout or "").strip(),
            err=(completed.stderr or "").strip(),
        )
    except Exception as exc:  # noqa: BLE001
        return CmdResult(cmd=" ".join(cmd), code=127, out="", err=str(exc))


def _run_json(cmd: list[str], timeout: int = 25) -> JsonResult:
    """Выполняет JSON-команду и безопасно парсит ответ."""
    result = _run(cmd, timeout=timeout)
    if result.code != 0:
        return JsonResult(ok=False, payload={}, error=result.err or result.out or "unknown error")
    try:
        payload = json.loads(result.out or "{}")
        if isinstance(payload, dict):
            return JsonResult(ok=True, payload=payload, error="")
        return JsonResult(ok=False, payload={}, error="JSON root is not an object")
    except Exception as exc:  # noqa: BLE001
        return JsonResult(ok=False, payload={}, error=f"json parse failed: {exc}")


def _openclaw_cmd(openclaw_bin: str, profile: str, *tail: str) -> list[str]:
    """Собирает команду OpenClaw с учётом профиля."""
    cmd = [openclaw_bin]
    if profile != "main":
        cmd.extend(["--profile", profile])
    cmd.extend(list(tail))
    return cmd


def _bytes_to_gib(raw_bytes: int) -> str:
    """Переводит байты в GiB с одним знаком после запятой."""
    gib = raw_bytes / (1024**3)
    return f"{gib:.1f} GiB"


def _detect_ram() -> str:
    """Определяет объём RAM на macOS через sysctl (fallback на unknown)."""
    result = _run(["sysctl", "-n", "hw.memsize"])
    if result.code != 0 or not result.out.strip().isdigit():
        return "unknown"
    return _bytes_to_gib(int(result.out.strip()))


def _channel_state(
    channel_name: str,
    channels_list_payload: dict[str, Any],
    channels_config_payload: dict[str, Any],
) -> tuple[str, str]:
    """Возвращает состояние канала: enabled|disabled|configured|unconfigured."""
    chat = channels_list_payload.get("chat", {})
    if isinstance(chat, dict) and channel_name in chat:
        return "enabled", "✅"

    config_value = channels_config_payload.get(channel_name)
    if isinstance(config_value, dict):
        if "enabled" in config_value:
            if bool(config_value.get("enabled")):
                return "enabled", "✅"
            return "disabled", "🟡"
        return "configured", "⚪"

    if config_value is not None:
        return "configured", "⚪"
    return "unconfigured", "⚪"


def _format_missing_reqs(item: dict[str, Any]) -> str:
    """Форматирует missingRequirements из openclaw skills check."""
    missing = item.get("missing", {})
    bins = ", ".join(missing.get("bins", []) or [])
    env = ", ".join(missing.get("env", []) or [])
    config = ", ".join(missing.get("config", []) or [])
    os_req = ", ".join(missing.get("os", []) or [])
    parts: list[str] = []
    if bins:
        parts.append(f"bins: {bins}")
    if env:
        parts.append(f"env: {env}")
    if config:
        parts.append(f"config: {config}")
    if os_req:
        parts.append(f"os: {os_req}")
    return "; ".join(parts) if parts else "требования не указаны"


def _apply_safe_baseline(openclaw_bin: str, profile: str) -> list[str]:
    """Применяет только безопасные policy-настройки без включения новых каналов."""
    results: list[str] = []
    commands = [
        ("channels.telegram.groupPolicy", '"allowlist"'),
        ("channels.telegram.streamMode", '"partial"'),
        ("channels.telegram.dmPolicy", '"pairing"'),
        ("channels.telegram.enabled", "false"),
    ]
    for path, value in commands:
        cmd = _openclaw_cmd(openclaw_bin, profile, "config", "set", path, value, "--json")
        result = _run(cmd)
        if result.code == 0:
            results.append(f"✅ {path}={value}")
        else:
            line = result.err or result.out or "unknown error"
            results.append(f"⚠️ {path}: {line}")
    return results


def _build_channel_add_cmd(channel: str, openclaw_bin: str, profile: str) -> tuple[list[str], str]:
    """Собирает команду openclaw channels add на основе env-переменных."""
    base = _openclaw_cmd(openclaw_bin, profile, "channels", "add", "--channel", channel)
    if channel == "discord":
        token = os.getenv("OPENCLAW_DISCORD_BOT_TOKEN", "").strip()
        if not token:
            return [], "нет OPENCLAW_DISCORD_BOT_TOKEN"
        return base + ["--token", token], ""

    if channel == "telegram":
        token = os.getenv("OPENCLAW_TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            return [], "нет OPENCLAW_TELEGRAM_BOT_TOKEN"
        return base + ["--token", token], ""

    if channel == "slack":
        bot_token = os.getenv("OPENCLAW_SLACK_BOT_TOKEN", "").strip()
        app_token = os.getenv("OPENCLAW_SLACK_APP_TOKEN", "").strip()
        if not bot_token:
            return [], "нет OPENCLAW_SLACK_BOT_TOKEN"
        cmd = base + ["--bot-token", bot_token]
        if app_token:
            cmd.extend(["--app-token", app_token])
        return cmd, ""

    if channel == "imessage":
        cli_path = os.getenv("OPENCLAW_IMSG_CLI_PATH", "").strip() or (shutil.which("imsg") or "")
        if not cli_path:
            return [], "не найден imsg CLI (brew install imsg)"
        return base + ["--cli-path", cli_path], ""

    if channel == "signal":
        signal_number = os.getenv("OPENCLAW_SIGNAL_NUMBER", "").strip()
        http_url = os.getenv("OPENCLAW_SIGNAL_HTTP_URL", "").strip()
        if not signal_number and not http_url:
            return [], "нет OPENCLAW_SIGNAL_NUMBER/OPENCLAW_SIGNAL_HTTP_URL"
        cmd = base
        if signal_number:
            cmd.extend(["--signal-number", signal_number])
        if http_url:
            cmd.extend(["--http-url", http_url])
        return cmd, ""

    return [], f"канал {channel} не поддерживается bootstrap-скриптом"


def _enable_requested_channels(openclaw_bin: str, profile: str, channels: list[str]) -> list[str]:
    """Подключает запрошенные каналы, если доступны обязательные credentials/env."""
    if not channels:
        return []
    lines: list[str] = []
    for channel in channels:
        cmd, reason = _build_channel_add_cmd(channel, openclaw_bin, profile)
        if not cmd:
            lines.append(f"⚠️ {channel}: пропуск ({reason})")
            continue
        result = _run(cmd, timeout=35)
        if result.code == 0:
            lines.append(f"✅ {channel}: канал добавлен/обновлён")
        else:
            line = result.err or result.out or "unknown error"
            lines.append(f"❌ {channel}: {line}")
    return lines


def main() -> int:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(description="OpenClaw channels/skills bootstrap for Krab")
    parser.add_argument("--profile", default="main", help="Профиль OpenClaw (main|lab|...)")
    parser.add_argument(
        "--apply-safe",
        action="store_true",
        help="Применить только безопасный baseline (без включения новых каналов)",
    )
    parser.add_argument(
        "--enable",
        default="",
        help="Каналы для автоподключения (через запятую), напр. discord,slack,imessage",
    )
    args = parser.parse_args()

    openclaw_bin = shutil.which("openclaw")
    if not openclaw_bin:
        print("❌ OpenClaw CLI не найден в PATH.")
        return 2

    profile = (args.profile or "main").strip()
    enabled_channels = [x.strip().lower() for x in (args.enable or "").split(",") if x.strip()]

    version = _run(_openclaw_cmd(openclaw_bin, profile, "--version"))
    skills = _run_json(_openclaw_cmd(openclaw_bin, profile, "skills", "check", "--json"), timeout=45)
    channels_list = _run_json(
        _openclaw_cmd(openclaw_bin, profile, "channels", "list", "--json", "--no-usage"),
        timeout=35,
    )
    channels_status = _run_json(
        _openclaw_cmd(openclaw_bin, profile, "channels", "status", "--json"),
        timeout=35,
    )
    channels_config = _run_json(
        _openclaw_cmd(openclaw_bin, profile, "config", "get", "channels", "--json"),
        timeout=20,
    )
    models = _run_json(_openclaw_cmd(openclaw_bin, profile, "models", "list", "--json"), timeout=20)

    baseline_lines: list[str] = []
    if args.apply_safe:
        baseline_lines = _apply_safe_baseline(openclaw_bin, profile)

    enabled_lines = _enable_requested_channels(openclaw_bin, profile, enabled_channels)

    skills_payload = skills.payload if skills.ok else {}
    skills_summary = skills_payload.get("summary", {})
    eligible = set(skills_payload.get("eligible", []) or [])
    missing_items = skills_payload.get("missingRequirements", []) or []
    missing_map = {
        item.get("name"): item for item in missing_items if isinstance(item, dict) and item.get("name")
    }

    channel_list_payload = channels_list.payload if channels_list.ok else {}
    channel_status_payload = channels_status.payload if channels_status.ok else {}
    channel_config_payload = channels_config.payload if channels_config.ok else {}

    machine = platform.machine()
    os_name = platform.platform()
    ram = _detect_ram()
    now = dt.datetime.now().astimezone()

    model_lines: list[str] = []
    if models.ok:
        for model in models.payload.get("models", []) or []:
            if not isinstance(model, dict):
                continue
            key = str(model.get("key", "unknown"))
            tags = model.get("tags", []) or []
            tag = "default" if "default" in tags else "extra"
            local_flag = "local" if model.get("local") else "cloud"
            model_lines.append(f"- `{key}` ({tag}, {local_flag})")

    channel_lines: list[str] = []
    for channel in PRIORITY_CHANNELS:
        state, status_icon = _channel_state(channel, channel_list_payload, channel_config_payload)
        hints = ", ".join(CHANNEL_ENV_HINTS.get(channel, [])) or "n/a"
        channel_lines.append(f"- {status_icon} `{channel}` | state: `{state}` | env hints: `{hints}`")

    skill_lines: list[str] = []
    for skill_name in PRIORITY_SKILLS:
        if skill_name in eligible:
            skill_lines.append(f"- ✅ `{skill_name}` ready")
            continue
        if skill_name in missing_map:
            missing_text = _format_missing_reqs(missing_map[skill_name])
            skill_lines.append(f"- ⚠️ `{skill_name}` missing ({missing_text})")
            continue
        skill_lines.append(f"- ⚪ `{skill_name}` not found in check output")

    must_have_missing = [name for name in PROJECT_MUST_HAVE_SKILLS if name not in eligible]
    priority_brew_skill_names = set(PRIORITY_SKILLS) | {"wacli"}
    brew_hints = sorted(
        {
            pkg
            for item in missing_items
            if isinstance(item, dict) and str(item.get("name", "")) in priority_brew_skill_names
            for install in (item.get("install", []) or [])
            if isinstance(install, dict) and install.get("kind") == "brew"
            for pkg in (install.get("bins", []) or [])
        }
    )

    channel_order = channel_status_payload.get("channelOrder", []) if channels_status.ok else []
    report_header = [
        "# OpenClaw Channels & Skills Bootstrap Report",
        "",
        f"- Время: `{now.isoformat()}`",
        f"- Профиль OpenClaw: `{profile}`",
        f"- OpenClaw: `{version.out or version.err or 'unknown'}`",
        f"- macOS: `{os_name}`",
        f"- Архитектура: `{machine}`",
        f"- RAM: `{ram}`",
        "",
        "## Сводка skills check",
        f"- total: `{skills_summary.get('total', 'n/a')}`",
        f"- eligible: `{skills_summary.get('eligible', 'n/a')}`",
        f"- missingRequirements: `{skills_summary.get('missingRequirements', 'n/a')}`",
        "",
        "## Must-have навыки для Krab/OpenClaw",
    ]

    report_lines: list[str] = []
    report_lines.extend(report_header)
    report_lines.extend(skill_lines)
    report_lines.extend(["", "## Каналы (приоритет и readiness)", *channel_lines, ""])

    report_lines.append("## Текущие каналы gateway")
    if channel_order:
        for ch in channel_order:
            report_lines.append(f"- `{ch}`")
    else:
        report_lines.append("- `(нет активных каналов)`")
    report_lines.append("")

    report_lines.append("## Доступные модели OpenClaw")
    if model_lines:
        report_lines.extend(model_lines)
    else:
        report_lines.append("- `(не удалось получить список моделей)`")
    report_lines.append("")

    if args.apply_safe:
        report_lines.append("## Применение safe-baseline (--apply-safe)")
        report_lines.extend(baseline_lines or ["- `(нет изменений)`"])
        report_lines.append("")

    if enabled_channels:
        report_lines.append("## Автоподключение каналов (--enable)")
        report_lines.extend(enabled_lines or ["- `(нет действий)`"])
        report_lines.append("")

    report_lines.append("## Что критично закрыть дальше")
    if must_have_missing:
        for name in must_have_missing:
            item = missing_map.get(name, {})
            report_lines.append(f"- `{name}`: {_format_missing_reqs(item) if item else 'статус неясен'}")
    else:
        report_lines.append("- ✅ Все must-have навыки готовы.")
    report_lines.append("")

    report_lines.append("## Brew-пакеты, которые помогут закрыть часть missing requirements")
    if brew_hints:
        report_lines.append(f"- `brew install {' '.join(brew_hints)}`")
    else:
        report_lines.append("- `(по данным skills check brew-зависимостей не найдено)`")
    report_lines.append("")

    report_lines.append("## Рекомендуемый порядок включения каналов")
    report_lines.append("- `imessage` -> `discord` -> `slack` -> `signal`")
    report_lines.append("- Telegram bot в OpenClaw включать только при явной задаче (у тебя основной Telegram-контур = Pyrogram userbot Krab).")
    report_lines.append("")

    report_lines.append("## Следующие команды")
    report_lines.append(
        "- Аудит: `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_channels_skills_bootstrap.command`"
    )
    report_lines.append(
        "- Безопасный baseline: `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_channels_skills_bootstrap.command apply`"
    )
    report_lines.append(
        "- Подключить каналы из env: "
        "`/Users/pablito/Antigravity_AGENTS/Краб/openclaw_channels_skills_bootstrap.command apply discord,slack`"
    )
    report_lines.append("")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"channels_skills_bootstrap_{stamp}.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    print("✅ OpenClaw bootstrap-аудит завершён.")
    print(f"📄 Отчёт: {report_path}")
    if must_have_missing:
        print(f"⚠️ Must-have missing: {', '.join(must_have_missing)}")
    else:
        print("✅ Must-have навыки готовы.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
