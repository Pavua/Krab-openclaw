# -*- coding: utf-8 -*-
"""
state_commands — Phase 2 Wave 16 extraction (Session 28).

Команды управления состоянием/моделями/desktop-контуром:
  !clear, !forget, !reset, !model, !web, !macos (alias !mac), !browser

Re-exported из command_handlers.py для обратной совместимости (тесты,
external imports `from src.handlers.command_handlers import handle_X`).

Использует dual-namespace lookup pattern: тесты могут патчить
`command_handlers.<symbol>` и оно подхватится через `_ch_attr()`.
"""

from __future__ import annotations

import datetime
import os
from typing import TYPE_CHECKING, Any

import httpx
from pyrogram.types import Message

from ...cache_manager import history_cache as _history_cache_baseline
from ...cache_manager import search_cache as _search_cache_baseline
from ...config import config as _config_baseline
from ...core.access_control import AccessLevel
from ...core.exceptions import UserInputError
from ...core.lm_studio_health import is_lm_studio_available as _is_lm_studio_available_baseline
from ...core.logger import get_logger
from ...core.model_aliases import normalize_model_alias as _normalize_model_alias_baseline
from ...core.scheduler import parse_due_time, split_reminder_input
from ...integrations.macos_automation import macos_automation as _macos_automation_baseline
from ...model_manager import model_manager as _model_manager_baseline
from ...openclaw_client import openclaw_client as _openclaw_client_baseline

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

_logger_baseline = get_logger(__name__)


class _LoggerProxy:
    """Прокси к command_handlers.logger (тесты патчат его), fallback к локальному."""

    def __getattr__(self, item: str) -> Any:
        from .. import command_handlers as _ch  # noqa: PLC0415

        target = getattr(_ch, "logger", _logger_baseline)
        return getattr(target, item)


logger = _LoggerProxy()


# ---------------------------------------------------------------------------
# Dual-namespace lookup (patch через command_handlers namespace)
# ---------------------------------------------------------------------------


def _ch_attr(name: str, default: Any) -> Any:
    """Dual-namespace lookup: command_handlers namespace first (для monkeypatch),
    fallback к local baseline."""
    from .. import command_handlers as _ch  # noqa: PLC0415

    return getattr(_ch, name, default)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_size_gb(size_gb: float) -> str:
    """Форматирует размер модели для человекочитаемого вывода."""
    try:
        value = float(size_gb)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        return "n/a"
    return f"{value:.2f} GB"


def _split_text_for_telegram(text: str, limit: int = 3900) -> list[str]:
    """Делит длинный текст на части с сохранением границ строк (Telegram limit ~4096)."""
    lines = text.splitlines()
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(line) <= limit:
            current = line
        else:
            for i in range(0, len(line), limit):
                part = line[i : i + limit]
                if len(part) == limit:
                    chunks.append(part)
                else:
                    current = part
    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


# ---------------------------------------------------------------------------
# !model
# ---------------------------------------------------------------------------


async def handle_model(bot: "KraabUserbot", message: Message) -> None:
    """Управление маршрутизацией и загрузкой AI моделей."""
    config = _ch_attr("config", _config_baseline)
    model_manager = _ch_attr("model_manager", _model_manager_baseline)
    normalize_model_alias = _ch_attr("normalize_model_alias", _normalize_model_alias_baseline)

    args = message.text.split()
    sub = args[1].lower() if len(args) > 1 else ""

    async def _is_local_model(model_id: str) -> bool:
        """Определяет, относится ли model_id к локальным моделям LM Studio."""
        normalized = str(model_id or "").strip().lower()
        if normalized in {"local", "lmstudio/local"} or normalized.startswith("lmstudio/"):
            return True
        try:
            models = await model_manager.discover_models()
            return any(m.id == model_id and m.type.name.startswith("LOCAL") for m in models)
        except Exception:
            return normalized.startswith("local/") or "mlx" in normalized

    if not sub:
        force_cloud = getattr(config, "FORCE_CLOUD", False)
        if force_cloud:
            mode_label = "☁️ cloud (принудительно)"
        else:
            mode_label = "🤖 auto"
        current = model_manager._current_model or "нет"
        cloud_model = config.MODEL or "не задана"
        text = (
            "🧭 **Маршрутизация моделей**\n"
            f"---------------------------\n"
            f"**Режим:** {mode_label}\n"
            f"**Активная модель:** `{current}`\n"
            f"**Облачная модель:** `{cloud_model}`\n"
            f"**LM Studio URL:** `{config.LM_STUDIO_URL}`\n"
            f"**FORCE_CLOUD:** `{force_cloud}`\n\n"
            "_Подкоманды: `info`, `local`, `cloud`, `auto`, `set <model_id>`, `load <name>`, `unload`, `scan`_"
        )
        await message.reply(text)
        return

    if sub == "local":
        config.update_setting("FORCE_CLOUD", "0")
        config.FORCE_CLOUD = False
        await message.reply("💻 Режим: **local** — используется локальная модель (LM Studio).")
        return

    if sub == "cloud":
        config.update_setting("FORCE_CLOUD", "1")
        config.FORCE_CLOUD = True
        await message.reply(f"☁️ Режим: **cloud** — используется `{config.MODEL}`.")
        return

    if sub == "auto":
        config.update_setting("FORCE_CLOUD", "0")
        config.FORCE_CLOUD = False
        await message.reply("🤖 Режим: **auto** — автоматический выбор лучшей модели.")
        return

    if sub == "set":
        if len(args) < 3:
            raise UserInputError(user_message="⚙️ Формат: `!model set <model_id>`")

        raw_id = args[2].strip()
        resolved_id, alias_note = normalize_model_alias(raw_id)
        is_local = await _is_local_model(resolved_id)

        if is_local:
            config.update_setting("LOCAL_PREFERRED_MODEL", resolved_id)
            config.update_setting("FORCE_CLOUD", "0")
            config.FORCE_CLOUD = False
            await message.reply(
                "💻 Зафиксирована локальная модель.\n"
                f"**Model:** `{resolved_id}`\n"
                f"{f'ℹ️ Alias: {alias_note}' if alias_note else ''}\n"
                "Режим переключен в `auto/local` (без принудительного cloud)."
            )
            return

        config.update_setting("MODEL", resolved_id)
        config.update_setting("FORCE_CLOUD", "1")
        config.FORCE_CLOUD = True
        await message.reply(
            "☁️ Зафиксирована облачная модель.\n"
            f"**Model:** `{resolved_id}`\n"
            f"{f'ℹ️ Alias: {alias_note}' if alias_note else ''}\n"
            "Режим переключен в `cloud`."
        )
        return

    if sub == "load":
        if len(args) < 3:
            raise UserInputError(user_message="⚙️ Укажите модель: `!model load <name>`")
        mid = args[2]
        msg = await message.reply(f"⏳ Загружаю `{mid}`...")
        try:
            ok = await model_manager.load_model(mid)
            if ok:
                config.update_setting("MODEL", mid)
                await msg.edit(f"✅ Модель загружена: `{mid}`")
            else:
                await msg.edit(f"❌ Не удалось загрузить `{mid}`")
        except Exception as e:
            await msg.edit(f"❌ Ошибка загрузки: `{str(e)[:200]}`")
        return

    if sub == "unload":
        msg = await message.reply("⏳ Выгружаю модели...")
        try:
            await model_manager.unload_all()
            await msg.edit("✅ Все модели выгружены. VRAM освобождена.")
        except Exception as e:
            await msg.edit(f"❌ Ошибка выгрузки: `{str(e)[:200]}`")
        return

    if sub == "info":
        text = await _format_model_info()
        await message.reply(text)
        return

    if sub in ("scan", "list"):
        msg = await message.reply("🔍 Сканирую доступные модели...")
        try:
            models = await model_manager.discover_models()
            from ...core.cloud_gateway import get_cloud_fallback_chain  # noqa: PLC0415

            cloud_ids = [c for c in get_cloud_fallback_chain() if "gemini" in c.lower()]
            local_models = [m for m in models if m.type.name.startswith("LOCAL")]
            cloud_from_api = [m for m in models if m.type.name.startswith("CLOUD")]
            cloud_seen = {m.id for m in cloud_from_api}
            for cid in cloud_ids:
                if cid not in cloud_seen:
                    from ...core.model_types import (  # noqa: PLC0415
                        ModelInfo,
                        ModelStatus,
                        ModelType,
                    )

                    cloud_from_api.append(
                        ModelInfo(
                            id=cid,
                            name=cid,
                            type=ModelType.CLOUD_GEMINI,
                            status=ModelStatus.AVAILABLE,
                            size_gb=0.0,
                            supports_vision=True,
                        )
                    )
                    cloud_seen.add(cid)
            lines = [
                f"🔍 **Доступные модели** (local={len(local_models)}, cloud={len(cloud_from_api)})\n",
                "☁️ **Облачные**\n",
            ]
            for m in sorted(cloud_from_api, key=lambda x: x.id):
                loaded = " ✅" if m.id == model_manager._current_model else ""
                lines.append(
                    f"☁️ `{m.id}` · `{_format_size_gb(getattr(m, 'size_gb', 0.0))}`{loaded}"
                )
            lines.append("\n💻 **Локальные**\n")
            for m in sorted(local_models, key=lambda x: x.id):
                loaded = " ✅" if m.id == model_manager._current_model else ""
                lines.append(
                    f"💻 `{m.id}` · `{_format_size_gb(getattr(m, 'size_gb', 0.0))}`{loaded}"
                )
            text = "\n".join(lines)
            chunks = _split_text_for_telegram(text)
            await msg.edit(chunks[0])
            for part in chunks[1:]:
                await message.reply(part)
        except Exception as e:
            await msg.edit(f"❌ Ошибка сканирования: `{str(e)[:200]}`")
        return

    raise UserInputError(
        user_message=(
            f"❓ Неизвестная подкоманда `{sub}`.\n"
            "Доступные: `local`, `cloud`, `auto`, `set`, `load`, `unload`, `scan`, `info`"
        )
    )


async def _format_model_info() -> str:
    """Формирует Markdown-отчёт для `!model info`."""
    from ...core.cloud_gateway import get_cloud_fallback_chain  # noqa: PLC0415

    openclaw_client = _ch_attr("openclaw_client", _openclaw_client_baseline)
    is_lm_studio_available = _ch_attr("is_lm_studio_available", _is_lm_studio_available_baseline)

    # 1) Активный маршрут
    try:
        last_route = openclaw_client.get_last_runtime_route() or {}
    except Exception:
        last_route = getattr(openclaw_client, "_last_runtime_route", {}) or {}

    # 2) Providers health через локальный web-app (1.5s timeout).
    cloud_report: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get("http://127.0.0.1:8080/api/openclaw/cloud")
            if resp.status_code == 200:
                payload = resp.json() or {}
                cloud_report = payload.get("report", {}) if isinstance(payload, dict) else {}
    except Exception:
        cloud_report = {}

    # 3) Fallback chain.
    try:
        fallback_chain = get_cloud_fallback_chain()
    except Exception:
        fallback_chain = []

    # 4) LM Studio availability.
    try:
        lm_available = bool(await is_lm_studio_available())
    except Exception:
        lm_available = False

    lines: list[str] = ["🤖 **Model Info**", "", "**Active route:**"]
    provider = str(last_route.get("provider") or "n/a")
    model_id = str(last_route.get("model") or "n/a")
    tier = str(last_route.get("active_tier") or "n/a")
    status = str(last_route.get("status") or "n/a")
    ts_raw = last_route.get("timestamp")
    if isinstance(ts_raw, int) and ts_raw > 0:
        ts_str = datetime.datetime.fromtimestamp(ts_raw).strftime("%H:%M:%S")
        status_line = f"✅ {status} ({ts_str})" if status == "ok" else f"⚠️ {status} ({ts_str})"
    else:
        status_line = status
    lines.append(f"• Provider: `{provider}`")
    lines.append(f"• Model: `{model_id}`")
    lines.append(f"• Tier: `{tier}`")
    lines.append(f"• Last status: {status_line}")

    # Fallback chain.
    lines.append("")
    lines.append("**Fallback chain:**")
    if fallback_chain:
        for idx, mid in enumerate(fallback_chain[:6], start=1):
            lines.append(f"{idx}. {mid}")
    else:
        lines.append("_(пусто или недоступно)_")

    # Providers health.
    lines.append("")
    lines.append("**Providers health:**")
    providers = cloud_report.get("providers", {}) if isinstance(cloud_report, dict) else {}
    if providers:
        for name, info in providers.items():
            if not isinstance(info, dict):
                continue
            ok = info.get("ok")
            http_status = info.get("http_status")
            provider_status = info.get("provider_status", "n/a")
            icon = "✅" if ok else "❌"
            http_hint = f" (http {http_status})" if http_status else ""
            lines.append(f"• {name}: {icon} {provider_status}{http_hint}")
    else:
        lines.append("• google: ⚠️ недоступно (cloud API off)")

    lm_state = "ready" if lm_available else "idle"
    lines.append(f"• LM Studio: {lm_state}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# !clear
# ---------------------------------------------------------------------------


async def handle_clear(bot: "KraabUserbot", message: Message) -> None:
    """Очистка контекста / кэшей."""
    openclaw_client = _ch_attr("openclaw_client", _openclaw_client_baseline)
    history_cache = _ch_attr("history_cache", _history_cache_baseline)
    search_cache = _ch_attr("search_cache", _search_cache_baseline)

    chat_id = str(message.chat.id)
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    sub = parts[1].strip().lower() if len(parts) > 1 else ""

    if sub == "all":
        count = len(openclaw_client._sessions)
        openclaw_client._sessions.clear()
        if hasattr(openclaw_client, "_lm_native_chat_state"):
            openclaw_client._lm_native_chat_state.clear()
        res = f"🧹 **Все сессии очищены** (`{count}` чат(ов)). Краб начинает с чистого листа!"
    elif sub == "cache":
        h_count = history_cache.clear_all()
        s_count = search_cache.clear_all()
        res = (
            f"🗑️ **Кэши очищены**\n"
            f"• history_cache: `{h_count}` записей\n"
            f"• search_cache: `{s_count}` записей"
        )
    else:
        openclaw_client.clear_session(chat_id)
        res = "🧹 **Память очищена. Клешни как новые!**"

    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(res)
    else:
        await message.reply(res)


# ---------------------------------------------------------------------------
# !forget / !clear_session
# ---------------------------------------------------------------------------


async def handle_forget(bot: "KraabUserbot", message: Message) -> None:
    """!forget — очистить session history текущего чата. Owner-only."""
    openclaw_client = _ch_attr("openclaw_client", _openclaw_client_baseline)

    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

    chat_id = str(message.chat.id)
    openclaw_client.clear_session(chat_id)
    await message.reply("🧠 Контекст чата очищен. Начинаю свежий разговор.")


# ---------------------------------------------------------------------------
# !reset — агрессивная многослойная очистка
# ---------------------------------------------------------------------------


async def handle_reset(bot: "KraabUserbot", message: Message) -> None:
    """Очищает все слои истории одной операцией."""
    from ...core.gemini_cache_nonce import invalidate_gemini_cache_for_chat  # noqa: PLC0415
    from ...core.reset_helpers import (  # noqa: PLC0415
        clear_archive_db_for_chat,
        count_archive_messages_for_chat,
    )

    openclaw_client = _ch_attr("openclaw_client", _openclaw_client_baseline)
    history_cache = _ch_attr("history_cache", _history_cache_baseline)

    valid_layers = {"krab", "openclaw", "gemini", "archive"}

    chat_id = str(message.chat.id)
    raw_args = ""
    if hasattr(bot, "_get_command_args"):
        try:
            raw_args = (bot._get_command_args(message) or "").strip()
        except Exception:  # noqa: BLE001
            raw_args = ""

    tokens = raw_args.split() if raw_args else []
    is_all = "--all" in tokens
    is_force = "--force" in tokens
    dry_run_aliases = {"--dry-run", "dry-run", "dryrun", "dry"}
    dry_run = any(token.lower() in dry_run_aliases for token in tokens)
    layer: str | None = None
    for token in tokens:
        if token.startswith("--layer="):
            layer = token.split("=", 1)[1].strip().lower() or None

    if layer is not None and layer not in valid_layers:
        await message.reply(f"❌ Unknown layer: `{layer}`. Valid: {sorted(valid_layers)}")
        return

    if is_all:
        me_id = getattr(getattr(bot, "me", None), "id", None)
        sender_id = getattr(getattr(message, "from_user", None), "id", None)
        if me_id is None or sender_id != me_id:
            await message.reply("🚫 `!reset --all` доступен только владельцу.")
            return

    if is_all and not is_force and not dry_run:
        await message.reply(
            "⚠️ `!reset --all` удалит историю из **ВСЕХ** чатов (все слои).\n"
            "Это необратимо.\n\nПовтори с флагом `--force`:\n`!reset --all --force`"
        )
        return

    if is_all:
        target_chat_ids = [str(cid) for cid in openclaw_client._sessions.keys()]
        if not target_chat_ids:
            target_chat_ids = [chat_id]
    else:
        target_chat_ids = [chat_id]

    if is_all and is_force and not dry_run:
        sender_id = getattr(getattr(message, "from_user", None), "id", None)
        logger.warning(
            "reset_all_force_executed",
            chat_count=len(target_chat_ids),
            user_id=sender_id,
            layer=layer or "all",
        )

    impact = {"krab": 0, "openclaw": 0, "gemini": 0, "archive": 0}

    if layer in (None, "krab"):
        for cid in target_chat_ids:
            try:
                if history_cache.get(f"chat_history:{cid}"):
                    impact["krab"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("reset_krab_probe_failed", chat_id=cid, error=str(exc))

    if layer in (None, "openclaw"):
        impact["openclaw"] = sum(1 for cid in target_chat_ids if cid in openclaw_client._sessions)

    if layer in (None, "gemini"):
        impact["gemini"] = len(target_chat_ids)

    if layer == "archive":
        for cid in target_chat_ids:
            impact["archive"] += count_archive_messages_for_chat(cid)

    if dry_run:
        scope = "все чаты" if is_all else "текущий чат"
        archive_hint = ""
        if layer is None:
            archive_hint = (
                "\n⚠️ Archive НЕ включён в default scope. "
                "Используй `--layer=archive` для очистки archive.db."
            )
        preview = (
            f"🔍 **Dry-run** (nothing deleted)\n"
            f"Scope: {scope}\n"
            f"Layer filter: `{layer or 'all'}`\n\n"
            f"Удалилось бы:\n"
            f"• Krab cache: {impact['krab']}\n"
            f"• OpenClaw: {impact['openclaw']}\n"
            f"• Gemini cache invalidate: {impact['gemini']}\n"
            f"• Archive: {impact['archive']}{archive_hint}"
        )
        if message.from_user and message.from_user.id == bot.me.id:
            await message.edit(preview)
        else:
            await message.reply(preview)
        return

    stats = {"krab": 0, "openclaw": 0, "gemini": 0, "archive": 0}

    total = len(target_chat_ids)
    progress_msg = None
    if total > 10:
        try:
            progress_msg = await message.reply(f"🔄 Reset: 0 / {total}...")
        except Exception as exc:  # noqa: BLE001
            logger.warning("reset_progress_init_failed", error=str(exc))
            progress_msg = None

    for idx, cid in enumerate(target_chat_ids):
        if layer in (None, "krab"):
            key = f"chat_history:{cid}"
            try:
                if history_cache.get(key):
                    history_cache.delete(key)
                    stats["krab"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("reset_krab_failed", chat_id=cid, error=str(exc))

        if layer in (None, "openclaw"):
            try:
                openclaw_client.clear_session(cid)
                stats["openclaw"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("reset_openclaw_failed", chat_id=cid, error=str(exc))

        if layer in (None, "gemini"):
            try:
                invalidate_gemini_cache_for_chat(cid)
                stats["gemini"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("reset_gemini_failed", chat_id=cid, error=str(exc))

        if layer == "archive":
            try:
                stats["archive"] += clear_archive_db_for_chat(cid)
            except Exception as exc:  # noqa: BLE001
                logger.warning("reset_archive_failed", chat_id=cid, error=str(exc))

        if progress_msg is not None and (idx + 1) % 10 == 0 and (idx + 1) < total:
            try:
                await progress_msg.edit(f"🔄 Reset: {idx + 1} / {total}...")
            except Exception:  # noqa: BLE001
                pass

    if progress_msg is not None:
        try:
            await progress_msg.delete()
        except Exception:  # noqa: BLE001
            pass

    scope = "всех чатов" if is_all else "текущего чата"
    res = (
        f"🗑️ **Reset выполнен** ({scope})\n"
        f"• Krab cache: {stats['krab']}\n"
        f"• OpenClaw: {stats['openclaw']}\n"
        f"• Gemini: {stats['gemini']}\n"
        f"• Archive: {stats['archive']}"
    )
    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(res)
    else:
        await message.reply(res)


# ---------------------------------------------------------------------------
# !web — автоматизация браузера (web_session)
# ---------------------------------------------------------------------------


async def handle_web(bot: "KraabUserbot", message: Message) -> None:
    """Автоматизация браузера."""
    from ...web_session import web_manager  # noqa: PLC0415

    args = message.text.split()
    if len(args) < 2:
        from urllib.parse import quote  # noqa: PLC0415

        def link(c: str) -> str:
            return f"https://t.me/share/url?url={quote(c)}"

        await message.reply(
            "🌏 **Web Control**\n\n"
            f"[🔑 Login]({link('!web login')}) | [📸 Screen]({link('!web screen')})\n"
            f"[🤖 GPT]({link('!web gpt привет')})",
            disable_web_page_preview=True,
        )
        return
    sub = args[1].lower()
    if sub == "login":
        await message.reply(await web_manager.login_mode())
    elif sub == "screen":
        path = await web_manager.take_screenshot()
        if path:
            await message.reply_photo(path)
            if os.path.exists(path):
                os.remove(path)
    elif sub == "stop":
        await web_manager.stop()
        await message.reply("🛑 Web остановлен.")
    elif sub == "self-test":
        await bot._run_self_test(message)


# ---------------------------------------------------------------------------
# !macos / !mac — desktop control layer
# ---------------------------------------------------------------------------


async def handle_macos(bot: "KraabUserbot", message: Message) -> None:
    """Базовое управление macOS из owner/full-контура."""
    del bot
    macos_automation = _ch_attr("macos_automation", _macos_automation_baseline)

    raw_args = str(message.text or "").split(maxsplit=1)
    args = raw_args[1].strip() if len(raw_args) > 1 else ""

    if not macos_automation.is_available():
        await message.reply(
            "🍎 macOS automation сейчас недоступен.\n"
            "Нужны `osascript`, `open`, `pbcopy`, `pbpaste` и запуск на macOS."
        )
        return

    if not args:
        await message.reply(
            "🍎 **macOS control layer**\n\n"
            "`!mac status` — краткий статус desktop-контура\n"
            "`!mac clip get` — прочитать clipboard\n"
            "`!mac clip set <текст>` — записать clipboard\n"
            "`!mac notify <текст>` — показать системное уведомление\n"
            "`!mac notify <заголовок> | <текст>` — уведомление с заголовком\n"
            "`!mac app front` — активное приложение\n"
            "`!mac app list` — список видимых приложений\n"
            "`!mac app open <имя>` — открыть приложение\n"
            "`!mac focus <имя>` — вывести приложение на передний план\n"
            "`!mac type <текст>` — напечатать текст в активном окне\n"
            "`!mac typeclip <текст>` — вставить текст через clipboard (Unicode/кириллица)\n"
            "`!mac click <приложение> <кнопка>` — нажать кнопку UI элемент\n"
            "`!mac key <клавиша>` — нажать клавишу (return/tab/escape/...)\n"
            "`!mac reminders list` — список напоминаний из macOS Reminders\n"
            "`!mac reminders add <время> | <текст>` — создать reminder в Reminders\n"
            "`!mac notes list` — список заметок\n"
            "`!mac notes add <заголовок> | <текст>` — создать заметку\n"
            "`!mac calendar list` — список календарей\n"
            "`!mac calendar events` — ближайшие события\n"
            "`!mac calendar add <время> | <название>` — создать событие (30 мин)\n"
            "`!mac open <url|path>` — открыть URL или путь\n"
            "`!mac finder reveal <path>` — показать файл/папку в Finder"
        )
        return

    parts = args.split(maxsplit=2)
    sub = parts[0].lower()

    if sub == "status":
        status = await macos_automation.status()
        lines = [
            "🍎 **macOS control layer**",
            f"- Доступность: {'ON' if status.get('available') else 'OFF'}",
            f"- Активное приложение: `{status.get('frontmost_app') or 'n/a'}`",
        ]
        if status.get("frontmost_window"):
            lines.append(f"- Переднее окно: `{status.get('frontmost_window')}`")
        running_apps = status.get("running_apps") or []
        if running_apps:
            lines.append("- Видимые приложения: " + ", ".join(f"`{item}`" for item in running_apps))
        lines.append(
            f"- Clipboard: {int(status.get('clipboard_chars', 0) or 0)} символов"
            + (f" (`{status.get('clipboard_preview')}`)" if status.get("clipboard_preview") else "")
        )
        warnings = status.get("warnings") or []
        if warnings:
            lines.append("- Warnings: " + "; ".join(str(item) for item in warnings[:3]))
        reminder_lists = status.get("reminder_lists") or []
        note_folders = status.get("note_folders") or []
        calendars = status.get("calendars") or []
        if reminder_lists:
            lines.append(
                "- Reminders lists: " + ", ".join(f"`{item}`" for item in reminder_lists[:5])
            )
        if note_folders:
            lines.append("- Notes folders: " + ", ".join(f"`{item}`" for item in note_folders[:5]))
        if calendars:
            lines.append("- Calendars: " + ", ".join(f"`{item}`" for item in calendars[:6]))
        await message.reply("\n".join(lines))
        return

    if sub == "reminders":
        if len(parts) < 2:
            raise UserInputError(
                user_message="🍎 Формат: `!mac reminders list` или `!mac reminders add <время> | <текст>`"
            )
        rem_action = parts[1].lower()
        if rem_action == "list":
            rows = await macos_automation.list_reminders(limit=8)
            if not rows:
                await message.reply("📝 В macOS Reminders сейчас нет незавершённых напоминаний.")
                return
            lines = ["📝 **Reminders (macOS)**"]
            for item in rows:
                due = f" · `{item['due_label']}`" if item.get("due_label") else ""
                lines.append(f"- `{item['title']}` — список `{item['list_name']}`{due}")
            await message.reply("\n".join(lines))
            return
        if rem_action == "add":
            payload = args.split(maxsplit=2)[2] if len(args.split(maxsplit=2)) > 2 else ""
            time_spec, reminder_text = split_reminder_input(payload)
            if not time_spec or not reminder_text:
                raise UserInputError(
                    user_message="🍎 Формат: `!mac reminders add <время> | <текст>`"
                )
            due_at = parse_due_time(time_spec)
            created = await macos_automation.create_reminder(title=reminder_text, due_at=due_at)
            due_label = due_at.astimezone().strftime("%d.%m.%Y %H:%M")
            await message.reply(
                "✅ Reminder создан в macOS Reminders.\n"
                f"- ID: `{created['id']}`\n"
                f"- Список: `{created['list_name']}`\n"
                f"- Когда: `{due_label}`\n"
                f"- Текст: {reminder_text}"
            )
            return
        raise UserInputError(
            user_message="🍎 Формат: `!mac reminders list` или `!mac reminders add <время> | <текст>`"
        )

    if sub == "notes":
        if len(parts) < 2:
            raise UserInputError(
                user_message="🍎 Формат: `!mac notes list` или `!mac notes add <заголовок> | <текст>`"
            )
        notes_action = parts[1].lower()
        if notes_action == "list":
            rows = await macos_automation.list_notes(limit=8)
            if not rows:
                await message.reply("🗒️ В Notes пока ничего не найдено.")
                return
            lines = ["🗒️ **Notes (macOS)**"]
            for item in rows:
                lines.append(
                    f"- `{item['title']}` — папка `{item['folder_name']}`, аккаунт `{item['account_name']}`"
                )
            await message.reply("\n".join(lines))
            return
        if notes_action == "add":
            payload = args.split(maxsplit=2)[2] if len(args.split(maxsplit=2)) > 2 else ""
            if "|" not in payload:
                raise UserInputError(
                    user_message="🍎 Формат: `!mac notes add <заголовок> | <текст>`"
                )
            raw_title, raw_body = payload.split("|", 1)
            title = raw_title.strip()
            body = raw_body.strip()
            if not title or not body:
                raise UserInputError(
                    user_message="🍎 Заголовок и текст заметки не должны быть пустыми."
                )
            created = await macos_automation.create_note(title=title, body=body)
            await message.reply(
                "✅ Заметка создана в Notes.\n"
                f"- ID: `{created['id']}`\n"
                f"- Папка: `{created['folder_name']}`\n"
                f"- Заголовок: `{title}`"
            )
            return
        raise UserInputError(
            user_message="🍎 Формат: `!mac notes list` или `!mac notes add <заголовок> | <текст>`"
        )

    if sub == "calendar":
        if len(parts) < 2:
            raise UserInputError(
                user_message="🍎 Формат: `!mac calendar list`, `!mac calendar events` или `!mac calendar add <время> | <название>`"
            )
        cal_action = parts[1].lower()
        if cal_action == "list":
            rows = await macos_automation.list_calendars()
            await message.reply(
                "📆 **Calendars (macOS)**\n"
                + ("\n".join(f"- `{item}`" for item in rows[:12]) if rows else "- список пуст")
            )
            return
        if cal_action == "events":
            rows = await macos_automation.list_upcoming_calendar_events(limit=8, days_ahead=7)
            if not rows:
                await message.reply("📆 На ближайшие 7 дней событий не найдено.")
                return
            lines = ["📆 **Ближайшие события Calendar**"]
            for item in rows:
                lines.append(
                    f"- `{item['title']}` — календарь `{item['calendar_name']}` · `{item['start_label']}`"
                )
            await message.reply("\n".join(lines))
            return
        if cal_action == "add":
            payload = args.split(maxsplit=2)[2] if len(args.split(maxsplit=2)) > 2 else ""
            time_spec, event_title = split_reminder_input(payload)
            if not time_spec or not event_title:
                raise UserInputError(
                    user_message="🍎 Формат: `!mac calendar add <время> | <название>`"
                )
            start_at = parse_due_time(time_spec)
            created = await macos_automation.create_calendar_event(
                title=event_title, start_at=start_at, duration_minutes=30
            )
            start_label = start_at.astimezone().strftime("%d.%m.%Y %H:%M")
            await message.reply(
                "✅ Событие создано в Calendar.\n"
                f"- ID: `{created['id']}`\n"
                f"- Календарь: `{created['calendar_name']}`\n"
                f"- Начало: `{start_label}`\n"
                f"- Название: `{event_title}`"
            )
            return
        raise UserInputError(
            user_message="🍎 Формат: `!mac calendar list`, `!mac calendar events` или `!mac calendar add <время> | <название>`"
        )

    if sub in {"clip", "clipboard"}:
        if len(parts) < 2:
            raise UserInputError(
                user_message="🍎 Формат: `!mac clip get` или `!mac clip set <текст>`"
            )
        clip_action = parts[1].lower()
        if clip_action == "get":
            content = await macos_automation.get_clipboard_text()
            preview = content if len(content) <= 3400 else content[:3400] + "…"
            await message.reply(
                "📋 **Clipboard**\n\n"
                + (f"```\n{preview}\n```" if preview else "_Буфер обмена пустой или не текстовый._")
            )
            return
        if clip_action == "set":
            if len(parts) < 3 or not parts[2].strip():
                raise UserInputError(user_message="🍎 Формат: `!mac clip set <текст>`")
            await macos_automation.set_clipboard_text(parts[2])
            await message.reply(f"📋 Clipboard обновлён: `{parts[2][:120]}`")
            return
        raise UserInputError(user_message="🍎 Формат: `!mac clip get` или `!mac clip set <текст>`")

    if sub == "notify":
        payload = args[len("notify") :].strip()
        if not payload:
            raise UserInputError(
                user_message="🍎 Формат: `!mac notify <текст>` или `!mac notify <заголовок> | <текст>`"
            )
        title = "Краб"
        body = payload
        if "|" in payload:
            raw_title, raw_body = payload.split("|", 1)
            title = raw_title.strip() or "Краб"
            body = raw_body.strip()
        if not body:
            raise UserInputError(user_message="🍎 Уведомление не может быть пустым.")
        await macos_automation.show_notification(title=title, message=body)
        await message.reply(f"🔔 Уведомление отправлено: `{title}`")
        return

    if sub == "app":
        if len(parts) < 2:
            raise UserInputError(user_message="🍎 Формат: `!mac app front|list|open <имя>`")
        app_action = parts[1].lower()
        if app_action == "front":
            front = await macos_automation.get_frontmost_app()
            reply = f"🪟 Активное приложение: `{front.get('app_name') or 'n/a'}`"
            if front.get("window_title"):
                reply += f"\nЗаголовок окна: `{front['window_title']}`"
            await message.reply(reply)
            return
        if app_action == "list":
            apps = await macos_automation.list_running_apps(limit=12)
            await message.reply(
                "🧩 **Видимые приложения**\n"
                + ("\n".join(f"- `{item}`" for item in apps) if apps else "\n- список пуст")
            )
            return
        if app_action == "open":
            if len(parts) < 3 or not parts[2].strip():
                raise UserInputError(user_message="🍎 Формат: `!mac app open <имя приложения>`")
            opened = await macos_automation.open_app(parts[2])
            await message.reply(f"🚀 Открываю приложение: `{opened}`")
            return
        raise UserInputError(user_message="🍎 Формат: `!mac app front|list|open <имя>`")

    if sub == "open":
        target = args[len("open") :].strip()
        if not target:
            raise UserInputError(user_message="🍎 Формат: `!mac open <url|path>`")
        opened = await macos_automation.open_target(target)
        await message.reply(f"🚀 Открываю {opened['kind']}: `{opened['target']}`")
        return

    if sub == "finder":
        if len(parts) < 3 or parts[1].lower() != "reveal":
            raise UserInputError(user_message="🍎 Формат: `!mac finder reveal <path>`")
        revealed = await macos_automation.reveal_in_finder(parts[2])
        await message.reply(f"📂 Показываю в Finder: `{revealed}`")
        return

    if sub == "focus":
        app_arg = args[len("focus") :].strip()
        if not app_arg:
            raise UserInputError(user_message="🍎 Формат: `!mac focus <имя приложения>`")
        result = await macos_automation.focus_app(app_arg)
        await message.reply(f"🪟 Фокус: `{result['app_name']}`")
        return

    if sub == "type":
        text_arg = args[len("type") :].strip()
        if not text_arg:
            raise UserInputError(user_message="🍎 Формат: `!mac type <текст>`")
        result = await macos_automation.type_text(text_arg)
        await message.reply(
            f"⌨️ Напечатано {result['text_length']} символов в `{result['app_name']}`"
        )
        return

    if sub == "typeclip":
        text_arg = args[len("typeclip") :].strip()
        if not text_arg:
            raise UserInputError(
                user_message="🍎 Формат: `!mac typeclip <текст>` (через clipboard, поддерживает Unicode)"
            )
        result = await macos_automation.type_text_via_clipboard(text_arg)
        await message.reply(
            f"📋→⌨️ Вставлено {result['text_length']} символов в `{result['app_name']}`"
        )
        return

    if sub == "click":
        if len(parts) < 3:
            raise UserInputError(user_message="🍎 Формат: `!mac click <приложение> <кнопка>`")
        app_arg = parts[1]
        elem_arg = " ".join(parts[2:])
        result = await macos_automation.click_ui_element(app_arg, elem_arg)
        await message.reply(f"🖱 Нажато: `{result['element']}` в `{result['app_name']}`")
        return

    if sub == "key":
        key_arg = args[len("key") :].strip()
        if not key_arg:
            raise UserInputError(
                user_message="🍎 Формат: `!mac key <клавиша>` (return/tab/escape/...)"
            )
        result = await macos_automation.press_key(key_arg)
        await message.reply(f"⌨️ Нажато: `{result['key']}`")
        return

    raise UserInputError(
        user_message=(
            "🍎 Неизвестная подкоманда macOS.\n"
            "Используй: `!mac status`, `!mac clip ...`, `!mac notify ...`, "
            "`!mac app ...`, `!mac focus ...`, `!mac type ...`, `!mac click ...`, "
            "`!mac key ...`, `!mac open ...`, `!mac finder reveal ...`"
        )
    )


# ---------------------------------------------------------------------------
# !browser — Chrome через CDP
# ---------------------------------------------------------------------------


async def handle_browser(bot: "KraabUserbot", message: Message) -> None:
    """Управление Chrome через CDP."""
    from ...integrations.browser_bridge import browser_bridge  # noqa: PLC0415

    args = str(message.text or "").split(maxsplit=2)
    sub = str(args[1] if len(args) > 1 else "status").strip().lower()

    if sub == "status":
        attached = await browser_bridge.is_attached()
        if not attached:
            await message.reply(
                "🌐 Браузер: **отключён**\n"
                "Запусти `new Enable Chrome Remote Debugging.command` для подключения."
            )
            return
        tabs = await browser_bridge.list_tabs()
        active = tabs[-1] if tabs else None
        active_info = f"\n🔗 Активная: {active['url']}" if active else ""
        await message.reply(f"🌐 Браузер: **подключён** ({len(tabs)} вкладок){active_info}")
        return

    if sub == "tabs":
        tabs = await browser_bridge.list_tabs()
        if not tabs:
            await message.reply("🌐 Вкладок не найдено (браузер отключён или пуст).")
            return
        lines = [
            f"{i + 1}. {t.get('title') or t.get('url')}\n   {t['url']}" for i, t in enumerate(tabs)
        ]
        await message.reply("🌐 Вкладки:\n" + "\n".join(lines))
        return

    if sub == "open":
        url = str(args[2] if len(args) > 2 else "").strip()
        if not url:
            raise UserInputError(user_message="❌ Укажи URL: `!browser open <url>`")
        final_url = await browser_bridge.navigate(url)
        await message.reply(f"✅ Переход: {final_url}")
        return

    if sub == "read":
        text = await browser_bridge.get_page_text()
        if not text:
            await message.reply("❌ Не удалось получить текст страницы.")
            return
        await message.reply(f"📄 Страница (до 2000 символов):\n```\n{text[:2000]}\n```")
        return

    if sub == "shot":
        data = await browser_bridge.screenshot()
        if data is None:
            await message.reply("❌ Не удалось сделать скриншот.")
            return
        import tempfile  # noqa: PLC0415

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as _tmp:
            _tmp.write(data)
            _tmp_path = _tmp.name
        try:
            await message.reply_photo(_tmp_path)
        except Exception as _photo_err:
            logger.warning("reply_photo_failed_browser_shot", error=str(_photo_err))
            try:
                await message.reply_document(_tmp_path, caption="📸 Screenshot (fallback)")
            except Exception as _doc_err:
                logger.error("reply_document_failed_browser_shot", error=str(_doc_err))
                await message.reply(f"❌ Не удалось отправить скриншот: `{str(_photo_err)[:200]}`")
        finally:
            os.unlink(_tmp_path)
        return

    if sub == "js":
        code = str(args[2] if len(args) > 2 else "").strip()
        if not code:
            raise UserInputError(user_message="❌ Укажи код: `!browser js <code>`")
        result = await browser_bridge.execute_js(code)
        await message.reply(f"✅ Результат:\n```\n{str(result)[:1000]}\n```")
        return

    if sub == "ai":
        from ...integrations.browser_ai_provider import browser_ai_provider  # noqa: PLC0415

        rest_parts = str(message.text or "").split(maxsplit=3)
        service = "gemini"
        prompt = ""
        if len(rest_parts) >= 3:
            maybe_service = rest_parts[2].lower()
            if maybe_service in ("gemini", "chatgpt"):
                service = maybe_service
                prompt = rest_parts[3] if len(rest_parts) > 3 else ""
            else:
                prompt = " ".join(rest_parts[2:])
        if not prompt:
            raise UserInputError(
                user_message=(
                    "❌ Укажи запрос: `!browser ai <prompt>` или\n"
                    "`!browser ai gemini <prompt>` / `!browser ai chatgpt <prompt>`"
                )
            )

        status_msg = await message.reply(f"🌐 Отправляю в {service}... ⏳")
        response = await browser_ai_provider.chat(prompt, service=service)  # type: ignore[arg-type]

        if response.startswith("[ERROR]"):
            await status_msg.edit(f"❌ {response}")
        else:
            preview = response[:3500]
            if len(response) > 3500:
                preview += "\n\n_[ответ обрезан]_"
            await status_msg.edit(f"🌐 **{service}**:\n\n{preview}")
        return

    raise UserInputError(
        user_message=(
            "🌐 Команды браузера:\n"
            "`!browser status` — статус\n"
            "`!browser tabs` — список вкладок\n"
            "`!browser open <url>` — навигация\n"
            "`!browser read` — текст страницы\n"
            "`!browser shot` — скриншот\n"
            "`!browser js <code>` — выполнить JS\n"
            "`!browser ai [gemini|chatgpt] <prompt>` — запрос через браузерный AI"
        )
    )
