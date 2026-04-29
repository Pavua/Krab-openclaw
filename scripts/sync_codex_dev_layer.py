#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Синхронизирует безопасный dev-layer Codex для Krab между macOS-учётками.

Зачем нужен:
- у `pablito`, `USER2`, `USER3` разные ChatGPT Plus/OAuth состояния, поэтому нельзя
  копировать `auth.json`, браузерные профили, Telegram session и `~/.openclaw`;
- при этом skills, plugin cache, AGENTS.md и MCP-конфиг можно держать одинаковыми,
  чтобы параллельная разработка шла без конфликтов и без ручной настройки с нуля;
- скрипт запускается из нужной учётки и пишет только в её собственный `~/.codex`.

Связь с проектом:
- используется `.command`-лаунчерами в корне Krab;
- поддерживает code-only/dev-admin режимы для helper-учёток и `full` профиль для
  основной инженерной учётки, где допустимы Telegram/OpenClaw MCP.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import stat
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_CODEX = Path("/Users/pablito/.codex")
DEFAULT_SOURCE_CLAUDE_MARKETPLACE = Path(
    "/Users/pablito/.claude/plugins/marketplaces/claude-plugins-official"
)
PRIMARY_REPO_PATH = Path("/Users/pablito/Antigravity_AGENTS/Краб")
SHARED_REPO_PATH = Path("/Users/Shared/Antigravity_AGENTS/Краб")

SAFE_COPY_DIRS = (
    "skills",
    "plugins/cache",
    "vendor_imports",
)

SAFE_COPY_FILES = (
    "AGENTS.md",
    "check_codex_tooling.command",
)

ENABLED_SKILL_NAMES = (
    "develop-web-game",
    "chatgpt-apps",
    "cloudflare-deploy",
    "doc",
    "jupyter-notebook",
    "figma",
    "imagegen",
    "linear",
    "netlify-deploy",
    "notion-meeting-intelligence",
    "notion-knowledge-capture",
    "notion-research-documentation",
    "notion-spec-to-implementation",
    "pdf",
    "render-deploy",
    "sentry",
    "sora",
    "speech",
    "vercel-deploy",
    "yeet",
)

ENABLED_PLUGINS = (
    "google-calendar@openai-curated",
    "gmail@openai-curated",
    "canva@openai-curated",
    "netlify@openai-curated",
    "stripe@openai-curated",
    "vercel@openai-curated",
    "github@openai-curated",
    "google-drive@openai-curated",
    "browser-use@openai-bundled",
    "documents@openai-primary-runtime",
    "spreadsheets@openai-primary-runtime",
    "presentations@openai-primary-runtime",
    "sentry@openai-curated",
)


def _q(value: str | Path) -> str:
    """Возвращает TOML-safe строку."""
    return json.dumps(str(value), ensure_ascii=False)


def _timestamp() -> str:
    """UTC timestamp для бэкапов и отчётов."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")


def _copy_tree(src: Path, dst: Path, dry_run: bool) -> dict[str, object]:
    """Копирует директорию целиком, не затрагивая секретные файлы Codex."""
    if not src.exists():
        return {"src": str(src), "dst": str(dst), "ok": False, "reason": "source_missing"}
    if src.resolve() == dst.resolve():
        return {"src": str(src), "dst": str(dst), "ok": True, "skipped": "same_path"}
    if dry_run:
        return {"src": str(src), "dst": str(dst), "ok": True, "dry_run": True}
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        src,
        dst,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            ".git",
            "__pycache__",
            "*.pyc",
            "*.sqlite",
            "*.sqlite-shm",
            "*.sqlite-wal",
            "*.log",
        ),
    )
    return {"src": str(src), "dst": str(dst), "ok": True}


def _copy_file(src: Path, dst: Path, dry_run: bool) -> dict[str, object]:
    """Копирует одиночный безопасный файл, сохраняя executable bit."""
    if not src.exists():
        return {"src": str(src), "dst": str(dst), "ok": False, "reason": "source_missing"}
    if src.resolve() == dst.resolve():
        return {"src": str(src), "dst": str(dst), "ok": True, "skipped": "same_path"}
    if dry_run:
        return {"src": str(src), "dst": str(dst), "ok": True, "dry_run": True}
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if src.stat().st_mode & stat.S_IXUSR:
        dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return {"src": str(src), "dst": str(dst), "ok": True}


def _backup_file(path: Path, dry_run: bool) -> str | None:
    """Создаёт timestamp-бэкап существующего файла перед перезаписью."""
    if not path.exists():
        return None
    backup_path = path.with_name(f"{path.name}.bak.{_timestamp()}")
    if not dry_run:
        shutil.copy2(path, backup_path)
    return str(backup_path)


def _render_mcp(profile: str) -> list[str]:
    """Рендерит MCP-серверы под текущую учётку без шаринга чужих session state."""
    lines: list[str] = ["[mcp_servers]", ""]

    lines += [
        "[mcp_servers.chrome-devtools]",
        'command = "npx"',
        'args = ["-y", "chrome-devtools-mcp@latest", "--autoConnect"]',
        "startup_timeout_sec = 20.0",
        "",
        "[mcp_servers.context7]",
        'url = "https://mcp.context7.com/mcp"',
        "",
        "[mcp_servers.notion]",
        'url = "https://mcp.notion.com/mcp"',
        "",
        "[mcp_servers.playwright]",
        'command = "npx"',
        'args = ["@playwright/mcp@latest"]',
        "",
        "[mcp_servers.openclaw-browser]",
        'command = "/opt/homebrew/bin/npx"',
        'args = ["-y", "chrome-devtools-mcp@latest", "--browserUrl", "http://127.0.0.1:18800", "--no-usage-statistics"]',
        "",
        "[mcp_servers.supabase]",
        'url = "https://mcp.supabase.com/mcp"',
        "",
        "[mcp_servers.github-copilot]",
        'url = "https://api.githubcopilot.com/mcp/"',
        'bearer_token_env_var = "GITHUB_PERSONAL_ACCESS_TOKEN"',
        "",
        "[mcp_servers.linear]",
        'url = "https://mcp.linear.app/mcp"',
        "",
        "[mcp_servers.sentry]",
        'url = "https://mcp.sentry.dev/mcp"',
        "",
        "[mcp_servers.figma]",
        'url = "https://mcp.figma.com/mcp"',
        "",
        "[mcp_servers.gitlab]",
        'url = "https://gitlab.com/api/v4/mcp"',
        "",
        "[mcp_servers.huggingface-skills]",
        'url = "https://huggingface.co/mcp?login"',
        "",
        "[mcp_servers.intercom]",
        'url = "https://mcp.intercom.com/mcp"',
        "",
        "[mcp_servers.slack]",
        'url = "https://mcp.slack.com/mcp"',
        "",
        "[mcp_servers.vercel-mcp]",
        'url = "https://mcp.vercel.com"',
        "",
        "[mcp_servers.zapier]",
        'url = "https://mcp.zapier.com/api/v1/connect"',
        "",
    ]

    if profile == "full":
        # Full-профиль не копирует Telegram sessions: сервер возьмёт account-local ~/.krab_mcp_sessions.
        lines += [
            "[mcp_servers.krab-telegram]",
            f"command = {_q(PRIMARY_REPO_PATH / 'venv/bin/python')}",
            f"args = [{_q(PRIMARY_REPO_PATH / 'scripts/run_telegram_mcp_account.py')}, \"--session-name\", \"kraab\"]",
            "startup_timeout_sec = 20.0",
            "",
            "[mcp_servers.krab-telegram-test]",
            f"command = {_q(PRIMARY_REPO_PATH / 'venv/bin/python')}",
            f"args = [{_q(PRIMARY_REPO_PATH / 'scripts/run_telegram_mcp_account.py')}, \"--session-name\", \"p0lrd_cc\"]",
            "startup_timeout_sec = 20.0",
            "",
            "[mcp_servers.krab-telegram-test.tools.krab_restart_gateway]",
            'approval_mode = "approve"',
            "",
        ]

    return lines


def _existing_skill_paths(home: Path) -> Iterable[Path]:
    """Возвращает включаемые skill paths, которые реально существуют после копирования."""
    skills_root = home / ".codex" / "skills"
    for skill_name in ENABLED_SKILL_NAMES:
        path = skills_root / skill_name / "SKILL.md"
        if path.exists():
            yield path


def _render_config(home: Path, profile: str, source_claude_marketplace: Path) -> str:
    """Генерирует минимальный, переносимый `config.toml` для текущей учётки."""
    lines: list[str] = [
        'model = "gpt-5.5"',
        'model_reasoning_effort = "medium"',
        "personality = 'pragmatic'",
        "",
    ]
    lines += _render_mcp(profile)

    for path in _existing_skill_paths(home):
        lines += [
            "[[skills.config]]",
            f"path = {_q(path)}",
            "enabled = true",
            "",
        ]

    for plugin in ENABLED_PLUGINS:
        lines += [
            f"[plugins.{_q(plugin)}]",
            "enabled = true",
            "",
        ]

    for project_path in (PRIMARY_REPO_PATH, SHARED_REPO_PATH, home / ".openclaw/workspace-main-messaging"):
        lines += [
            f"[projects.{_q(project_path)}]",
            'trust_level = "trusted"',
            "",
        ]

    if source_claude_marketplace.exists():
        lines += [
            "[marketplaces.claude-plugins-official]",
            f"last_updated = {_q(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))}",
            'source_type = "local"',
            f"source = {_q(source_claude_marketplace)}",
            "",
        ]

    return "\n".join(lines)


def _write_config(home: Path, profile: str, source_claude_marketplace: Path, dry_run: bool) -> dict[str, object]:
    """Пишет переносимый Codex config и проверяет TOML-синтаксис."""
    config_path = home / ".codex" / "config.toml"
    config_text = _render_config(home, profile, source_claude_marketplace)
    tomllib.loads(config_text)
    backup = _backup_file(config_path, dry_run)
    if not dry_run:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(config_text + "\n", encoding="utf-8")
        config_path.chmod(0o600)
    return {
        "path": str(config_path),
        "ok": True,
        "backup": backup,
        "profile": profile,
        "dry_run": dry_run,
    }


def _check_readiness(home: Path) -> dict[str, object]:
    """Проверяет, что dev-layer установлен без проверки OAuth-секретов."""
    config_path = home / ".codex" / "config.toml"
    skills_manifest = home / ".codex" / "skills" / ".imported-from-claude" / "manifest.tsv"
    plugin_cache = home / ".codex" / "plugins" / "cache"
    result: dict[str, object] = {
        "user": getpass.getuser(),
        "home": str(home),
        "config_exists": config_path.exists(),
        "skills_manifest_exists": skills_manifest.exists(),
        "plugin_cache_exists": plugin_cache.exists(),
        "auth_json_exists": (home / ".codex" / "auth.json").exists(),
        "sentry_env": {
            name: bool(os.environ.get(name))
            for name in ("SENTRY_AUTH_TOKEN", "SENTRY_ORG", "SENTRY_PROJECT", "SENTRY_BASE_URL")
        },
    }
    if config_path.exists():
        try:
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            result["config_ok"] = True
            result["mcp_count"] = len(config.get("mcp_servers", {}))
            result["enabled_plugins"] = sum(
                1 for item in config.get("plugins", {}).values() if item.get("enabled")
            )
        except Exception as exc:  # noqa: BLE001
            result["config_ok"] = False
            result["config_error"] = str(exc)
    if skills_manifest.exists():
        result["imported_claude_skills"] = sum(1 for _ in skills_manifest.open(encoding="utf-8"))
    return result


def sync_dev_layer(
    *,
    home: Path,
    source_codex: Path,
    source_claude_marketplace: Path,
    profile: str,
    dry_run: bool,
) -> dict[str, object]:
    """Главная операция синхронизации безопасного слоя."""
    target_codex = home / ".codex"
    copied: list[dict[str, object]] = []

    for rel_dir in SAFE_COPY_DIRS:
        copied.append(_copy_tree(source_codex / rel_dir, target_codex / rel_dir, dry_run))

    for rel_file in SAFE_COPY_FILES:
        copied.append(_copy_file(source_codex / rel_file, target_codex / rel_file, dry_run))

    config_report = _write_config(home, profile, source_claude_marketplace, dry_run)
    readiness = _check_readiness(home) if not dry_run else {}

    report = {
        "ok": all(item.get("ok") for item in copied) and bool(config_report.get("ok")),
        "mode": "sync",
        "user": getpass.getuser(),
        "home": str(home),
        "profile": profile,
        "copied": copied,
        "config": config_report,
        "readiness": readiness,
        "secrets_policy": "auth.json, OAuth/session state, browser profiles, Telegram sessions and ~/.openclaw were not copied",
    }
    if not dry_run:
        marker = target_codex / ".krab_multi_account_sync.json"
        marker.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    """CLI entrypoint для `.command`-лаунчеров."""
    parser = argparse.ArgumentParser(description="Sync safe Codex dev-layer for Krab helper accounts")
    parser.add_argument("--profile", choices=("dev-tools", "full"), default="dev-tools")
    parser.add_argument("--source-codex", type=Path, default=DEFAULT_SOURCE_CODEX)
    parser.add_argument(
        "--source-claude-marketplace",
        type=Path,
        default=DEFAULT_SOURCE_CLAUDE_MARKETPLACE,
    )
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.check_only:
        print(json.dumps(_check_readiness(args.home), ensure_ascii=False, indent=2))
        return 0

    report = sync_dev_layer(
        home=args.home,
        source_codex=args.source_codex,
        source_claude_marketplace=args.source_claude_marketplace,
        profile=args.profile,
        dry_run=args.dry_run,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
