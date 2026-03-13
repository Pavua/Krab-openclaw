#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
One-click восстановление runtime-конфига OpenClaw для стабильной работы каналов.

Что исправляет:
1) Синхронизирует Gemini API key (AI Studio, формат `AIza...`) в:
   - ~/.openclaw/agents/main/agent/models.json
   - ~/.openclaw/openclaw.json
   Дополнительно синхронизирует LM Studio API token в providers.lmstudio,
   чтобы direct-каналы не падали в cloud fallback из-за stale `local-dummy-key`.
2) Убирает залипшие channel session overrides вида `lmstudio/local`,
   из-за которых каналы Telegram/iMessage/WhatsApp могут падать с
   `400 No models loaded`.
   Дополнительно снимает global-agent overrides `lmstudio/local` в openclaw.json,
   чтобы embedded agent не падал с тем же симптомом вне userbot.
3) По желанию переводит DM policy каналов в `allowlist`, чтобы убрать
   навязчивые pairing-сообщения внешним контактам.
4) Нормализует allowlist-файлы (например, удаляет слишком широкие маски).
5) Отключает compaction memory flush для direct-каналов, чтобы OpenClaw
   не зацикливался на служебном `NO_REPLY` вместо пользовательского ответа.
6) Выставляет `replyToMode=off` для внешних каналов, чтобы transport не
   приклеивал служебные `[[reply_to:*]]` к началу ответа.
7) Сбрасывает уже отравленные direct-session, если в transcript найдены
   `Pre-compaction memory flush` / `NO_REPLY`.
8) Выключает reasoning по умолчанию для внешних OpenClaw-каналов, чтобы
   hidden-thinking не съедал бюджет ответа и не ломал outbound transport.
9) Патчит iMessage transport OpenClaw, чтобы он не вшивал видимый
   `[[reply_to:*]]` в пользовательский текст.

Почему это отдельный скрипт:
- Runtime OpenClaw хранится вне репозитория (~/.openclaw), поэтому нужен
  явный и повторяемый "ремонт" без ручного редактирования JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parent.parent
MANAGED_OUTPUT_SANITIZER_PLUGIN_ID = "krab-output-sanitizer"
MANAGED_OUTPUT_SANITIZER_PLUGIN_DIR = REPO_ROOT / "plugins" / MANAGED_OUTPUT_SANITIZER_PLUGIN_ID
MANAGED_OUTPUT_SANITIZER_PLUGIN_FILES = ("index.mjs", "openclaw.plugin.json")
LOCAL_MARKERS = {"local", "lmstudio", "lmstudio/local", "google/local"}
DEFAULT_CHANNELS = ("telegram", "imessage", "whatsapp", "signal", "discord", "slack")
SUPPORTED_REPLY_TO_MODE_CHANNELS = {"telegram", "discord", "slack"}
POLLUTED_SESSION_MARKERS = (
    "pre-compaction memory flush",
    "store durable memories now",
    "if nothing to store, reply with no_reply",
    "\"content\":\"NO_REPLY\"",
    "\"content\": \"NO_REPLY\"",
    "connection error.",
    "connection error\"",
    "в данный момент генерация ответа не удалась из-за системной ошибки",
    "[current message - respond to this]",
)
POLLUTED_TOOL_PACKET_MARKERS = (
    '"role":"toolresult"',
    '"role":"toolcall"',
    '"type":"toolresult"',
    '"type":"toolcall"',
    "<|begin_of_box|>",
    "<|end_of_box|>",
    "tool [[reply_to_current]]",
    "tool [[reply_to:",
)
POLLUTED_EXTERNAL_CAPABILITY_MARKERS = (
    "[[reply_to:",
    "[[reply_to_current]]",
    "heartbeat-state.json",
    "browser",
    "cron",
    "voice",
    "tts",
    "session_status",
    "мой доступ к твоей вкладке chrome теперь работает корректно",
    "я могу использовать браузер",
    "крон работает",
    "хардбит настроен",
)
BROKEN_CRON_DELIVERY_ERROR_MARKERS = (
    "delivering to whatsapp requires target",
    "requires target <e.164|group jid>",
)
LEGACY_TRANSCRIPT_BOOTSTRAP_MARKERS = (
    '<available_skills>',
    '"cwd":"/users/pablito/.openclaw/workspace"',
    'workspace "/users/pablito/.openclaw/workspace',
    'workspace \'/users/pablito/.openclaw/workspace',
    '/.openclaw/workspace/skills/',
    'apple-notes',
    'apple-reminders',
    'gh-issues',
    'coding-agent',
)
AUTH_FALLBACK_SESSION_MARKERS = (
    "malformed lm studio api token",
    "fallbacknoticereason\":\"auth\"",
    "\"fallbacknoticereason\": \"auth\"",
    "local-dumm",
)
THINKING_PATCH_MODELS = ("lmstudio/local",)
IMESSAGE_REPLY_PATCH_MARKER = "Краб: iMessage показывает [[reply_to:*]] как обычный текст"
IMESSAGE_REPLY_TAG_REGEX_JS = (
    '/^\\s*\\[\\[\\s*(?:reply_to_current|reply_to\\s*:[^\\]]+|reply_to_[^\\]]+)\\s*\\]\\]\\s*/i'
)
LMSTUDIO_PROVIDER_DEFAULT_MAX_TOKENS = 2048
DEFAULT_OWNER_ALIASES = ["По", "Павел", "Pavel", "Pablo"]
SAFE_GUEST_ALLOWED_TOOLS = ["web_search", "web_fetch", "weather", "time"]
SAFE_EXTERNAL_ALLOWED_TOOLS = ["web_search", "web_fetch", "weather", "time"]
LEGACY_OWNER_SKILL_MARKERS = (
    "<available_skills>",
    "/opt/homebrew/lib/node_modules/openclaw/skills/",
    "~/.openclaw/workspace/skills/",
    "coding-agent",
    "healthcheck",
    "apple-notes",
    "apple-reminders",
    "gh-issues",
)
LEGACY_SESSION_RESET_KEYS = (
    "skillsSnapshot",
    "systemPromptReport",
    "systemPromptDigest",
    "systemPromptHash",
    "workspaceSnapshot",
    "availableSkills",
    "available_skills",
    "skillsPrompt",
)
MAIN_AGENT_MESSAGING_WORKSPACE_NAME = "workspace-main-messaging"
MAIN_AGENT_MESSAGING_SOUL = """# Краб: внешний messaging-агент

Этот workspace обслуживает внешние каналы OpenClaw: Telegram Bot, WhatsApp, iMessage и похожие transport-поверхности.

## Главные правила

1. Будь кратким, честным и прикладным.
2. Не заявляй, что у тебя есть доступ к cron, браузеру, shell, файловой системе, интернету, голосу или внешним интеграциям, если это не подтверждено результатом текущего runtime/tool-вызова.
3. Telegram Bot считай резервным transport: основной owner-канал живёт в отдельном Python userbot-контуре.
4. Если возможность доступна только в отдельном Python userbot-контуре, прямо так и говори: "это доступно в userbot, но не подтверждено для этого внешнего канала".
5. Если спрашивают, где ты сейчас работаешь, отвечай кратко: "В этом диалоге отвечает reserve Telegram Bot; основной owner-канал — Python userbot".
6. Не предлагай пользователю shell-скрипты, crontab, Python-хелперы и другие owner/workspace-решения как будто ты уже выполнил их сам.
7. На вопросы "что умеешь" и "что не умеешь" отвечай по фактическому состоянию именно этого канала и текущего runtime.
8. Если нет подтверждённого доступа к браузеру или интернету, говори это прямо и без фантазий.
9. Если транспорт просит ответ, но фактов мало, лучше дать короткий truthful-ответ, чем выдумывать функциональность.
"""

LEGACY_INVALID_TOOL_KEYS = ("sessions", "message", "agentToAgent", "elevated")


def _imessage_reply_strip_expr(source: str) -> str:
    """
    Возвращает JS-выражение для вырезания служебного reply-tag.

    Почему helper, а не одна строковая константа:
    - на первом шаге инициализации источником должен быть `text`,
      иначе получится обращение к ещё не объявленной переменной `message`;
    - перед отправкой в transport уже нужен текущий `message`, потому что
      текст к этому моменту мог пройти через дополнительные преобразования.
    """
    normalized_source = str(source or "").strip() or "message"
    return f'String({normalized_source} ?? "").replace({IMESSAGE_REPLY_TAG_REGEX_JS}, "")'


def _is_local_marker(value: str) -> bool:
    """True, если значение похоже на generic local/lmstudio override."""
    raw = str(value or "").strip().lower()
    if raw in LOCAL_MARKERS:
        return True
    return raw.startswith("lmstudio/")


def mask_secret(secret: str) -> str:
    """Маскирует секрет для безопасного вывода в лог/JSON."""
    value = str(secret or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def is_ai_studio_key(value: str) -> bool:
    """True, если ключ похож на Gemini AI Studio API key."""
    raw = str(value or "").strip()
    return raw.startswith("AIza") and len(raw) >= 30


def choose_target_key(*, free_key: str, paid_key: str, tier: str) -> tuple[str, str]:
    """
    Выбирает целевой ключ и tier.

    Логика:
    - tier=free/paid: строгий выбор.
    - tier=auto: free, иначе paid.
    """
    free = str(free_key or "").strip()
    paid = str(paid_key or "").strip()
    requested = str(tier or "auto").strip().lower()

    if requested == "free":
        return ("free", free) if is_ai_studio_key(free) else ("", "")
    if requested == "paid":
        return ("paid", paid) if is_ai_studio_key(paid) else ("", "")

    if is_ai_studio_key(free):
        return "free", free
    if is_ai_studio_key(paid):
        return "paid", paid
    return "", ""


def choose_lmstudio_token(*, primary_token: str, legacy_token: str) -> str:
    """
    Возвращает каноничный токен LM Studio из окружения.

    Приоритет:
    1) LM_STUDIO_API_KEY
    2) LM_STUDIO_AUTH_TOKEN (legacy)
    """
    primary = str(primary_token or "").strip()
    legacy = str(legacy_token or "").strip()
    if primary:
        return primary
    if legacy:
        return legacy
    return ""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any], *, backup: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_suffix(path.suffix + f".bak_{stamp}")
        shutil.copy2(path, backup_path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str, *, backup: bool = True) -> None:
    """Записывает текстовый файл с резервной копией при изменении."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_suffix(path.suffix + f".bak_{stamp}")
        shutil.copy2(path, backup_path)
    path.write_text(text, encoding="utf-8")


def sync_managed_output_sanitizer_plugin(
    *,
    openclaw_root: Path,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """
    Синхронизирует repo-managed copy плагина в live runtime OpenClaw.

    Это превращает плагин из "живёт только в ~/.openclaw" в воспроизводимую
    часть проекта, которую можно чинить, тестировать и переносить между чатами.
    """
    source_dir = repo_root / "plugins" / MANAGED_OUTPUT_SANITIZER_PLUGIN_ID
    target_dir = openclaw_root / "extensions" / MANAGED_OUTPUT_SANITIZER_PLUGIN_ID
    if not source_dir.exists():
        return {
            "changed": False,
            "reason": "managed_plugin_missing",
            "source_dir": str(source_dir),
            "target_dir": str(target_dir),
        }

    changed_files: list[str] = []
    synced_files: list[str] = []
    for file_name in MANAGED_OUTPUT_SANITIZER_PLUGIN_FILES:
        source_path = source_dir / file_name
        target_path = target_dir / file_name
        if not source_path.exists():
            return {
                "changed": False,
                "reason": "managed_plugin_file_missing",
                "missing_file": str(source_path),
                "source_dir": str(source_dir),
                "target_dir": str(target_dir),
            }

        source_text = source_path.read_text(encoding="utf-8")
        target_text = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
        if target_text != source_text:
            _write_text(target_path, source_text)
            changed_files.append(str(target_path))
        synced_files.append(str(target_path))

    return {
        "changed": bool(changed_files),
        "source_dir": str(source_dir),
        "target_dir": str(target_dir),
        "changed_files": changed_files,
        "synced_files": synced_files,
    }


def sync_plugin_allowlist(openclaw_path: Path) -> dict[str, Any]:
    """
    Фиксирует явный allowlist доверенных plugin ids в runtime-конфиге.

    Почему это нужно:
    - без `plugins.allow` native security audit считает любые extensions
      потенциально автозагружаемыми и поднимает critical finding;
    - в нашем контуре есть repo-managed `krab-output-sanitizer` и enabled auth
      plugins, которые нужно разрешить явно, а не неявным discover-механизмом;
    - allowlist должен быть воспроизводимым после restart на любой учётке.
    """
    payload = _read_json(openclaw_path)
    plugins = payload.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        plugins = {}
        payload["plugins"] = plugins

    entries = plugins.get("entries")
    enabled_entry_ids: list[str] = []
    if isinstance(entries, dict):
        for plugin_id, entry_payload in entries.items():
            normalized_id = str(plugin_id or "").strip()
            if not normalized_id:
                continue
            if isinstance(entry_payload, dict) and entry_payload.get("enabled") is True:
                enabled_entry_ids.append(normalized_id)

    current_allow = plugins.get("allow")
    current_allow_list = (
        [str(item).strip() for item in current_allow if str(item).strip()]
        if isinstance(current_allow, list)
        else []
    )
    desired_allow = list(
        dict.fromkeys(
            current_allow_list
            + [MANAGED_OUTPUT_SANITIZER_PLUGIN_ID]
            + enabled_entry_ids
        )
    )

    if desired_allow == current_allow_list and isinstance(current_allow, list):
        return {
            "path": str(openclaw_path),
            "changed": False,
            "allow": desired_allow,
        }

    plugins["allow"] = desired_allow
    _write_json(openclaw_path, payload)
    return {
        "path": str(openclaw_path),
        "changed": True,
        "allow": desired_allow,
    }


def repair_output_sanitizer_plugin_config(openclaw_path: Path) -> dict[str, Any]:
    """
    Нормализует runtime-конфиг outbound-плагина для внешних каналов.

    Основная цель:
    - внешние каналы должны быть truthful и text-first;
    - owner/trusted peer не должен автоматически получать широкие tool-права
      только потому, что контакт знаком системе;
    - browser/cron/tts/tool claims разрешаем только после отдельного явного
      проектного решения, а не по наследию старого owner-like bootstrap.
    """
    payload = _read_json(openclaw_path)
    plugins = payload.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        plugins = {}
        payload["plugins"] = plugins

    entries = plugins.setdefault("entries", {})
    if not isinstance(entries, dict):
        entries = {}
        plugins["entries"] = entries

    entry = entries.get(MANAGED_OUTPUT_SANITIZER_PLUGIN_ID)
    if not isinstance(entry, dict):
        entry = {}
        entries[MANAGED_OUTPUT_SANITIZER_PLUGIN_ID] = entry

    config_payload = entry.get("config")
    if not isinstance(config_payload, dict):
        config_payload = {}
        entry["config"] = config_payload

    changed_entries: list[str] = []

    if entry.get("enabled") is not True:
        entry["enabled"] = True
        changed_entries.append("plugins.entries.krab-output-sanitizer.enabled")

    desired_scalar_values = {
        "enabled": True,
        "fallbackText": "На связи. Сформулируй запрос ещё раз, если нужен подробный ответ.",
        "runtimeFailureFallback": "Секунду, локальная модель перезапускается.",
        "guestModeEnabled": True,
        "guestToolGuardEnabled": True,
        "externalChannelGuardEnabled": True,
        "externalChannelToolGuardEnabled": True,
    }
    for key, desired in desired_scalar_values.items():
        if config_payload.get(key) != desired:
            config_payload[key] = desired
            changed_entries.append(f"plugins.entries.krab-output-sanitizer.config.{key}")

    if config_payload.get("guestAllowedTools") != SAFE_GUEST_ALLOWED_TOOLS:
        config_payload["guestAllowedTools"] = list(SAFE_GUEST_ALLOWED_TOOLS)
        changed_entries.append("plugins.entries.krab-output-sanitizer.config.guestAllowedTools")

    if config_payload.get("externalChannelAllowedTools") != SAFE_EXTERNAL_ALLOWED_TOOLS:
        config_payload["externalChannelAllowedTools"] = list(SAFE_EXTERNAL_ALLOWED_TOOLS)
        changed_entries.append("plugins.entries.krab-output-sanitizer.config.externalChannelAllowedTools")

    owner_aliases = config_payload.get("ownerAliases")
    if not isinstance(owner_aliases, list) or not owner_aliases:
        config_payload["ownerAliases"] = list(DEFAULT_OWNER_ALIASES)
        changed_entries.append("plugins.entries.krab-output-sanitizer.config.ownerAliases")

    trusted_peers = config_payload.get("trustedPeers")
    if not isinstance(trusted_peers, dict):
        config_payload["trustedPeers"] = {}
        changed_entries.append("plugins.entries.krab-output-sanitizer.config.trustedPeers")
        trusted_peers = config_payload["trustedPeers"]

    telegram_candidates = derive_telegram_trusted_peers(project_root=REPO_ROOT)
    current_telegram_peers = trusted_peers.get("telegram")
    current_telegram_list = (
        [str(item).strip() for item in current_telegram_peers if str(item).strip()]
        if isinstance(current_telegram_peers, list)
        else []
    )
    desired_telegram_peers = list(dict.fromkeys(current_telegram_list + telegram_candidates))
    if desired_telegram_peers and desired_telegram_peers != current_telegram_list:
        trusted_peers["telegram"] = desired_telegram_peers
        changed_entries.append("plugins.entries.krab-output-sanitizer.config.trustedPeers.telegram")

    if changed_entries:
        _write_json(openclaw_path, payload)

    return {
        "path": str(openclaw_path),
        "changed": bool(changed_entries),
        "entries": changed_entries,
    }


def _normalized_env_subjects(raw_items: list[str]) -> list[str]:
    """Нормализует потенциальные Telegram subjects из env/runtime hints."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        item = str(raw or "").strip()
        if not item:
            continue
        if item.startswith("@"):
            item = item[1:]
        if not item:
            continue
        if item in {"*", "+"}:
            continue
        if item not in seen:
            seen.add(item)
            normalized.append(item)
    return normalized


def _resolve_telegram_numeric_ids_via_session(
    *,
    project_root: Path,
    usernames: list[str],
) -> list[str]:
    """
    Пытается безопасно зарезолвить Telegram usernames в numeric sender IDs.

    Почему через копию session:
    - живой `kraab.session` часто держится рантаймом под SQLite lock;
    - нам нужен best-effort resolve без остановки userbot;
    - копия session-файла позволяет прочитать MTProto авторизацию отдельно.
    """
    normalized_usernames = [str(item).strip().lstrip("@") for item in usernames if str(item).strip()]
    if not normalized_usernames:
        return []

    api_id_raw = str(os.getenv("TELEGRAM_API_ID", "") or "").strip()
    api_hash = str(os.getenv("TELEGRAM_API_HASH", "") or "").strip()
    if not api_id_raw or not api_hash:
        return []

    try:
        api_id = int(api_id_raw)
    except ValueError:
        return []

    session_name = str(os.getenv("TELEGRAM_SESSION_NAME", "kraab") or "kraab").strip() or "kraab"
    session_dir = project_root / "data" / "sessions"
    session_src = session_dir / f"{session_name}.session"
    if not session_src.exists():
        return []

    try:
        from pyrogram import Client
    except Exception:
        return []

    resolved_ids: list[str] = []
    seen: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="krab_telegram_resolve_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        session_copy = tmp_path / f"{session_name}_copy.session"
        try:
            shutil.copy2(session_src, session_copy)
        except OSError:
            return []

        try:
            with Client(f"{session_name}_copy", api_id=api_id, api_hash=api_hash, workdir=str(tmp_path)) as app:
                for username in normalized_usernames:
                    try:
                        user = app.get_users(username)
                    except Exception:
                        continue
                    user_id = str(getattr(user, "id", "") or "").strip()
                    if user_id and user_id not in seen:
                        seen.add(user_id)
                        resolved_ids.append(user_id)
        except sqlite3.Error:
            return []
        except Exception:
            return []
    return resolved_ids


def derive_telegram_trusted_peers(*, project_root: Path) -> list[str]:
    """
    Собирает trusted peers для Telegram из env и, при возможности, из session-resolve.

    Это нужно для двух сценариев:
    - reserve-safe repair должен уметь восстановить allowlist даже если credentials/*
      ещё не заполнены руками;
    - на второй macOS-учётке у нас часто есть owner usernames, но нет заранее
      записанного numeric chat id в `.env`.
    """
    direct_subjects = _normalized_env_subjects(
        [
            str(os.getenv("OPENCLAW_TELEGRAM_CHAT_ID", "") or ""),
            str(os.getenv("OWNER_TELEGRAM_ID", "") or ""),
            str(os.getenv("OPENCLAW_ALERT_TARGET", "") or ""),
            str(os.getenv("OWNER_USERNAME", "") or ""),
            *[part for part in str(os.getenv("OWNER_USER_IDS", "") or "").split(",")],
            *[part for part in str(os.getenv("ALLOWED_USERS", "") or "").split(",")],
        ]
    )
    numeric_ids = [item for item in direct_subjects if item.isdigit()]
    usernames = [item for item in direct_subjects if not item.isdigit()]
    resolved_numeric_ids = _resolve_telegram_numeric_ids_via_session(
        project_root=project_root,
        usernames=usernames,
    )
    return list(dict.fromkeys(numeric_ids + resolved_numeric_ids + usernames))


def resolve_telegram_bot_token() -> str:
    """
    Возвращает каноничный bot token для native Telegram-контура OpenClaw.

    Почему helper отдельный:
    - repo/runtime glue исторически использовал `OPENCLAW_TELEGRAM_BOT_TOKEN`;
    - сам OpenClaw native transport ожидает `TELEGRAM_BOT_TOKEN` либо
      `channels.telegram.botToken` внутри runtime-конфига;
    - для repair важно свести оба мира к одному truth-источнику.
    """
    preferred = str(os.getenv("OPENCLAW_TELEGRAM_BOT_TOKEN", "") or "").strip()
    legacy = str(os.getenv("TELEGRAM_BOT_TOKEN", "") or "").strip()
    return preferred or legacy


def sync_telegram_channel_token(openclaw_path: Path) -> dict[str, Any]:
    """
    Синхронизирует Telegram bot token в native runtime-конфиг OpenClaw.

    Почему это нужно:
    - reserve-safe owner-слой мог выглядеть зелёным даже без реального
      `channels.telegram.botToken`;
    - `openclaw status` при этом честно показывал `Telegram: not configured`
      и `tokenSource=none`;
    - хранение токена прямо в per-account runtime-конфиге делает контур
      воспроизводимым после restart на текущей macOS-учётке.
    """
    payload = _read_json(openclaw_path)
    channels = payload.setdefault("channels", {})
    if not isinstance(channels, dict):
        channels = {}
        payload["channels"] = channels

    telegram = channels.get("telegram")
    if not isinstance(telegram, dict):
        telegram = {}
        channels["telegram"] = telegram

    token = resolve_telegram_bot_token()
    if not token:
        return {
            "path": str(openclaw_path),
            "changed": False,
            "token_present": False,
            "reason": "missing_env_token",
        }

    current = str(telegram.get("botToken", "") or "").strip()
    if current == token:
        return {
            "path": str(openclaw_path),
            "changed": False,
            "token_present": True,
            "token_masked": mask_secret(token),
        }

    telegram["botToken"] = token
    _write_json(openclaw_path, payload)
    return {
        "path": str(openclaw_path),
        "changed": True,
        "token_present": True,
        "token_masked": mask_secret(token),
    }


def bootstrap_missing_channels(openclaw_path: Path, channels: tuple[str, ...]) -> dict[str, Any]:
    """
    Создаёт минимальный channel block для отсутствующих каналов, если это безопасно.

    Почему это нужно:
    - раньше repair-path молча ничего не делал, когда `channels.telegram` отсутствовал;
    - owner UI тогда честно показывал reserve disabled, но self-healing не происходил;
    - для Telegram reserve-safe нам нужен хотя бы минимальный runtime block, чтобы
      дальше apply_dm_policy/apply_group_policy могли довести его до allowlist-режима.
    """
    payload = _read_json(openclaw_path)
    channel_cfg = payload.setdefault("channels", {})
    created: list[str] = []
    details: dict[str, Any] = {}

    for channel in channels:
        if channel in channel_cfg and isinstance(channel_cfg.get(channel), dict):
            continue
        if channel != "telegram":
            continue
        token_present = bool(resolve_telegram_bot_token())
        if not token_present:
            details[channel] = {"created": False, "reason": "missing_bot_token"}
            continue
        trusted_peers = derive_telegram_trusted_peers(project_root=REPO_ROOT)
        numeric_ids = _filter_telegram_sender_ids(trusted_peers)
        channel_cfg[channel] = {
            "enabled": True,
            "streamMode": "partial",
            "dmPolicy": "pairing",
            "groupPolicy": "open",
            "replyToMode": "off",
        }
        if numeric_ids:
            channel_cfg[channel]["groupAllowFrom"] = list(dict.fromkeys(numeric_ids))
            # Для native Telegram-конфига OpenClaw allowFrom тоже должен быть numeric-only.
            channel_cfg[channel]["allowFrom"] = list(dict.fromkeys(numeric_ids))
        created.append(channel)
        details[channel] = {
            "created": True,
            "trusted_peers": trusted_peers,
            "numeric_sender_ids": numeric_ids,
        }

    if created:
        _write_json(openclaw_path, payload)

    return {
        "path": str(openclaw_path),
        "changed": bool(created),
        "channels": created,
        "details": details,
    }


def _resolve_session_file_path(sessions_path: Path, session_file: str, session_id: str = "") -> Path | None:
    """Преобразует sessionFile/sessionId из sessions.json в абсолютный путь transcript'а."""
    raw = str(session_file or "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            return candidate
        return sessions_path.parent / candidate

    # У transport-сессий OpenClaw нередко есть только sessionId без sessionFile.
    # В этом случае transcript лежит рядом как `<sessionId>.jsonl`.
    sid = str(session_id or "").strip()
    if sid:
        return sessions_path.parent / f"{sid}.jsonl"
    return None


def _session_file_looks_polluted(path: Path) -> bool:
    """
    Определяет отравлённый transcript direct-session.

    Почему по содержимому, а не по флагу:
    - OpenClaw сохраняет service-turn'ы прямо в jsonl;
    - после этого канал может получать `NO_REPLY` вместо обычного ответа,
      хотя транспорт и модель формально "живы".
    """
    if not path.exists() or not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
    if any(marker in text for marker in POLLUTED_SESSION_MARKERS):
        return True

    # Внешние channel/backing-session не должны накапливать tool-packet'ы,
    # если они уже смешались с reply-tag или ложными capability-утверждениями.
    # Именно такой transcript потом заставляет канал «проверять cron/browser»
    # и не отправлять нормальный пользовательский ответ наружу.
    has_tool_packet = any(marker in text for marker in POLLUTED_TOOL_PACKET_MARKERS)
    has_external_capability_pollution = any(marker in text for marker in POLLUTED_EXTERNAL_CAPABILITY_MARKERS)
    return has_tool_packet and has_external_capability_pollution


def _session_file_looks_auth_fallback(path: Path) -> bool:
    """
    Определяет direct-session, уже отравленную fallback'ом из-за сломанного LM Studio токена.

    Почему это нужно:
    - после старого `local-dummy-key` OpenClaw сохранял в transcript auth-ошибку и
      fallback state на Gemini;
    - даже после починки ключа такие direct-session продолжают держать старый маршрут
      и дают ложное впечатление, что локальный канал всё ещё "не работает".
    """
    if not path.exists() or not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
    return any(marker in text for marker in AUTH_FALLBACK_SESSION_MARKERS)


def _session_file_looks_like_legacy_owner_bootstrap(path: Path) -> bool:
    """
    Определяет transcript, унаследованный от старого owner/bootstrap workspace.

    Почему смотрим именно transcript:
    - часть старых `agent:main:openai:*` сессий уже потеряла полезные runtime-флаги,
      но всё ещё тащит старый bootstrap prompt и owner-only skills прямо в jsonl;
    - такие backing-сессии дают внешним каналам ложные claims про cron/browser/files
      и периодически ломают доставку ответа, хотя транспорт и модель "живы".
    """
    if not path.exists() or not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
    return any(marker in text for marker in LEGACY_TRANSCRIPT_BOOTSTRAP_MARKERS)


def _session_file_uses_legacy_workspace(path: Path) -> bool:
    """
    Определяет transcript, стартовавший из старого workspace вместо messaging-workspace.

    Почему это важно:
    - даже после починки `openclaw.json` старая active-session может продолжать
      жить на `~/.openclaw/workspace`;
    - такие direct/backing session тащат старый bootstrap, owner-only claims и
      нестабильную доставку наружу, пока их явно не пересоздать.
    """
    if not path.exists() or not path.is_file():
        return False
    try:
        first_line = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        return False
    try:
        payload = json.loads(first_line)
    except json.JSONDecodeError:
        return False
    cwd = str(payload.get("cwd") or "").strip()
    if not cwd:
        return False
    if cwd.endswith("/.openclaw/workspace"):
        return True
    return MAIN_AGENT_MESSAGING_WORKSPACE_NAME not in cwd


def _session_looks_like_legacy_owner_bootstrap(meta: dict[str, Any]) -> bool:
    """
    Определяет session metadata, унаследовавшую старый owner-like bootstrap.

    Признаки:
    - старый skills catalog из `.openclaw/workspace`;
    - workspace, отличный от `workspace-main-messaging`.
    """
    if not isinstance(meta, dict):
        return False

    skills_snapshot = meta.get("skillsSnapshot")
    if isinstance(skills_snapshot, dict):
        prompt = str(skills_snapshot.get("prompt") or "").strip().lower()
        if prompt and any(marker in prompt for marker in LEGACY_OWNER_SKILL_MARKERS):
            return True

    system_prompt_report = meta.get("systemPromptReport")
    if isinstance(system_prompt_report, dict):
        workspace_dir = str(system_prompt_report.get("workspaceDir") or "").strip()
        if workspace_dir.endswith("/.openclaw/workspace"):
            return True
        if workspace_dir and MAIN_AGENT_MESSAGING_WORKSPACE_NAME not in workspace_dir:
            return True

    return False


def _archive_session_file(path: Path, *, reason: str) -> str:
    """Архивирует проблемный transcript, чтобы не терять артефакт для разбора."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    archived = path.with_name(f"{path.stem}.{reason}_{stamp}{path.suffix}")
    shutil.move(str(path), str(archived))
    return str(archived)


def _is_already_archived_session_file(path: Path) -> bool:
    """
    Определяет transcript, уже ранее заархивированный repair-скриптом.

    Почему нужен явный фильтр:
    - `sessions/*.jsonl` хранит и живые transcript-файлы, и уже архивированные
      артефакты вида `*.polluted_*.jsonl`;
    - при повторном repair нельзя снова перекладывать эти архивы, иначе мы
      потеряем понятную историю причин и раздуем имена файлов.
    """
    stem = str(path.stem or "").lower()
    archived_markers = (
        ".polluted_",
        ".authfallback_",
        ".legacybootstrap_",
        ".legacyworkspace_",
    )
    return any(marker in stem for marker in archived_markers)


def _collect_referenced_session_files(
    sessions_path: Path,
    payload: dict[str, Any],
) -> set[Path]:
    """
    Возвращает набор transcript-файлов, на которые сейчас ссылается live index.

    Это позволяет отделить реально используемые session-файлы от осиротевшего
    исторического хвоста, который уже не фигурирует в `sessions.json`, но всё ещё
    лежит в каталоге и мешает ручной диагностике.
    """
    referenced: set[Path] = set()
    for meta in payload.values():
        if not isinstance(meta, dict):
            continue
        session_file = _resolve_session_file_path(
            sessions_path,
            str(meta.get("sessionFile", "") or ""),
            str(meta.get("sessionId", "") or ""),
        )
        if session_file is None:
            continue
        referenced.add(session_file.resolve())
    return referenced


def _archive_orphaned_problem_session_files(
    sessions_path: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Архивирует осиротевшие transcript-файлы с явными признаками pollution.

    Почему это отдельный проход:
    - live `sessions.json` уже может быть чистым, но в каталоге остаются старые
      `*.jsonl` со служебным мусором, reply-tag артефактами и legacy bootstrap;
    - такие файлы путают диагностику и мешают быстро понять, какие сессии ещё
      действительно активны, а какие уже давно должны считаться архивом.
    """
    sessions_dir = sessions_path.parent
    if not sessions_dir.exists():
        return {
            "archived_files": [],
            "reset_polluted_orphan_session_files": 0,
            "reset_auth_fallback_orphan_session_files": 0,
            "reset_legacy_bootstrap_orphan_session_files": 0,
            "reset_legacy_workspace_orphan_session_files": 0,
        }

    referenced = _collect_referenced_session_files(sessions_path, payload)
    archived_files: list[str] = []
    reset_polluted_orphan_session_files = 0
    reset_auth_fallback_orphan_session_files = 0
    reset_legacy_bootstrap_orphan_session_files = 0
    reset_legacy_workspace_orphan_session_files = 0

    for path in sorted(sessions_dir.glob("*.jsonl")):
        if _is_already_archived_session_file(path):
            continue
        try:
            resolved_path = path.resolve()
        except OSError:
            continue
        if resolved_path in referenced:
            continue

        reason = ""
        if _session_file_looks_polluted(path):
            reason = "polluted"
            reset_polluted_orphan_session_files += 1
        elif _session_file_looks_auth_fallback(path):
            reason = "authfallback"
            reset_auth_fallback_orphan_session_files += 1
        elif _session_file_looks_like_legacy_owner_bootstrap(path):
            reason = "legacybootstrap"
            reset_legacy_bootstrap_orphan_session_files += 1
        elif _session_file_uses_legacy_workspace(path):
            reason = "legacyworkspace"
            reset_legacy_workspace_orphan_session_files += 1

        if not reason:
            continue
        archived_files.append(_archive_session_file(path, reason=reason))

    return {
        "archived_files": archived_files,
        "reset_polluted_orphan_session_files": reset_polluted_orphan_session_files,
        "reset_auth_fallback_orphan_session_files": reset_auth_fallback_orphan_session_files,
        "reset_legacy_bootstrap_orphan_session_files": reset_legacy_bootstrap_orphan_session_files,
        "reset_legacy_workspace_orphan_session_files": reset_legacy_workspace_orphan_session_files,
    }


def _remove_transport_aliases_for_session_id(payload: dict[str, Any], session_id: str) -> list[str]:
    """
    Удаляет transport-алиасы, ссылающиеся на уже сброшенную backing-session.

    Зачем:
    - `telegram:slash:*` хранит только sessionId и не содержит transcript;
    - если backing `agent:main:openai:*` уже сломан/удалён, alias остаётся висеть
      и Telegram bot продолжает обращаться к несуществующей или отравлённой сессии.
    """
    target = str(session_id or "").strip()
    if not target:
        return []

    removed: list[str] = []
    for key, meta in list(payload.items()):
        if key.startswith("agent:main:"):
            continue
        if not isinstance(meta, dict):
            continue
        current_session_id = str(meta.get("sessionId") or "").strip()
        if current_session_id != target:
            continue
        payload.pop(key, None)
        removed.append(key)
    return removed


def _normalize_local_model_id(model_id: str) -> str:
    """
    Нормализует ID локальной модели для LM Studio provider catalog.

    Правило:
    - `lmstudio/...` и `local/...` превращаем в голый локальный ID;
    - ID от внешних провайдеров (`openai-codex/...`, `google/...`, `qwen-portal/...`)
      в локальный каталог не пропускаем вообще.

    Почему это важно:
    - иначе repair может записать cloud primary внутрь `models.providers.lmstudio.models`,
      и runtime начинает жить с конфликтующим alias, который не существует в LM Studio.
    """
    raw = str(model_id or "").strip()
    if not raw:
        return ""
    if "/" not in raw:
        return raw
    head, tail = raw.split("/", 1)
    if head.strip().lower() in {"lmstudio", "local"} and tail.strip():
        return tail.strip()
    return ""


def _pick_lmstudio_text_model_id(
    *,
    live_models: list[dict[str, Any]],
    primary_model: str,
    preferred_text_model: str,
) -> str:
    """
    Выбирает текстовую модель для компактного каталога LM Studio.

    Приоритет:
    1) локальный `primary_model`, если он действительно локальный и есть в live-каталоге;
    2) `LOCAL_PREFERRED_MODEL`, если он найден среди live-моделей;
    3) первая не-vision модель из live-каталога;
    4) первая вообще доступная модель как fail-open fallback.
    """
    indexed = {
        str(item.get("id") or "").strip(): item
        for item in live_models
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }

    for candidate in (
        _normalize_local_model_id(primary_model),
        _normalize_local_model_id(preferred_text_model),
        str(preferred_text_model or "").strip(),
    ):
        normalized = str(candidate or "").strip()
        if normalized and normalized in indexed:
            return normalized

    for item in live_models:
        model_id = str(item.get("id") or "").strip()
        if model_id and not bool(item.get("supports_vision", False)):
            return model_id

    for item in live_models:
        model_id = str(item.get("id") or "").strip()
        if model_id:
            return model_id

    return ""


def _derive_lmstudio_catalog_base_url(existing_base_url: str) -> str:
    """Приводит baseUrl provider'а к корню сервера LM Studio без `/v1`."""
    raw = str(existing_base_url or "").strip().rstrip("/")
    if raw.endswith("/v1"):
        return raw[:-3].rstrip("/")
    return raw or "http://localhost:1234"


def _fetch_lmstudio_catalog_models(*, base_url: str, api_key: str) -> list[dict[str, Any]]:
    """
    Запрашивает каталог моделей LM Studio.

    Важно:
    - `/api/v1/models` в LM Studio возвращает `{"models":[...]}`, а не OpenAI-совместимый `data`;
    - для OpenClaw нам достаточно key/display_name/vision/context, без полного сырого payload.
    """
    root_url = _derive_lmstudio_catalog_base_url(base_url)
    headers: dict[str, str] = {}
    token = str(api_key or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["x-api-key"] = token

    requests_plan = (
        (f"{root_url}/api/v1/models", "native"),
        (f"{root_url}/v1/models", "compat"),
    )
    for url, mode in requests_plan:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError):
            continue

        items: list[dict[str, Any]] = []
        if mode == "native":
            raw_models = payload.get("models", [])
            if not isinstance(raw_models, list):
                continue
            for raw in raw_models:
                if not isinstance(raw, dict):
                    continue
                model_id = str(raw.get("key") or raw.get("id") or "").strip()
                if not model_id:
                    continue
                capabilities = raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else {}
                items.append(
                    {
                        "id": model_id,
                        "display_name": str(raw.get("display_name") or model_id),
                        "supports_vision": bool(capabilities.get("vision", False)),
                        "context_window": int(raw.get("max_context_length") or 32768),
                        "size_bytes": int(raw.get("size_bytes") or 0),
                    }
                )
            if items:
                return items
            continue

        raw_models = payload.get("data", [])
        if not isinstance(raw_models, list):
            continue
        for raw in raw_models:
            if not isinstance(raw, dict):
                continue
            model_id = str(raw.get("id") or "").strip()
            if not model_id:
                continue
            items.append(
                {
                    "id": model_id,
                    "display_name": model_id,
                    "supports_vision": False,
                    "context_window": 32768,
                    "size_bytes": 0,
                }
            )
        if items:
            return items
    return []


def _pretty_lmstudio_name(model_id: str) -> str:
    """Строит читаемое имя модели для provider catalog OpenClaw."""
    raw = str(model_id or "").strip()
    if not raw:
        return "LM Studio Model"
    base = raw.split("/")[-1]
    return f"{base} (LM Studio)"


def _build_lmstudio_catalog_entries(
    *,
    live_models: list[dict[str, Any]],
    primary_model: str,
    preferred_text_model: str,
    preferred_vision_model: str,
) -> tuple[list[dict[str, Any]], str]:
    """
    Собирает компактный provider catalog для OpenClaw.

    Стратегия:
    - всегда синхронизируем text primary-модель;
    - vision-модель добавляем только если она явно запрошена (`smallest` или substring),
      чтобы OpenClaw не выгружал Nemotron и не пересаживался молча на случайный маленький VL.
    """
    indexed = {
        str(item.get("id") or "").strip(): item
        for item in live_models
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }

    selected_ids: list[str] = []
    primary_id = _pick_lmstudio_text_model_id(
        live_models=live_models,
        primary_model=primary_model,
        preferred_text_model=preferred_text_model,
    )
    if primary_id:
        selected_ids.append(primary_id)

    preferred_raw = str(preferred_vision_model or "").strip().lower()
    vision_source = "none"
    if preferred_raw and preferred_raw not in {"auto"}:
        vision_models = [item for item in live_models if bool(item.get("supports_vision", False))]
        if preferred_raw == "smallest":
            vision_models.sort(key=lambda item: int(item.get("size_bytes") or 0) or 10**18)
            if vision_models:
                selected_ids.append(str(vision_models[0].get("id") or "").strip())
                vision_source = "smallest"
        else:
            for item in vision_models:
                model_id = str(item.get("id") or "").strip()
                if preferred_raw in model_id.lower():
                    selected_ids.append(model_id)
                    vision_source = "preferred"
                    break

    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for model_id in selected_ids:
        normalized_id = str(model_id or "").strip()
        if not normalized_id or normalized_id in seen_ids:
            continue
        seen_ids.add(normalized_id)
        raw = indexed.get(normalized_id, {})
        supports_vision = bool(raw.get("supports_vision", False))
        display_name = str(raw.get("display_name") or _pretty_lmstudio_name(normalized_id))
        context_window = int(raw.get("context_window") or 32768)
        entries.append(
            {
                "id": normalized_id,
                "name": display_name if display_name.endswith("(LM Studio)") else f"{display_name} (LM Studio)",
                "reasoning": False,
                "input": ["text", "image"] if supports_vision else ["text"],
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "contextWindow": max(32768, context_window),
                "maxTokens": LMSTUDIO_PROVIDER_DEFAULT_MAX_TOKENS,
                "compat": {"maxTokensField": "max_tokens"},
                "api": "openai-completions",
            }
        )

    return entries, vision_source


def repair_lmstudio_provider_catalog(
    path: Path,
    *,
    primary_model: str,
    preferred_text_model: str,
    preferred_vision_model: str,
    lmstudio_token: str,
    live_models: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Синхронизирует compact provider catalog LM Studio внутри live-конфига OpenClaw.

    Почему это критично:
    - после рефакторинга runtime оставался на stale provider catalog с единственной
      `zai-org/glm-4.6v-flash`;
    - из-за этого внешние каналы жили не на том truth, что userbot/runtime-panel.
    """
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return {"path": str(path), "changed": False, "reason": "not_dict"}

    providers = payload.setdefault("models", {}).setdefault("providers", {}) if path.name == "openclaw.json" else payload.setdefault("providers", {})
    if not isinstance(providers, dict):
        return {"path": str(path), "changed": False, "reason": "providers_missing"}

    lmstudio = providers.get("lmstudio")
    if not isinstance(lmstudio, dict):
        return {"path": str(path), "changed": False, "reason": "lmstudio_provider_missing"}

    current_base_url = str(lmstudio.get("baseUrl", "") or "").strip() or "http://localhost:1234/v1"
    catalog_source = "live"
    if live_models is None:
        live_models = _fetch_lmstudio_catalog_models(
            base_url=current_base_url,
            api_key=lmstudio_token,
        )
    if not live_models:
        catalog_source = "fallback"

    entries, vision_source = _build_lmstudio_catalog_entries(
        live_models=live_models,
        primary_model=primary_model,
        preferred_text_model=preferred_text_model,
        preferred_vision_model=preferred_vision_model,
    )
    if not entries:
        primary_id = _pick_lmstudio_text_model_id(
            live_models=live_models,
            primary_model=primary_model,
            preferred_text_model=preferred_text_model,
        )
        if not primary_id:
            return {
                "path": str(path),
                "changed": False,
                "reason": "no_primary_model",
                "catalog_source": catalog_source,
            }
        entries = [
            {
                "id": primary_id,
                "name": _pretty_lmstudio_name(primary_id),
                "reasoning": False,
                "input": ["text"],
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "contextWindow": 32768,
                "maxTokens": LMSTUDIO_PROVIDER_DEFAULT_MAX_TOKENS,
                "compat": {"maxTokensField": "max_tokens"},
                "api": "openai-completions",
            }
        ]
        catalog_source = "fallback"

    previous_models = lmstudio.get("models", [])
    previous_api = str(lmstudio.get("api", "") or "")
    previous_auth = str(lmstudio.get("auth", "") or "")
    changed = False
    if previous_models != entries:
        lmstudio["models"] = entries
        changed = True
    if previous_api != "openai-completions":
        lmstudio["api"] = "openai-completions"
        changed = True
    if previous_auth != "api-key":
        lmstudio["auth"] = "api-key"
        changed = True
    if not str(lmstudio.get("baseUrl", "") or "").strip():
        lmstudio["baseUrl"] = "http://localhost:1234/v1"
        changed = True

    if changed:
        _write_json(path, payload)

    return {
        "path": str(path),
        "changed": changed,
        "catalog_source": catalog_source,
        "models": [str(item.get("id") or "") for item in entries],
        "vision_source": vision_source,
        "base_url": str(lmstudio.get("baseUrl", "") or ""),
    }


def sync_models_json(path: Path, target_key: str, lmstudio_token: str) -> dict[str, Any]:
    """Синхронизирует providers.google.apiKey и providers.lmstudio.apiKey в models.json."""
    payload = _read_json(path)
    providers = payload.setdefault("providers", {})

    google = providers.setdefault("google", {})
    prev = str(google.get("apiKey", "") or "")
    google["apiKey"] = target_key
    google["auth"] = "api-key"
    google["api"] = "google-generative-ai"

    lmstudio = providers.get("lmstudio") if isinstance(providers.get("lmstudio"), dict) else None
    lmstudio_prev = ""
    lmstudio_changed = False
    if lmstudio is not None:
        lmstudio_prev = str(lmstudio.get("apiKey", "") or "")
        if lmstudio_token:
            lmstudio["apiKey"] = lmstudio_token
            lmstudio["auth"] = "api-key"
            lmstudio_changed = lmstudio_prev != lmstudio_token

    _write_json(path, payload)
    return {
        "path": str(path),
        "changed": prev != target_key or lmstudio_changed,
        "prev_key_masked": mask_secret(prev),
        "new_key_masked": mask_secret(target_key),
        "lmstudio_token_present": bool(lmstudio_token),
        "lmstudio_prev_masked": mask_secret(lmstudio_prev),
        "lmstudio_new_masked": mask_secret(lmstudio_token),
        "lmstudio_changed": lmstudio_changed,
    }


def sync_openclaw_json(path: Path, target_key: str, lmstudio_token: str) -> dict[str, Any]:
    """Синхронизирует providers.google.apiKey и providers.lmstudio.apiKey в корневом openclaw.json."""
    payload = _read_json(path)
    models = payload.setdefault("models", {})
    providers = models.setdefault("providers", {})

    google = providers.setdefault("google", {})
    prev = str(google.get("apiKey", "") or "")
    google["apiKey"] = target_key
    google["auth"] = "api-key"
    google["api"] = "google-generative-ai"

    lmstudio = providers.get("lmstudio") if isinstance(providers.get("lmstudio"), dict) else None
    lmstudio_prev = ""
    lmstudio_changed = False
    if lmstudio is not None:
        lmstudio_prev = str(lmstudio.get("apiKey", "") or "")
        if lmstudio_token:
            lmstudio["apiKey"] = lmstudio_token
            lmstudio["auth"] = "api-key"
            lmstudio_changed = lmstudio_prev != lmstudio_token

    _write_json(path, payload)
    return {
        "path": str(path),
        "changed": prev != target_key or lmstudio_changed,
        "prev_key_masked": mask_secret(prev),
        "new_key_masked": mask_secret(target_key),
        "lmstudio_token_present": bool(lmstudio_token),
        "lmstudio_prev_masked": mask_secret(lmstudio_prev),
        "lmstudio_new_masked": mask_secret(lmstudio_token),
        "lmstudio_changed": lmstudio_changed,
    }


def sync_auth_profiles_json(path: Path, target_key: str, lmstudio_token: str) -> dict[str, Any]:
    """
    Синхронизирует auth-profiles.json для живых direct-каналов OpenClaw.

    Почему нужен отдельный шаг:
    - часть transport/direct-сессий берёт API-ключи не из models.json/openclaw.json,
      а из auth-profiles.json;
    - если там остался `local-dummy-key`, внешние каналы получают `401` на
      локальном LM Studio и падают в cloud fallback.
    """
    payload = _read_json(path)
    if not isinstance(payload, dict):
        payload = {}

    def _ensure_profile(name: str) -> dict[str, Any]:
        profile = payload.get(name)
        if not isinstance(profile, dict):
            profile = {}
            payload[name] = profile
        return profile

    changed = False

    google = _ensure_profile("google")
    prev_google = str(google.get("apiKey", "") or "")
    if target_key and prev_google != target_key:
        google["apiKey"] = target_key
        changed = True

    gemini = _ensure_profile("gemini")
    prev_gemini = str(gemini.get("apiKey", "") or "")
    if target_key and prev_gemini != target_key:
        gemini["apiKey"] = target_key
        changed = True

    lmstudio = _ensure_profile("lmstudio")
    prev_lmstudio = str(lmstudio.get("apiKey", "") or "")
    if lmstudio_token and prev_lmstudio != lmstudio_token:
        lmstudio["apiKey"] = lmstudio_token
        changed = True

    if changed:
        _write_json(path, payload)

    return {
        "path": str(path),
        "changed": changed,
        "prev_google_masked": mask_secret(prev_google),
        "new_google_masked": mask_secret(target_key),
        "prev_gemini_masked": mask_secret(prev_gemini),
        "new_gemini_masked": mask_secret(target_key),
        "lmstudio_token_present": bool(lmstudio_token),
        "lmstudio_prev_masked": mask_secret(prev_lmstudio),
        "lmstudio_new_masked": mask_secret(lmstudio_token),
    }


def repair_compaction_memory_flush(openclaw_path: Path) -> dict[str, Any]:
    """
    Отключает service memory flush в compaction.

    Зачем:
    - для direct-каналов такой flush иногда превращается в отдельный
      pseudo-turn с требованием ответить `NO_REPLY`;
    - после этого живая сессия может перестать доставлять нормальные ответы.
    """
    payload = _read_json(openclaw_path)
    agents = payload.setdefault("agents", {})
    if not isinstance(agents, dict):
        agents = {}
        payload["agents"] = agents
    defaults = agents.setdefault("defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}
        agents["defaults"] = defaults
    compaction = defaults.setdefault("compaction", {})
    if not isinstance(compaction, dict):
        compaction = {}
        defaults["compaction"] = compaction
    memory_flush = compaction.setdefault("memoryFlush", {})
    if not isinstance(memory_flush, dict):
        memory_flush = {}
        compaction["memoryFlush"] = memory_flush

    previous = memory_flush.get("enabled")
    changed = previous is not False
    if changed:
        memory_flush["enabled"] = False
        _write_json(openclaw_path, payload)

    return {
        "path": str(openclaw_path),
        "changed": changed,
        "previous_enabled": previous,
        "enabled": False,
    }


def repair_channel_health_monitor(openclaw_path: Path) -> dict[str, Any]:
    """
    Смягчает health-monitor OpenClaw для боевого messaging-профиля.

    Почему это делаем:
    - текущая версия monitor-а считает каналы "stale" просто из-за того,
      что в DM/low-traffic канале давно не было входящих событий;
    - из-за этого Telegram/WhatsApp/iMessage и другие transport-каналы
      перезапускаются по кругу и могут терять доставку ответа;
    - для нашего боевого профиля безопаснее полностью отключить этот монитор,
      чем позволять ему ложноположительно убивать живые соединения.

    Технически:
    - OpenClaw трактует `gateway.channelHealthCheckMinutes = 0`
      как отключение `startChannelHealthMonitor(...)`.
    """
    payload = _read_json(openclaw_path)
    gateway = payload.setdefault("gateway", {})
    if not isinstance(gateway, dict):
        gateway = {}
        payload["gateway"] = gateway

    previous = gateway.get("channelHealthCheckMinutes")
    changed = previous != 0
    if changed:
        gateway["channelHealthCheckMinutes"] = 0
        _write_json(openclaw_path, payload)

    return {
        "path": str(openclaw_path),
        "changed": changed,
        "previous_minutes": previous,
        "channel_health_check_minutes": 0,
        "mode": "disabled_for_stability",
    }


def repair_reply_to_modes(openclaw_path: Path, channels: tuple[str, ...], mode: str) -> dict[str, Any]:
    """
    Выставляет единый replyToMode для внешних каналов.

    Почему:
    - часть transport-адаптеров добавляет reply-маркеры в исходящий текст;
    - для личных DM это даёт видимые артефакты вида `[[reply_to:69787]]`.
    """
    payload = _read_json(openclaw_path)
    channel_cfg = payload.setdefault("channels", {})
    if not isinstance(channel_cfg, dict):
        channel_cfg = {}
        payload["channels"] = channel_cfg

    changed_channels: list[str] = []
    removed_unsupported: list[str] = []
    for channel in channels:
        cfg = channel_cfg.get(channel)
        if not isinstance(cfg, dict):
            continue
        if channel not in SUPPORTED_REPLY_TO_MODE_CHANNELS:
            if "replyToMode" in cfg:
                cfg.pop("replyToMode", None)
                removed_unsupported.append(channel)
            continue
        current = str(cfg.get("replyToMode", "") or "").strip().lower()
        if current == mode:
            continue
        cfg["replyToMode"] = mode
        changed_channels.append(channel)

    if changed_channels or removed_unsupported:
        _write_json(openclaw_path, payload)

    return {
        "path": str(openclaw_path),
        "changed": bool(changed_channels or removed_unsupported),
        "mode": mode,
        "channels": changed_channels,
        "removed_unsupported": removed_unsupported,
    }


def repair_hooks_config(path: Path) -> dict[str, Any]:
    """
    Стабилизирует секцию hooks в openclaw.json.

    Критичный кейс:
    - hooks.enabled=true и пустой hooks.token
      => OpenClaw не стартует с ошибкой
         "hooks.enabled requires hooks.token".
    """
    payload = _read_json(path)
    hooks = payload.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        payload["hooks"] = hooks

    enabled = bool(hooks.get("enabled", False))
    token = str(hooks.get("token", "") or "").strip()

    changed = False
    action = "none"
    if enabled and not token:
        hooks["enabled"] = False
        changed = True
        action = "disabled_hooks_without_token"

    if changed:
        _write_json(path, payload)

    return {
        "path": str(path),
        "changed": changed,
        "hooks_enabled": bool(hooks.get("enabled", False)),
        "token_present": bool(str(hooks.get("token", "") or "").strip()),
        "action": action,
    }


def repair_external_reasoning_defaults(openclaw_path: Path, *, primary_model: str) -> dict[str, Any]:
    """
    Выключает hidden-thinking в live-конфиге внешних каналов OpenClaw.

    Почему это отдельный шаг:
    - userbot уже использует наш local-direct путь с `reasoning=off`;
    - внешние каналы OpenClaw продолжали жить с `thinkingDefault=high`,
      из-за чего часть ответов не доходила до транспорта или приходила
      с деградацией по бюджету.
    """
    payload = _read_json(openclaw_path)
    agents = payload.setdefault("agents", {})
    if not isinstance(agents, dict):
        agents = {}
        payload["agents"] = agents

    defaults = agents.setdefault("defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}
        agents["defaults"] = defaults

    changed_entries: list[str] = []
    if str(defaults.get("thinkingDefault", "") or "").strip().lower() != "off":
        defaults["thinkingDefault"] = "off"
        changed_entries.append("agents.defaults.thinkingDefault")

    defaults_models = defaults.setdefault("models", {})
    if not isinstance(defaults_models, dict):
        defaults_models = {}
        defaults["models"] = defaults_models

    candidate_models: list[str] = []
    primary = str(primary_model or "").strip()
    if primary:
        candidate_models.append(primary)
    candidate_models.extend(THINKING_PATCH_MODELS)

    for model_id in dict.fromkeys(candidate_models):
        entry = defaults_models.get(model_id)
        if not isinstance(entry, dict):
            entry = {}
            defaults_models[model_id] = entry
            changed_entries.append(f"agents.defaults.models[{model_id}]")

        params = entry.get("params")
        if not isinstance(params, dict):
            params = {}
            entry["params"] = params
            changed_entries.append(f"agents.defaults.models[{model_id}].params")

        if str(params.get("thinking", "") or "").strip().lower() != "off":
            params["thinking"] = "off"
            changed_entries.append(f"agents.defaults.models[{model_id}].params.thinking")

    if changed_entries:
        _write_json(openclaw_path, payload)

    return {
        "path": str(openclaw_path),
        "changed": bool(changed_entries),
        "entries": changed_entries,
        "primary_model": primary,
    }


def repair_main_agent_messaging_profile(openclaw_path: Path) -> dict[str, Any]:
    """
    Переводит внешнего `main`-агента OpenClaw в безопасный messaging-профиль.

    Почему это нужно:
    - после рефакторинга `main` жил как owner-like агент с широким workspace
      и системными tool-правами;
    - из-за этого внешние каналы начинали обещать cron, shell, браузер,
      интернет и другие возможности, которые на самом деле доступны только
      в отдельных контурах или вообще не подтверждены текущим runtime.
    """
    payload = _read_json(openclaw_path)
    agents = payload.setdefault("agents", {})
    if not isinstance(agents, dict):
        agents = {}
        payload["agents"] = agents

    agents_list = agents.setdefault("list", [])
    if not isinstance(agents_list, list):
        agents_list = []
        agents["list"] = agents_list

    main_agent: dict[str, Any] | None = None
    for entry in agents_list:
        if isinstance(entry, dict) and str(entry.get("id") or "").strip().lower() == "main":
            main_agent = entry
            break

    if main_agent is None:
        main_agent = {"id": "main"}
        agents_list.append(main_agent)

    openclaw_root = openclaw_path.parent
    main_workspace = openclaw_root / MAIN_AGENT_MESSAGING_WORKSPACE_NAME
    main_workspace.mkdir(parents=True, exist_ok=True)
    soul_path = main_workspace / "SOUL.md"
    previous_soul = soul_path.read_text(encoding="utf-8") if soul_path.exists() else ""
    soul_changed = previous_soul != MAIN_AGENT_MESSAGING_SOUL
    if soul_changed:
        soul_path.write_text(MAIN_AGENT_MESSAGING_SOUL, encoding="utf-8")

    changed_entries: list[str] = []
    desired_workspace = str(main_workspace)
    defaults = agents.setdefault("defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}
        agents["defaults"] = defaults
        changed_entries.append("agents.defaults")

    # Новые backing/direct session не должны стартовать из старого owner-like
    # workspace, иначе каналы снова наследуют bootstrap с лишними claims.
    if str(defaults.get("workspace") or "").strip() != desired_workspace:
        defaults["workspace"] = desired_workspace
        changed_entries.append("agents.defaults.workspace")

    if str(main_agent.get("workspace") or "").strip() != desired_workspace:
        main_agent["workspace"] = desired_workspace
        changed_entries.append("agents.list[main].workspace")

    tools = main_agent.get("tools")
    if not isinstance(tools, dict):
        tools = {}
        main_agent["tools"] = tools
        changed_entries.append("agents.list[main].tools")

    if str(tools.get("profile") or "").strip().lower() != "messaging":
        tools["profile"] = "messaging"
        changed_entries.append("agents.list[main].tools.profile")

    for invalid_key in LEGACY_INVALID_TOOL_KEYS:
        if invalid_key in tools:
            tools.pop(invalid_key, None)
            changed_entries.append(f"agents.list[main].tools.{invalid_key} removed")

    root_tools = payload.get("tools")
    if isinstance(root_tools, dict):
        for invalid_key in LEGACY_INVALID_TOOL_KEYS:
            if invalid_key in root_tools:
                root_tools.pop(invalid_key, None)
                changed_entries.append(f"tools.{invalid_key} removed")

    deny = tools.get("deny")
    desired_deny = ["sessions_send", "sessions_spawn"]
    if not isinstance(deny, list):
        deny = []
        tools["deny"] = deny
        changed_entries.append("agents.list[main].tools.deny")
    normalized_deny = [str(item or "").strip() for item in deny if str(item or "").strip()]
    next_deny = list(dict.fromkeys(normalized_deny + desired_deny))
    if normalized_deny != next_deny:
        tools["deny"] = next_deny
        changed_entries.append("agents.list[main].tools.deny[*]")

    if changed_entries:
        _write_json(openclaw_path, payload)

    return {
        "path": str(openclaw_path),
        "changed": bool(changed_entries or soul_changed),
        "entries": changed_entries,
        "workspace": desired_workspace,
        "soul_path": str(soul_path),
        "soul_changed": soul_changed,
        "tools_profile": str(tools.get("profile") or ""),
    }


def repair_imessage_reply_tag_patch(search_roots: tuple[Path, ...] | None = None) -> dict[str, Any]:
    """
    Патчит установленный OpenClaw transport для iMessage.

    Почему нужен патч:
    - iMessage sender может как сам дописывать `[[reply_to:*]]`, так и
      пропускать уже пришедший сверху reply-tag в пользовательский текст;
    - в приложении Messages этот служебный тег виден пользователю как текст.

    Патч intentionally узкий и воспроизводимый:
    - меняем только одну строку в `send-*.js`;
    - runtime_repair переиспользует этот шаг на каждом старте, поэтому
      обновление OpenClaw не оставит систему в поломанном состоянии.
    """
    roots: list[Path] = []
    if search_roots:
        roots.extend(search_roots)

    openclaw_bin = shutil.which("openclaw") or ""
    if openclaw_bin:
        bin_path = Path(openclaw_bin).resolve()
        roots.extend(
            [
                bin_path.parent.parent / "lib" / "node_modules" / "openclaw" / "dist",
                bin_path.parent.parent / "node_modules" / "openclaw" / "dist",
                bin_path.parent.parent / "lib" / "node_modules" / "openclaw" / "dist" / "plugin-sdk",
                bin_path.parent.parent / "node_modules" / "openclaw" / "dist" / "plugin-sdk",
            ]
        )

    roots.extend(
        [
            Path("/opt/homebrew/lib/node_modules/openclaw/dist"),
            Path("/opt/homebrew/lib/node_modules/openclaw/dist/plugin-sdk"),
            Path("/usr/local/lib/node_modules/openclaw/dist"),
            Path("/usr/local/lib/node_modules/openclaw/dist/plugin-sdk"),
        ]
    )

    candidates: list[Path] = []
    seen: set[str] = set()
    seen_candidates: set[str] = set()
    for root in roots:
        root_path = Path(root).expanduser()
        key = str(root_path)
        if key in seen:
            continue
        seen.add(key)
        if not root_path.exists():
            continue
        for candidate in sorted(root_path.glob("send-*.js")):
            candidate_key = str(candidate.resolve())
            if candidate_key not in seen_candidates:
                seen_candidates.add(candidate_key)
                candidates.append(candidate)
        plugin_sdk_root = root_path / "plugin-sdk"
        if plugin_sdk_root.exists():
            for candidate in sorted(plugin_sdk_root.glob("send-*.js")):
                candidate_key = str(candidate.resolve())
                if candidate_key not in seen_candidates:
                    seen_candidates.add(candidate_key)
                    candidates.append(candidate)

    if not candidates:
        return {
            "changed": False,
            "patched_paths": [],
            "reason": "send_bundle_not_found",
        }

    patched_paths: list[str] = []
    already_patched: list[str] = []
    searched_paths: list[str] = []
    function_pattern = re.compile(
        r"function\s+prependReplyTagIfNeeded\(message,\s*replyToId\)\s*\{[\s\S]*?\}\s*(?=function\s+resolveMessageId\()"
    )
    replacement_function = (
        "function prependReplyTagIfNeeded(message, replyToId) {\n"
        f"\t/* {IMESSAGE_REPLY_PATCH_MARKER}: отключаем reply-tag перед отправкой. */\n"
        "\treturn message;\n"
        "}\n"
    )
    legacy_init_pattern = re.compile(r'let\s+message\s*=\s*text\s*\?\?\s*"";')
    broken_strip_line_pattern = re.compile(
        rf'let\s+message\s*=\s*String\(message\s*\?\?\s*""\)\.replace\([^\n]*\);\s*/\*\s*{re.escape(IMESSAGE_REPLY_PATCH_MARKER)}:[\s\S]*?\*/'
    )
    legacy_noop_pattern = re.compile(
        rf'message\s*=\s*message;\s*/\*\s*{re.escape(IMESSAGE_REPLY_PATCH_MARKER)}:[\s\S]*?\*/'
    )
    prepend_call_pattern = re.compile(r'message\s*=\s*prependReplyTagIfNeeded\(message,\s*opts\.replyToId\);\s*')
    init_strip_line_replacement = (
        f'let message = {_imessage_reply_strip_expr("text")}; '
        f"/* {IMESSAGE_REPLY_PATCH_MARKER}: вырезаем reply-tag из готового текста перед отправкой. */"
    )
    final_strip_line_replacement = (
        f'message = {_imessage_reply_strip_expr("message")}; '
        f"/* {IMESSAGE_REPLY_PATCH_MARKER}: вырезаем reply-tag из готового текста перед отправкой. */"
    )
    final_strip_line_pattern = re.compile(
        rf'message\s*=\s*String\(message\s*\?\?\s*""\)\.replace\([^\n]*\);\s*/\*\s*{re.escape(IMESSAGE_REPLY_PATCH_MARKER)}:[\s\S]*?\*/'
    )

    for path in candidates:
        searched_paths.append(str(path))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "sendMessageIMessage" not in text or "prependReplyTagIfNeeded" not in text:
            continue
        patched_text = text
        function_replaced = 0
        function_match = function_pattern.search(patched_text)
        if function_match is None:
            continue
        function_source = function_match.group(0)
        function_needs_patch = "const replyTag" in function_source or IMESSAGE_REPLY_PATCH_MARKER not in function_source
        if function_needs_patch:
            patched_text, function_replaced = function_pattern.subn(replacement_function, patched_text, count=1)
            if function_replaced != 1:
                continue
        line_replaced = 0
        if legacy_init_pattern.search(patched_text):
            patched_text, init_replaced = legacy_init_pattern.subn(
                lambda _m: init_strip_line_replacement,
                patched_text,
                count=1,
            )
            line_replaced += init_replaced
        else:
            patched_text, init_replaced = broken_strip_line_pattern.subn(
                lambda _m: init_strip_line_replacement,
                patched_text,
                count=1,
            )
            line_replaced += init_replaced

        patched_text, broken_final_replaced = broken_strip_line_pattern.subn(
            lambda _m: final_strip_line_replacement,
            patched_text,
        )
        line_replaced += broken_final_replaced

        patched_text, noop_replaced = legacy_noop_pattern.subn(
            lambda _m: final_strip_line_replacement,
            patched_text,
        )
        line_replaced += noop_replaced

        patched_text, prepend_replaced = prepend_call_pattern.subn(
            lambda _m: final_strip_line_replacement,
            patched_text,
            count=1,
        )
        line_replaced += prepend_replaced

        if not final_strip_line_pattern.search(patched_text):
            params_pattern = re.compile(r'^(\s*const\s+params\s*=)', re.MULTILINE)
            patched_text, inserted_final = params_pattern.subn(
                lambda match: f"\t{final_strip_line_replacement}\n{match.group(1)}",
                patched_text,
                count=1,
            )
            line_replaced += inserted_final

        if function_replaced == 0 and line_replaced == 0:
            already_patched.append(str(path))
            continue
        if patched_text == text:
            continue
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_suffix(path.suffix + f".bak_{stamp}")
        shutil.copy2(path, backup_path)
        path.write_text(patched_text, encoding="utf-8")
        patched_paths.append(str(path))

    return {
        "changed": bool(patched_paths),
        "patched_paths": patched_paths,
        "already_patched": already_patched,
        "searched_paths": searched_paths,
    }


def _default_model_from_openclaw(openclaw_payload: dict[str, Any]) -> tuple[str, str]:
    """
    Возвращает (provider, model) дефолтного primary-маршрута.
    Если не удалось распарсить — безопасный fallback.
    """
    primary = (
        str(
            (
                openclaw_payload.get("agents", {})
                .get("defaults", {})
                .get("model", {})
                .get("primary", "")
            )
            or ""
        )
        .strip()
        .lower()
    )
    if "/" in primary:
        provider = primary.split("/", 1)[0]
        return provider, primary
    return "google", "google/gemini-2.5-flash"


def detect_active_channels(openclaw_payload: dict[str, Any]) -> tuple[str, ...]:
    """
    Возвращает список активных каналов из openclaw.json.

    Правило:
    - если в `channels.<name>.enabled` есть bool — берём только `True`;
    - если `enabled` отсутствует, считаем канал активным (legacy-конфиг).
    """
    channels_cfg = openclaw_payload.get("channels", {})
    if not isinstance(channels_cfg, dict):
        return DEFAULT_CHANNELS

    discovered: list[str] = []
    for channel_name, channel_cfg in channels_cfg.items():
        channel = str(channel_name or "").strip().lower()
        if not channel:
            continue
        if not isinstance(channel_cfg, dict):
            continue
        enabled_raw = channel_cfg.get("enabled")
        is_enabled = bool(enabled_raw) if isinstance(enabled_raw, bool) else True
        if is_enabled:
            discovered.append(channel)

    if not discovered:
        return DEFAULT_CHANNELS
    return tuple(dict.fromkeys(discovered))


def _filter_telegram_sender_ids(values: list[str]) -> list[str]:
    """
    Оставляет только корректные Telegram sender IDs для groupAllowFrom.

    Почему отдельный helper:
    - `allowFrom` у Telegram может держать и numeric IDs, и usernames;
    - `groupAllowFrom` для reserve-safe должен содержать только sender IDs,
      иначе OpenClaw позже честно ругнётся на невалидные allowFrom entries;
    - отрицательные chat/group IDs сюда тоже попадать не должны.
    """
    filtered: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = str(raw or "").strip()
        if not item or item in {"*", "+"}:
            continue
        if not item.isdigit():
            continue
        if item in seen:
            continue
        seen.add(item)
        filtered.append(item)
    return filtered


def should_restart_gateway(report_steps: dict[str, Any]) -> bool:
    """
    Определяет, нужен ли перезапуск живого OpenClaw gateway после repair.

    Почему это важно:
    - часть runtime-состояния (sessions / model routing) живёт в памяти gateway;
    - если мы уже починили файлы на диске, но gateway не перезапущен,
      он может записать старое in-memory состояние обратно и откатить fix.
    """
    if not isinstance(report_steps, dict):
        return False

    restart_sensitive_steps = (
        "sync_managed_output_sanitizer_plugin",
        "sync_models_json",
        "sync_openclaw_json",
        "sync_auth_profiles_json",
        "sync_plugin_allowlist",
        "repair_output_sanitizer_plugin_config",
        "sync_telegram_channel_token",
        "bootstrap_missing_channels",
        "repair_lmstudio_catalog_models_json",
        "repair_lmstudio_catalog_openclaw_json",
        "repair_external_reasoning",
        "repair_main_agent_profile",
        "repair_imessage_reply_tag_patch",
        "repair_cron_jobs",
        "repair_compaction_memory_flush",
        "repair_agent_models",
        "repair_reply_to_modes",
        "repair_sessions",
        "dm_policy",
        "group_policy",
        "repair_group_policy",
    )
    for step_name in restart_sensitive_steps:
        step_payload = report_steps.get(step_name)
        if isinstance(step_payload, dict) and bool(step_payload.get("changed", False)):
            return True
    return False


def repair_cron_jobs(cron_jobs_path: Path) -> dict[str, Any]:
    """
    Отключает заведомо сломанные cron jobs без корректной delivery-цели.

    Почему это нужно:
    - scheduler мог остаться `enabled`, но конкретная задача уже гарантированно
      не может доставить сообщение в канал;
    - после этого assistant начинал уверенно говорить "cron работает", хотя
      в runtime уже зафиксирован `lastError` про отсутствующий target.
    - отдельный проблемный класс: `systemEvent`-задачи без delivery-цели,
      которые формально `enabled`, но ни разу не были реально доставлены.
    """
    payload = _read_json(cron_jobs_path)
    if not isinstance(payload, dict):
        return {"path": str(cron_jobs_path), "changed": False, "reason": "not_dict"}

    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return {"path": str(cron_jobs_path), "changed": False, "reason": "jobs_missing"}

    disabled_jobs: list[str] = []
    disabled_orphan_jobs: list[str] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        state = job.get("state") if isinstance(job.get("state"), dict) else {}
        target = job.get("target")
        delivery = job.get("delivery")
        delivery_target = ""
        delivery_mode = ""
        if isinstance(delivery, dict):
            delivery_target = str(delivery.get("target") or "").strip()
            delivery_mode = str(delivery.get("mode") or "").strip().lower()

        has_target = bool(str(target or "").strip() or delivery_target)

        if delivery_mode == "announce":
            last_error = str(state.get("lastError") or "").strip().lower()
            if not any(marker in last_error for marker in BROKEN_CRON_DELIVERY_ERROR_MARKERS):
                continue
            if has_target:
                continue
            if job.get("enabled") is not False:
                job["enabled"] = False
                disabled_jobs.append(str(job.get("id") or "").strip() or "<unknown>")
            continue

        payload_cfg = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        payload_kind = str(payload_cfg.get("kind") or "").strip()
        last_status = str(state.get("lastDeliveryStatus") or "").strip().lower()
        if payload_kind == "systemEvent" and not has_target and last_status == "not-requested":
            if job.get("enabled") is not False:
                job["enabled"] = False
                disabled_orphan_jobs.append(str(job.get("id") or "").strip() or "<unknown>")

    if disabled_jobs or disabled_orphan_jobs:
        _write_json(cron_jobs_path, payload)

    return {
        "path": str(cron_jobs_path),
        "changed": bool(disabled_jobs or disabled_orphan_jobs),
        "disabled_invalid_announce_jobs": len(disabled_jobs),
        "disabled_job_ids": disabled_jobs,
        "disabled_orphan_system_jobs": len(disabled_orphan_jobs),
        "disabled_orphan_job_ids": disabled_orphan_jobs,
    }


def repair_sessions(
    sessions_path: Path,
    *,
    channels: tuple[str, ...],
    default_provider: str,
    default_model: str,
) -> dict[str, Any]:
    """
    Убирает опасные local-overrides из runtime sessions.
    Также снимает залипший generic `local` (без explicit модели), чтобы
    runtime мог вернуться к primary/fallback цепочке.
    Дополнительно выравнивает pinned-модели под целевую default_model
    для каналов и transport-сессий OpenClaw (`agent:main:openai:*`, `agent:main:main`),
    чтобы runtime не держал старые модели после смены primary.

    Важный кейс:
    - профиль уже local-first, но старые direct session по Telegram/iMessage
      продолжают быть закреплены на `google/gemini-*`;
    - внешне канал "жив", но отвечает из облака и обходит локальный Nemotron.
    Если primary у агента локальный, такие stale pinned-сессии нужно вернуть
    к текущей локальной primary-модели.
    """
    payload = _read_json(sessions_path)
    if not isinstance(payload, dict):
        return {"path": str(sessions_path), "changed": False, "fixed_entries": 0, "reason": "not_dict"}

    fixed_entries = 0
    cleared_overrides = 0
    replaced_generic_local = 0
    replaced_channel_local_model = 0
    replaced_channel_pinned_model = 0
    reset_polluted_direct_sessions = 0
    reset_missing_session_files = 0
    reset_auth_fallback_sessions = 0
    reset_broken_transport_aliases = 0
    reset_legacy_bootstrap_sessions = 0
    reset_legacy_bootstrap_transcript_sessions = 0
    reset_legacy_workspace_sessions = 0
    archived_session_files: list[str] = []
    scanned_entries = 0
    default_provider_norm = str(default_provider or "").strip().lower()
    default_model_raw = str(default_model or "").strip()
    # В sessions.json local provider обычно хранит bare model id без `lmstudio/`.
    session_default_model = default_model_raw
    if default_provider_norm in {"lmstudio", "local"} and "/" in default_model_raw:
        head, tail = default_model_raw.split("/", 1)
        if head.strip().lower() in {"lmstudio", "local"} and tail.strip():
            session_default_model = tail.strip()

    for key, meta in list(payload.items()):
        if key.startswith("telegram:slash:"):
            if not isinstance(meta, dict):
                continue
            session_id = str(meta.get("sessionId") or "").strip()
            if not session_id:
                continue
            backing_key = f"agent:main:openai:{session_id}"
            if backing_key not in payload:
                payload.pop(key, None)
                reset_broken_transport_aliases += 1
                fixed_entries += 1

    for key, meta in list(payload.items()):
        if not isinstance(meta, dict):
            continue
        if not key.startswith("agent:main:"):
            continue
        scanned_entries += 1
        matched_scope = None
        for channel in channels:
            token = f"agent:main:{channel}:"
            if key.startswith(token):
                matched_scope = channel
                break
        if matched_scope is None and key.startswith("agent:main:openai:"):
            matched_scope = "openai"
        if matched_scope is None and key.startswith("agent:main:cron:"):
            matched_scope = "cron"
        if matched_scope is None and key == "agent:main:main":
            matched_scope = "main"

        session_id = str(meta.get("sessionId") or "").strip()
        session_file = _resolve_session_file_path(
            sessions_path,
            str(meta.get("sessionFile", "") or ""),
            session_id,
        )

        if matched_scope and (":direct:" in key or matched_scope in {"openai", "main", "cron"}):
            if session_file and not session_file.exists():
                removed_aliases = _remove_transport_aliases_for_session_id(payload, session_id)
                payload.pop(key, None)
                reset_missing_session_files += 1
                fixed_entries += 1 + len(removed_aliases)
                reset_broken_transport_aliases += len(removed_aliases)
                continue
            if session_file and _session_file_looks_polluted(session_file):
                archived_path = _archive_session_file(session_file, reason="polluted")
                removed_aliases = _remove_transport_aliases_for_session_id(payload, session_id)
                payload.pop(key, None)
                reset_polluted_direct_sessions += 1
                fixed_entries += 1 + len(removed_aliases)
                reset_broken_transport_aliases += len(removed_aliases)
                archived_session_files.append(archived_path)
                continue
            if session_file and _session_file_looks_auth_fallback(session_file):
                archived_path = _archive_session_file(session_file, reason="authfallback")
                removed_aliases = _remove_transport_aliases_for_session_id(payload, session_id)
                payload.pop(key, None)
                reset_auth_fallback_sessions += 1
                fixed_entries += 1 + len(removed_aliases)
                reset_broken_transport_aliases += len(removed_aliases)
                archived_session_files.append(archived_path)
                continue
            if key.startswith("agent:main:openai:") and session_file and _session_file_looks_like_legacy_owner_bootstrap(session_file):
                archived_path = _archive_session_file(session_file, reason="legacybootstrap")
                removed_aliases = _remove_transport_aliases_for_session_id(payload, session_id)
                payload.pop(key, None)
                reset_legacy_bootstrap_transcript_sessions += 1
                fixed_entries += 1 + len(removed_aliases)
                reset_broken_transport_aliases += len(removed_aliases)
                archived_session_files.append(archived_path)
                continue
            if session_file and _session_file_uses_legacy_workspace(session_file):
                archived_path = _archive_session_file(session_file, reason="legacyworkspace")
                removed_aliases = _remove_transport_aliases_for_session_id(payload, session_id)
                payload.pop(key, None)
                reset_legacy_workspace_sessions += 1
                fixed_entries += 1 + len(removed_aliases)
                reset_broken_transport_aliases += len(removed_aliases)
                archived_session_files.append(archived_path)
                continue
        elif matched_scope:
            if session_file and not session_file.exists():
                payload.pop(key, None)
                reset_missing_session_files += 1
                fixed_entries += 1
                continue

        changed = False

        if matched_scope and _session_looks_like_legacy_owner_bootstrap(meta):
            removed_legacy_keys = 0
            for field_name in LEGACY_SESSION_RESET_KEYS:
                if field_name in meta:
                    meta.pop(field_name, None)
                    removed_legacy_keys += 1
            if meta.get("systemSent") is not False:
                meta["systemSent"] = False
                removed_legacy_keys += 1
            if removed_legacy_keys > 0:
                reset_legacy_bootstrap_sessions += 1
                changed = True

        model_override = str(meta.get("modelOverride", "") or "").strip().lower()
        provider_override = str(meta.get("providerOverride", "") or "").strip().lower()
        if matched_scope and ("modelOverride" in meta or "providerOverride" in meta):
            # Для внешних/transport-сессий stale override опасен независимо от
            # того, указывает ли он на local или cloud: именно он ломает
            # предсказуемость force-cloud/local-first и уводит канал в старый pin.
            if "modelOverride" in meta:
                meta.pop("modelOverride", None)
                changed = True
            if "providerOverride" in meta:
                meta.pop("providerOverride", None)
                changed = True
            cleared_overrides += 1

        model_raw = str(meta.get("model", "") or "").strip()
        model = model_raw.lower()
        provider = str(meta.get("modelProvider", "") or "").strip().lower()
        if model in {"local", "lmstudio/local", "google/local"} and provider in {"", "lmstudio", "local"}:
            # Generic local без explicit model-id часто приводит к `No models loaded`.
            meta["modelProvider"] = default_provider
            meta["model"] = session_default_model
            replaced_generic_local += 1
            changed = True

        normalized_model_raw = model_raw
        if "/" in normalized_model_raw:
            head, tail = normalized_model_raw.split("/", 1)
            if head.strip().lower() in {"lmstudio", "local"} and tail.strip():
                normalized_model_raw = tail.strip()

        if matched_scope and provider in {"lmstudio", "local"} and model_raw:
            if normalized_model_raw == session_default_model:
                if changed:
                    fixed_entries += 1
                continue
            # Канальные сессии не должны удерживать старую локальную модель:
            # иначе OpenClaw продолжает догружать её даже после смены primary.
            # То же относится к transport-сессиям openai/main.
            meta["modelProvider"] = default_provider
            meta["model"] = session_default_model
            replaced_channel_local_model += 1
            changed = True

        # Когда профиль local-first уже активен, stale cloud pin на direct-канале
        # тоже надо снять. Иначе OpenClaw продолжает отвечать из Gemini,
        # хотя primary/fallback уже переключены на локальный Nemotron.
        if (
            matched_scope
            and default_provider_norm in {"lmstudio", "local"}
            and (provider != default_provider_norm or normalized_model_raw != session_default_model)
        ):
            meta["modelProvider"] = default_provider
            meta["model"] = session_default_model
            replaced_channel_pinned_model += 1
            changed = True

        if changed:
            fixed_entries += 1

    if fixed_entries > 0:
        _write_json(sessions_path, payload)

    orphan_cleanup = _archive_orphaned_problem_session_files(sessions_path, payload)
    if orphan_cleanup["archived_files"] and fixed_entries == 0:
        # Даже если live index не менялся, архивирование сирот — это осмысленное
        # изменение результата repair, поэтому наружу помечаем шаг как changed.
        fixed_entries = 0

    return {
        "path": str(sessions_path),
        "changed": fixed_entries > 0 or bool(orphan_cleanup["archived_files"]),
        "scanned_entries": scanned_entries,
        "fixed_entries": fixed_entries,
        "cleared_overrides": cleared_overrides,
        "replaced_generic_local": replaced_generic_local,
        "replaced_channel_local_model": replaced_channel_local_model,
        "replaced_channel_pinned_model": replaced_channel_pinned_model,
        "reset_polluted_direct_sessions": reset_polluted_direct_sessions,
        "reset_missing_session_files": reset_missing_session_files,
        "reset_auth_fallback_sessions": reset_auth_fallback_sessions,
        "reset_broken_transport_aliases": reset_broken_transport_aliases,
        "reset_legacy_bootstrap_sessions": reset_legacy_bootstrap_sessions,
        "reset_legacy_bootstrap_transcript_sessions": reset_legacy_bootstrap_transcript_sessions,
        "reset_legacy_workspace_sessions": reset_legacy_workspace_sessions,
        "reset_polluted_orphan_session_files": orphan_cleanup["reset_polluted_orphan_session_files"],
        "reset_auth_fallback_orphan_session_files": orphan_cleanup["reset_auth_fallback_orphan_session_files"],
        "reset_legacy_bootstrap_orphan_session_files": orphan_cleanup["reset_legacy_bootstrap_orphan_session_files"],
        "reset_legacy_workspace_orphan_session_files": orphan_cleanup["reset_legacy_workspace_orphan_session_files"],
        "archived_session_files": archived_session_files + orphan_cleanup["archived_files"],
        "session_default_model": session_default_model,
    }


def repair_agent_model_overrides(openclaw_path: Path, *, default_model: str) -> dict[str, Any]:
    """
    Снимает залипшие global-agent модели `lmstudio/local` в openclaw.json.

    Почему важно:
    - even при рабочих каналах embedded agent может стартовать через `agents.list[].model`;
    - generic local-модель без загруженного инстанса даёт `400 No models loaded`.
    """
    payload = _read_json(openclaw_path)
    if not isinstance(payload, dict):
        return {"path": str(openclaw_path), "changed": False, "fixed": 0, "reason": "not_dict"}

    agents = payload.get("agents")
    if not isinstance(agents, dict):
        return {"path": str(openclaw_path), "changed": False, "fixed": 0, "reason": "agents_missing"}

    defaults = agents.get("defaults")
    changed_entries: list[str] = []
    fixed = 0

    if isinstance(defaults, dict):
        subagents = defaults.get("subagents")
        if isinstance(subagents, dict):
            sub_model = str(subagents.get("model") or "").strip()
            if _is_local_marker(sub_model) and sub_model != default_model:
                subagents["model"] = default_model
                fixed += 1
                changed_entries.append("agents.defaults.subagents.model")

    agents_list = agents.get("list")
    if isinstance(agents_list, list):
        for idx, item in enumerate(agents_list):
            if not isinstance(item, dict):
                continue
            model_value = str(item.get("model") or "").strip()
            if not _is_local_marker(model_value):
                continue
            if model_value == default_model:
                continue
            item["model"] = default_model
            fixed += 1
            agent_id = str(item.get("id") or f"#{idx}")
            changed_entries.append(f"agents.list[{agent_id}].model")

    if fixed > 0:
        _write_json(openclaw_path, payload)

    return {
        "path": str(openclaw_path),
        "changed": fixed > 0,
        "fixed": fixed,
        "entries": changed_entries,
        "default_model": default_model,
    }


def normalize_allowlist(path: Path) -> dict[str, Any]:
    """
    Нормализует allowlist:
    - удаляет одиночные широкие wildcard-псевдоэлементы (`+`, `*`);
    - дубли;
    - пустые значения.
    """
    payload = _read_json(path)
    if not isinstance(payload, list):
        return {"path": str(path), "changed": False, "items": 0}

    before = [str(item or "").strip() for item in payload]
    after: list[str] = []
    seen: set[str] = set()
    for item in before:
        if not item:
            continue
        if item in {"+", "*"}:
            continue
        if item in seen:
            continue
        seen.add(item)
        after.append(item)

    changed = after != before
    if changed:
        _write_json(path, after)

    return {
        "path": str(path),
        "changed": changed,
        "items_before": len(before),
        "items_after": len(after),
    }


def apply_dm_policy(openclaw_path: Path, channels: tuple[str, ...], policy: str) -> dict[str, Any]:
    """
    Переводит dmPolicy каналов в выбранный режим (allowlist/open/pairing).
    Используется для отключения pairing-шума или открытия входящих DMs.
    """
    payload = _read_json(openclaw_path)
    channel_cfg = payload.setdefault("channels", {})
    changed_channels: list[str] = []
    allow_from_fixed: dict[str, str] = {}
    creds_root = openclaw_path.parent / "credentials"
    allow_from_changes = 0
    plugins = payload.get("plugins") if isinstance(payload.get("plugins"), dict) else {}
    plugin_entries = plugins.get("entries") if isinstance(plugins.get("entries"), dict) else {}
    sanitizer = (
        plugin_entries.get("krab-output-sanitizer")
        if isinstance(plugin_entries.get("krab-output-sanitizer"), dict)
        else {}
    )
    sanitizer_config = (
        sanitizer.get("config") if isinstance(sanitizer.get("config"), dict) else {}
    )
    trusted_peers = (
        sanitizer_config.get("trustedPeers")
        if isinstance(sanitizer_config.get("trustedPeers"), dict)
        else {}
    )

    for channel in channels:
        cfg = channel_cfg.get(channel)
        if not isinstance(cfg, dict):
            continue
        current = str(cfg.get("dmPolicy", "") or "").strip().lower()
        if "dmPolicy" not in cfg:
            continue

        allow_from = cfg.get("allowFrom")
        allow_from_list = allow_from if isinstance(allow_from, list) else []
        normalized_allow_from = [
            str(item).strip()
            for item in allow_from_list
            if str(item).strip() and str(item).strip() not in {"*", "+"}
        ]
        if channel == "telegram":
            normalized_allow_from = _filter_telegram_sender_ids(normalized_allow_from)

        if policy == "open":
            # Для open OpenClaw требует wildcard в allowFrom.
            changed = False
            if current != policy:
                cfg["dmPolicy"] = policy
                changed = True
            if "*" not in allow_from_list:
                cfg["allowFrom"] = ["*"]
                allow_from_fixed[channel] = "set_wildcard"
                allow_from_changes += 1
                changed = True
            if changed:
                changed_channels.append(channel)
        elif policy == "allowlist":
            # Для allowlist wildcard нельзя сохранять: иначе канал останется фактически открытым.
            desired_allow_from = list(normalized_allow_from)
            source = ""
            if not desired_allow_from:
                channel_allowlist_file = creds_root / f"{channel}-allowFrom.json"
                loaded: list[str] = []
                if channel_allowlist_file.exists():
                    try:
                        raw = json.loads(channel_allowlist_file.read_text(encoding="utf-8"))
                        if isinstance(raw, list):
                            loaded = [
                                str(x).strip()
                                for x in raw
                                if str(x).strip() and str(x).strip() not in {"*", "+"}
                            ]
                            if channel == "telegram":
                                loaded = _filter_telegram_sender_ids(loaded)
                    except (OSError, ValueError):
                        loaded = []
                if loaded:
                    desired_allow_from = loaded
                    source = "loaded_from_credentials"
                else:
                    trusted = trusted_peers.get(channel)
                    if isinstance(trusted, list):
                        derived = [
                            str(x).strip()
                            for x in trusted
                            if str(x).strip() and str(x).strip() not in {"*", "+"}
                        ]
                        if channel == "telegram":
                            derived = _filter_telegram_sender_ids(derived)
                        if derived:
                            desired_allow_from = derived
                            source = "derived_from_trusted_peers"
            changed = False
            if current != policy and not desired_allow_from:
                continue
            if current != policy:
                cfg["dmPolicy"] = policy
                changed = True
            if desired_allow_from and desired_allow_from != allow_from_list:
                cfg["allowFrom"] = desired_allow_from
                allow_from_fixed[channel] = source or "sanitized_inline_allowlist"
                allow_from_changes += 1
                changed = True
            if changed:
                changed_channels.append(channel)

    if changed_channels or allow_from_changes > 0:
        _write_json(openclaw_path, payload)

    return {
        "path": str(openclaw_path),
        "changed": bool(changed_channels or allow_from_changes > 0),
        "channels": changed_channels,
        "allow_from_fixed": allow_from_fixed,
        "allow_from_changes": allow_from_changes,
    }


def apply_group_policy(openclaw_path: Path, channels: tuple[str, ...], policy: str) -> dict[str, Any]:
    """
    Явно выставляет groupPolicy для каналов.

    Для `allowlist` пытается собрать `groupAllowFrom` из live-конфига:
    1) уже существующий inline `groupAllowFrom`;
    2) sender-id allowlist из `allowFrom`;
    3) credentials `<channel>-groupAllowFrom.json`;
    4) trusted peers из `krab-output-sanitizer`.

    Почему именно так:
    - `channels.<name>.groups` хранит chat/group IDs, а не sender IDs;
    - Telegram doctor ожидает в `groupAllowFrom` именно numeric sender IDs;
    - раньше мы ошибочно прокидывали туда group ID, из-за чего gateway
      честно ругался `Invalid allowFrom entry`.
    """
    payload = _read_json(openclaw_path)
    channel_cfg = payload.setdefault("channels", {})
    changed_channels: list[str] = []
    group_allow_from_fixed: dict[str, str] = {}
    creds_root = openclaw_path.parent / "credentials"
    plugins = payload.get("plugins") if isinstance(payload.get("plugins"), dict) else {}
    plugin_entries = plugins.get("entries") if isinstance(plugins.get("entries"), dict) else {}
    sanitizer = (
        plugin_entries.get("krab-output-sanitizer")
        if isinstance(plugin_entries.get("krab-output-sanitizer"), dict)
        else {}
    )
    sanitizer_config = (
        sanitizer.get("config") if isinstance(sanitizer.get("config"), dict) else {}
    )
    trusted_peers = (
        sanitizer_config.get("trustedPeers")
        if isinstance(sanitizer_config.get("trustedPeers"), dict)
        else {}
    )

    for channel in channels:
        cfg = channel_cfg.get(channel)
        if not isinstance(cfg, dict):
            continue

        current_policy = str(cfg.get("groupPolicy", "") or "").strip().lower()
        groups_cfg = cfg.get("groups") if isinstance(cfg.get("groups"), dict) else {}
        allow_from = cfg.get("allowFrom")
        allow_from_list = allow_from if isinstance(allow_from, list) else []
        group_allow_from = cfg.get("groupAllowFrom")
        group_allow_from_list = group_allow_from if isinstance(group_allow_from, list) else []
        normalized_group_allow_from = [
            str(item).strip()
            for item in group_allow_from_list
            if str(item).strip() and str(item).strip() not in {"*", "+"}
        ]
        if channel == "telegram":
            enabled_group_ids = {
                str(group_id).strip()
                for group_id, group_meta in groups_cfg.items()
                if str(group_id).strip()
                and (not isinstance(group_meta, dict) or bool(group_meta.get("enabled", True)))
            }
            # В Telegram `groupAllowFrom` — это только numeric sender IDs.
            # Usernames и group/chat IDs здесь недопустимы.
            normalized_group_allow_from = _filter_telegram_sender_ids(normalized_group_allow_from)
            normalized_group_allow_from = [
                item for item in normalized_group_allow_from if item not in enabled_group_ids
            ]

        desired_group_allow_from = list(normalized_group_allow_from)
        source = ""
        if policy == "allowlist" and not desired_group_allow_from:
            derived_from_dm_allowlist = [
                str(item).strip()
                for item in allow_from_list
                if str(item).strip() and str(item).strip() not in {"*", "+"}
            ]
            if channel == "telegram":
                derived_from_dm_allowlist = _filter_telegram_sender_ids(derived_from_dm_allowlist)
            if derived_from_dm_allowlist:
                desired_group_allow_from = derived_from_dm_allowlist
                source = "derived_from_dm_allowlist"
            if not desired_group_allow_from:
                group_allowlist_file = creds_root / f"{channel}-groupAllowFrom.json"
                if group_allowlist_file.exists():
                    try:
                        raw = json.loads(group_allowlist_file.read_text(encoding="utf-8"))
                        if isinstance(raw, list):
                            desired_group_allow_from = [
                                str(x).strip()
                                for x in raw
                                if str(x).strip() and str(x).strip() not in {"*", "+"}
                            ]
                            if channel == "telegram":
                                desired_group_allow_from = _filter_telegram_sender_ids(desired_group_allow_from)
                            if desired_group_allow_from:
                                source = "loaded_from_credentials"
                    except (OSError, ValueError):
                        desired_group_allow_from = []
            if not desired_group_allow_from:
                trusted = trusted_peers.get(channel)
                if isinstance(trusted, list):
                    desired_group_allow_from = [
                        str(x).strip()
                        for x in trusted
                        if str(x).strip() and str(x).strip() not in {"*", "+"}
                    ]
                    if channel == "telegram":
                        desired_group_allow_from = _filter_telegram_sender_ids(desired_group_allow_from)
                    if desired_group_allow_from:
                        source = "derived_from_trusted_peers"

        changed = False
        if current_policy != policy:
            # Не включаем allowlist без реального списка, иначе transport просто замолчит.
            if policy != "allowlist" or desired_group_allow_from:
                cfg["groupPolicy"] = policy
                changed = True

        if policy == "allowlist" and desired_group_allow_from and desired_group_allow_from != group_allow_from_list:
            cfg["groupAllowFrom"] = desired_group_allow_from
            group_allow_from_fixed[channel] = source or "sanitized_inline_group_allowlist"
            changed = True

        if policy == "open" and "groupAllowFrom" in cfg and group_allow_from_list:
            cfg.pop("groupAllowFrom", None)
            group_allow_from_fixed[channel] = "removed_for_open_policy"
            changed = True

        if changed:
            changed_channels.append(channel)

    if changed_channels:
        _write_json(openclaw_path, payload)

    return {
        "path": str(openclaw_path),
        "changed": bool(changed_channels),
        "channels": changed_channels,
        "group_allow_from_fixed": group_allow_from_fixed,
    }


def repair_group_policy_allowlist(openclaw_path: Path, channels: tuple[str, ...]) -> dict[str, Any]:
    """
    Убирает сломанное состояние groupPolicy=allowlist без groupAllowFrom.

    Почему:
    - OpenClaw doctor в таком кейсе предупреждает, что group-сообщения будут
      молча дропаться;
    - канал не использует fallback на allowFrom для групп;
    - безопасный дефолт для broken-конфига: `groupPolicy=open`.
    """
    payload = _read_json(openclaw_path)
    channel_cfg = payload.setdefault("channels", {})

    fixed_channels: list[str] = []
    for channel in channels:
        cfg = channel_cfg.get(channel)
        if not isinstance(cfg, dict):
            continue
        current_policy = str(cfg.get("groupPolicy", "") or "").strip().lower()
        groups_cfg = cfg.get("groups") if isinstance(cfg.get("groups"), dict) else {}
        group_allow_from = cfg.get("groupAllowFrom")
        group_allow_from_list = group_allow_from if isinstance(group_allow_from, list) else []
        is_invalid_telegram_sender_allowlist = False
        if channel == "telegram" and group_allow_from_list:
            enabled_group_ids = {
                str(group_id).strip()
                for group_id, group_meta in groups_cfg.items()
                if str(group_id).strip()
                and (not isinstance(group_meta, dict) or bool(group_meta.get("enabled", True)))
            }
            is_invalid_telegram_sender_allowlist = not any(
                str(item).strip()
                and not str(item).strip().startswith("-")
                and str(item).strip() not in enabled_group_ids
                for item in group_allow_from_list
            )

        if current_policy == "allowlist" and (not group_allow_from_list or is_invalid_telegram_sender_allowlist):
            cfg["groupPolicy"] = "open"
            cfg.pop("groupAllowFrom", None)
            fixed_channels.append(channel)

    if fixed_channels:
        _write_json(openclaw_path, payload)

    return {
        "path": str(openclaw_path),
        "changed": bool(fixed_channels),
        "channels": fixed_channels,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Восстановление runtime-конфига OpenClaw/каналов после рефакторинга."
    )
    parser.add_argument(
        "--tier",
        choices=("auto", "free", "paid"),
        default="auto",
        help="Какой Gemini key синхронизировать в runtime.",
    )
    parser.add_argument(
        "--channels",
        default="",
        help="Список каналов для очистки сессий (через запятую). Пусто = авто из openclaw.json.",
    )
    parser.add_argument(
        "--dm-policy",
        choices=("keep", "pairing", "allowlist", "open"),
        default="open",
        help="Режим dmPolicy для каналов. keep = не менять.",
    )
    parser.add_argument(
        "--reply-to-mode",
        choices=("keep", "off"),
        default="off",
        help="Какой replyToMode выставить каналам. keep = не менять.",
    )
    parser.add_argument(
        "--group-policy",
        choices=("keep", "allowlist", "open"),
        default="keep",
        help="Какой groupPolicy выставить каналам. keep = не менять.",
    )
    parser.add_argument(
        "--skip-allowlist-normalize",
        action="store_true",
        help="Не нормализовать файлы allowlist.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv()

    openclaw_root = Path.home() / ".openclaw"
    models_path = openclaw_root / "agents" / "main" / "agent" / "models.json"
    auth_profiles_path = openclaw_root / "agents" / "main" / "agent" / "auth-profiles.json"
    openclaw_path = openclaw_root / "openclaw.json"
    sessions_path = openclaw_root / "agents" / "main" / "sessions" / "sessions.json"
    cron_jobs_path = openclaw_root / "cron" / "jobs.json"

    free_key = str(os.getenv("GEMINI_API_KEY_FREE", "") or "").strip()
    paid_key = str(os.getenv("GEMINI_API_KEY_PAID", "") or "").strip()
    lmstudio_token = choose_lmstudio_token(
        primary_token=os.getenv("LM_STUDIO_API_KEY", ""),
        legacy_token=os.getenv("LM_STUDIO_AUTH_TOKEN", ""),
    )

    selected_tier, target_key = choose_target_key(
        free_key=free_key,
        paid_key=paid_key,
        tier=args.tier,
    )
    if not target_key:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "no_valid_aistudio_key",
                    "selected_tier": args.tier,
                    "free_key_masked": mask_secret(free_key),
                    "paid_key_masked": mask_secret(paid_key),
                    "hint": "Ожидаются ключи AI Studio формата AIza...",
                },
                ensure_ascii=False,
            )
        )
        return 1

    if not openclaw_path.exists():
        print(json.dumps({"ok": False, "error": "openclaw_json_not_found", "path": str(openclaw_path)}, ensure_ascii=False))
        return 1

    if not models_path.exists():
        print(json.dumps({"ok": False, "error": "models_json_not_found", "path": str(models_path)}, ensure_ascii=False))
        return 1

    openclaw_payload = _read_json(openclaw_path)
    default_provider, default_model = _default_model_from_openclaw(openclaw_payload)
    channels = tuple(item.strip().lower() for item in str(args.channels).split(",") if item.strip())
    if not channels:
        channels = detect_active_channels(openclaw_payload)

    report: dict[str, Any] = {
        "ok": True,
        "selected_tier": selected_tier,
        "target_key_masked": mask_secret(target_key),
        "channels": list(channels),
        "steps": {},
    }

    report["steps"]["sync_models_json"] = sync_models_json(models_path, target_key, lmstudio_token)
    report["steps"]["sync_openclaw_json"] = sync_openclaw_json(openclaw_path, target_key, lmstudio_token)
    report["steps"]["sync_auth_profiles_json"] = sync_auth_profiles_json(
        auth_profiles_path,
        target_key,
        lmstudio_token,
    )
    report["steps"]["sync_plugin_allowlist"] = sync_plugin_allowlist(openclaw_path)
    report["steps"]["sync_telegram_channel_token"] = sync_telegram_channel_token(openclaw_path)
    report["steps"]["sync_managed_output_sanitizer_plugin"] = sync_managed_output_sanitizer_plugin(
        openclaw_root=openclaw_root,
    )
    report["steps"]["repair_output_sanitizer_plugin_config"] = repair_output_sanitizer_plugin_config(
        openclaw_path,
    )
    report["steps"]["repair_external_reasoning"] = repair_external_reasoning_defaults(
        openclaw_path,
        primary_model=default_model,
    )
    report["steps"]["repair_main_agent_profile"] = repair_main_agent_messaging_profile(openclaw_path)
    report["steps"]["repair_hooks"] = repair_hooks_config(openclaw_path)
    report["steps"]["repair_channel_health_monitor"] = repair_channel_health_monitor(openclaw_path)
    report["steps"]["repair_compaction_memory_flush"] = repair_compaction_memory_flush(openclaw_path)
    report["steps"]["repair_agent_models"] = repair_agent_model_overrides(
        openclaw_path,
        default_model=default_model,
    )
    report["steps"]["bootstrap_missing_channels"] = bootstrap_missing_channels(openclaw_path, channels)
    report["steps"]["repair_group_policy"] = repair_group_policy_allowlist(openclaw_path, channels)
    preferred_vision_model = str(os.getenv("LOCAL_PREFERRED_VISION_MODEL", "auto") or "").strip()
    report["steps"]["repair_lmstudio_catalog_models_json"] = repair_lmstudio_provider_catalog(
        models_path,
        primary_model=default_model,
        preferred_text_model=str(os.getenv("LOCAL_PREFERRED_MODEL", "") or "").strip(),
        preferred_vision_model=preferred_vision_model,
        lmstudio_token=lmstudio_token,
    )
    report["steps"]["repair_lmstudio_catalog_openclaw_json"] = repair_lmstudio_provider_catalog(
        openclaw_path,
        primary_model=default_model,
        preferred_text_model=str(os.getenv("LOCAL_PREFERRED_MODEL", "") or "").strip(),
        preferred_vision_model=preferred_vision_model,
        lmstudio_token=lmstudio_token,
    )
    report["steps"]["repair_sessions"] = repair_sessions(
        sessions_path,
        channels=channels,
        default_provider=default_provider,
        default_model=default_model,
    )
    report["steps"]["repair_cron_jobs"] = repair_cron_jobs(cron_jobs_path)

    reply_to_mode = str(args.reply_to_mode or "keep").strip().lower()
    if reply_to_mode != "keep":
        report["steps"]["repair_reply_to_modes"] = repair_reply_to_modes(
            openclaw_path,
            channels,
            reply_to_mode,
        )
    else:
        report["steps"]["repair_reply_to_modes"] = {"skipped": True}

    dm_policy = str(args.dm_policy or "keep").strip().lower()
    if dm_policy != "keep":
        report["steps"]["dm_policy"] = apply_dm_policy(openclaw_path, channels, dm_policy)
    else:
        report["steps"]["dm_policy"] = {"skipped": True}

    group_policy = str(args.group_policy or "keep").strip().lower()
    if group_policy != "keep":
        report["steps"]["group_policy"] = apply_group_policy(openclaw_path, channels, group_policy)
        report["steps"]["repair_group_policy"] = repair_group_policy_allowlist(openclaw_path, channels)
    else:
        report["steps"]["group_policy"] = {"skipped": True}

    if not args.skip_allowlist_normalize:
        allowlist_steps: dict[str, Any] = {}
        for channel in channels:
            allowlist_path = openclaw_root / "credentials" / f"{channel}-allowFrom.json"
            if allowlist_path.exists():
                allowlist_steps[channel] = normalize_allowlist(allowlist_path)
        report["steps"]["normalize_allowlists"] = allowlist_steps
    else:
        report["steps"]["normalize_allowlists"] = {"skipped": True}

    report["steps"]["repair_imessage_reply_tag_patch"] = repair_imessage_reply_tag_patch()

    report["gateway_restart_recommended"] = should_restart_gateway(report["steps"])

    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
