# -*- coding: utf-8 -*-
"""
Обработчики Telegram-команд, вынесенные из userbot_bridge (Фаза 4.4).
Каждая функция принимает (bot, message) для тестируемости и уплощения register_handlers.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

import httpx
from pyrogram.types import Message

from ..config import config
from ..core.access_control import (
    AccessLevel,
    PARTIAL_ACCESS_COMMANDS,
    load_acl_runtime_state,
    update_acl_subject,
)
from ..core.exceptions import UserInputError
from ..core.inbox_service import inbox_service
from ..core.lm_studio_health import is_lm_studio_available
from ..core.logger import get_logger
from ..core.model_aliases import normalize_model_alias
from ..core.openclaw_workspace import append_workspace_memory_entry, recall_workspace_memory
from ..core.openclaw_workspace import list_workspace_memory_entries
from ..core.proactive_watch import proactive_watch
from ..core.scheduler import krab_scheduler, parse_due_time, split_reminder_input
from ..core.swarm import AgentRoom
from ..employee_templates import ROLES, get_role_prompt, list_roles, save_role
from ..integrations.macos_automation import macos_automation
from ..mcp_client import mcp_manager
from ..memory_engine import memory_manager
from ..model_manager import model_manager
from ..openclaw_client import openclaw_client
from ..search_engine import search_brave

logger = get_logger(__name__)

if TYPE_CHECKING:
    from ..userbot_bridge import KraabUserbot


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
    """
    Делит длинный текст на части с сохранением границ строк.
    Telegram ограничивает текст сообщения примерно 4096 символами.
    """
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
            # На случай сверхдлинной строки режем принудительно.
            for i in range(0, len(line), limit):
                part = line[i:i + limit]
                if len(part) == limit:
                    chunks.append(part)
                else:
                    current = part
    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


class _AgentRoomRouterAdapter:
    """
    Легковесный адаптер роевого запуска для userbot-команд.

    Почему отдельно:
    - AgentRoom ожидает контракт `route_query(prompt, skip_swarm=True)`;
    - userbot работает напрямую через `openclaw_client.send_message_stream`;
    - адаптер связывает эти два слоя без изменения core-логики.
    """

    def __init__(self, *, chat_id: str, system_prompt: str) -> None:
        self.chat_id = chat_id
        self.system_prompt = system_prompt

    async def route_query(self, prompt: str, skip_swarm: bool = False, **_: Any) -> str:
        """
        Выполняет один роевой шаг через OpenClaw stream.

        `skip_swarm` принят для совместимости контракта AgentRoom.
        """
        del skip_swarm
        chunks: list[str] = []
        max_output_tokens = int(getattr(config, "SWARM_ROLE_MAX_OUTPUT_TOKENS", 700) or 700)
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=self.chat_id,
            system_prompt=self.system_prompt,
            force_cloud=bool(getattr(config, "FORCE_CLOUD", False)),
            max_output_tokens=max_output_tokens,
        ):
            chunks.append(str(chunk))
        return "".join(chunks).strip()


async def handle_search(bot: "KraabUserbot", message: Message) -> None:
    """Ручной веб-поиск через Brave."""
    query = bot._get_command_args(message)
    if not query or query.lower() in ["search", "!search"]:
        raise UserInputError(user_message="🔍 Что ищем? Напиши: `!search <запрос>`")
    msg = await message.reply(f"🔍 **Краб ищет в сети:** `{query}`...")
    try:
        results = await search_brave(query)
        if len(results) > 4000:
            results = results[:3900] + "..."
        await msg.edit(f"🔍 **Результаты поиска:**\n\n{results}")
    except (httpx.HTTPError, OSError, ValueError, KeyError) as e:
        await msg.edit(f"❌ Ошибка поиска: {e}")


async def handle_remember(bot: "KraabUserbot", message: Message) -> None:
    """Запомнить факт."""
    text = bot._get_command_args(message)
    if not text:
        raise UserInputError(user_message="🧠 Что запомнить? Напиши: `!remember <текст>`")
    try:
        workspace_saved = append_workspace_memory_entry(
            text,
            source="userbot",
            author=str(getattr(getattr(message, "from_user", None), "username", "") or ""),
        )
        vector_saved = memory_manager.save_fact(text)
        success = workspace_saved or vector_saved
        if success:
            await message.reply(f"🧠 **Запомнил:** `{text}`")
        else:
            await message.reply("❌ Ошибка памяти.")
    except (ValueError, RuntimeError, OSError) as e:
        await message.reply(f"❌ Critical Memory Error: {e}")


async def handle_recall(bot: "KraabUserbot", message: Message) -> None:
    """Вспомнить факт."""
    text = bot._get_command_args(message)
    if not text:
        raise UserInputError(user_message="🧠 Что вспомнить? Напиши: `!recall <запрос>`")
    try:
        workspace_facts = recall_workspace_memory(text)
        vector_facts = memory_manager.recall(text)
        sections: list[str] = []
        if workspace_facts:
            sections.append(f"**OpenClaw workspace:**\n{workspace_facts}")
        if vector_facts and vector_facts not in workspace_facts:
            sections.append(f"**Local vector memory:**\n{vector_facts}")
        facts = "\n\n".join(section for section in sections if section).strip()
        if facts:
            await message.reply(f"🧠 **Вспомнил:**\n\n{facts}")
        else:
            await message.reply("🧠 Ничего не нашел по этому запросу.")
    except (ValueError, RuntimeError, OSError) as e:
        await message.reply(f"❌ Recalling Error: {e}")


async def handle_ls(bot: "KraabUserbot", message: Message) -> None:
    """Список файлов."""
    path = bot._get_command_args(message) or str(config.BASE_DIR)
    if ".." in path and not config.is_valid():
        pass
    msg = await message.reply("📂 Scanning...")
    try:
        result = await mcp_manager.list_directory(path)
        await msg.edit(f"📂 **Files in {path}:**\n\n`{result[:3900]}`")
    except (httpx.HTTPError, OSError, ValueError, KeyError, AttributeError) as e:
        await msg.edit(f"❌ Error listing: {e}")


async def handle_read(bot: "KraabUserbot", message: Message) -> None:
    """Чтение файла."""
    path = bot._get_command_args(message)
    if not path:
        raise UserInputError(user_message="📂 Какой файл читать? `!read <path>`")
    if not path.startswith("/"):
        path = os.path.join(config.BASE_DIR, path)
    msg = await message.reply("📂 Reading...")
    try:
        content = await mcp_manager.read_file(path)
        if len(content) > 4000:
            content = content[:1000] + "\n... [truncated]"
        await msg.edit(f"📂 **Content of {os.path.basename(path)}:**\n\n```\n{content}\n```")
    except (httpx.HTTPError, OSError, ValueError, KeyError, AttributeError) as e:
        await msg.edit(f"❌ Reading error: {e}")


async def handle_write(bot: "KraabUserbot", message: Message) -> None:
    """Запись файла (опасно!)."""
    text = bot._get_command_args(message)
    if not text:
        raise UserInputError(user_message="📂 Формат: `!write <filename> <content>`")
    parts = text.split("\n", 1)
    if len(parts) < 2:
        parts = text.split(" ", 1)
        if len(parts) < 2:
            raise UserInputError(user_message="📂 Нет контента для записи.")
    path = parts[0].strip()
    content = parts[1]
    if not path.startswith("/"):
        path = os.path.join(config.BASE_DIR, path)
    result = await mcp_manager.write_file(path, content)
    await message.reply(result)


async def handle_status(bot: "KraabUserbot", message: Message) -> None:
    """Статус системы и ресурсов."""
    ram = model_manager.get_ram_usage()
    is_ok = await openclaw_client.health_check()
    bar = "▓" * int(ram["percent"] / 10) + "░" * (10 - int(ram["percent"] / 10))
    text = f"""
🦀 **Системный статус Краба**
---------------------------
📡 **Gateway (OpenClaw):** {"✅ Online" if is_ok else "❌ Offline"}
🧠 **Модель:** `{config.MODEL}`
🎭 **Роль:** `{bot.current_role}`
🎙️ **Голос:** `{"ВКЛ" if bot.voice_mode else "ВЫКЛ"}`
💻 **RAM:** [{bar}] {ram["percent"]}%
"""
    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(text)
    else:
        await message.reply(text)


async def handle_model(bot: "KraabUserbot", message: Message) -> None:
    """Управление маршрутизацией и загрузкой AI моделей."""
    args = message.text.split()
    sub = args[1].lower() if len(args) > 1 else ""

    async def _is_local_model(model_id: str) -> bool:
        """Определяет, относится ли model_id к локальным моделям LM Studio."""
        normalized = str(model_id or "").strip().lower()
        if normalized in {"local", "lmstudio/local"} or normalized.startswith("lmstudio/"):
            return True
        try:
            models = await model_manager.discover_models()
            return any(
                m.id == model_id and m.type.name.startswith("LOCAL")
                for m in models
            )
        except Exception:
            # Если discovery недоступен, используем безопасную эвристику.
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
            "_Подкоманды: `local`, `cloud`, `auto`, `set <model_id>`, `load <name>`, `unload`, `scan`_"
        )
        await message.reply(text)
        return

    if sub == "local":
        # Фиксируем режим в .env, чтобы он не слетал после рестартов runtime.
        config.update_setting("FORCE_CLOUD", "0")
        config.FORCE_CLOUD = False
        await message.reply("💻 Режим: **local** — используется локальная модель (LM Studio).")
        return

    if sub == "cloud":
        # Фиксируем режим в .env, чтобы cloud оставался активным после перезапуска.
        config.update_setting("FORCE_CLOUD", "1")
        config.FORCE_CLOUD = True
        await message.reply(f"☁️ Режим: **cloud** — используется `{config.MODEL}`.")
        return

    if sub == "auto":
        # Auto = не форсить cloud, отдаём выбор роутеру.
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

    if sub in ("scan", "list"):
        msg = await message.reply("🔍 Сканирую доступные модели...")
        try:
            models = await model_manager.discover_models()
            from ..core.cloud_gateway import get_cloud_fallback_chain
            cloud_ids = [c for c in get_cloud_fallback_chain() if "gemini" in c.lower()]
            local_models = [m for m in models if m.type.name.startswith("LOCAL")]
            cloud_from_api = [m for m in models if m.type.name.startswith("CLOUD")]
            cloud_seen = {m.id for m in cloud_from_api}
            for cid in cloud_ids:
                if cid not in cloud_seen:
                    from ..core.model_types import ModelInfo, ModelStatus, ModelType
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
            lines = [f"🔍 **Доступные модели** (local={len(local_models)}, cloud={len(cloud_from_api)})\n", "☁️ **Облачные**\n"]
            for m in sorted(cloud_from_api, key=lambda x: x.id):
                loaded = " ✅" if m.id == model_manager._current_model else ""
                lines.append(f"☁️ `{m.id}` · `{_format_size_gb(getattr(m, 'size_gb', 0.0))}`{loaded}")
            lines.append("\n💻 **Локальные**\n")
            for m in sorted(local_models, key=lambda x: x.id):
                loaded = " ✅" if m.id == model_manager._current_model else ""
                lines.append(f"💻 `{m.id}` · `{_format_size_gb(getattr(m, 'size_gb', 0.0))}`{loaded}")
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
            "Доступные: `local`, `cloud`, `auto`, `set`, `load`, `unload`, `scan`"
        )
    )


async def handle_clear(bot: "KraabUserbot", message: Message) -> None:
    """Очистка истории диалога."""
    openclaw_client.clear_session(str(message.chat.id))
    res = "🧹 **Память очищена. Клешни как новые!**"
    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(res)
    else:
        await message.reply(res)


async def handle_config(bot: "KraabUserbot", message: Message) -> None:
    """Просмотр текущих настроек."""
    text = f"""
⚙️ **Конфигурация Краба**
----------------------
👤 **Владелец:** `{config.OWNER_USERNAME}`
🎯 **Триггеры:** `{", ".join(config.TRIGGER_PREFIXES)}`
🧠 **Память (RAM):** `{config.MAX_RAM_GB}GB`
"""
    await message.reply(text)


async def handle_set(bot: "KraabUserbot", message: Message) -> None:
    """Изменение настроек на лету."""
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        raise UserInputError(user_message="⚙️ `!set <KEY> <VAL>`")
    key = str(args[1] or "").upper()
    if config.update_setting(key, args[2]):
        extra = ""
        if key == "SCHEDULER_ENABLED" and hasattr(bot, "_sync_scheduler_runtime"):
            try:
                bot._sync_scheduler_runtime()
                state = "ON" if bool(getattr(config, "SCHEDULER_ENABLED", False)) else "OFF"
                extra = f"\n⏰ Scheduler runtime: `{state}`"
            except Exception as exc:  # noqa: BLE001
                extra = f"\n⚠️ Scheduler sync warning: `{str(exc)[:120]}`"
        await message.reply(f"✅ `{key}` обновлено!{extra}")
    else:
        await message.reply("❌ Ошибка обновления.")


async def handle_acl(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление runtime ACL userbot.

    Доступно только owner-контуру.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(
            user_message=(
                "🔒 Управление ACL доступно только владельцу.\n"
                "Можно попросить владельца выдать full или partial доступ."
            )
        )

    raw_args = bot._get_command_args(message).strip()
    parts = raw_args.split()
    action = str(parts[0] or "status").strip().lower() if parts else "status"
    state = load_acl_runtime_state()

    def _render_state() -> str:
        full_items = state.get(AccessLevel.FULL.value, [])
        partial_items = state.get(AccessLevel.PARTIAL.value, [])
        owner_items = state.get(AccessLevel.OWNER.value, [])
        return (
            "🛂 **Runtime ACL userbot**\n"
            "-----------------------\n"
            f"- Файл: `{config.USERBOT_ACL_FILE}`\n"
            f"- Владелец (config): `{config.OWNER_USERNAME}`\n"
            f"- Owner в runtime-файле: `{', '.join(owner_items) if owner_items else '-'}`\n"
            f"- Full: `{', '.join(full_items) if full_items else '-'}`\n"
            f"- Partial: `{', '.join(partial_items) if partial_items else '-'}`\n"
            f"- Partial-команды: `{', '.join(sorted(PARTIAL_ACCESS_COMMANDS))}`\n\n"
            "Команды:\n"
            "- `!acl status`\n"
            "- `!acl grant full @username`\n"
            "- `!acl grant partial @username`\n"
            "- `!acl revoke full @username`\n"
            "- `!acl revoke partial @username`\n"
            "- `!acl list`"
        )

    if action in {"", "status", "list"}:
        await message.reply(_render_state())
        return

    if action not in {"grant", "revoke"}:
        raise UserInputError(
            user_message=(
                "❌ Неизвестное действие ACL.\n"
                "Используй: `status`, `list`, `grant`, `revoke`."
            )
        )

    if len(parts) < 3:
        raise UserInputError(
            user_message=(
                "❌ Формат ACL-команды:\n"
                "- `!acl grant full @username`\n"
                "- `!acl grant partial 123456789`\n"
                "- `!acl revoke full @username`"
            )
        )

    level = str(parts[1] or "").strip().lower()
    subject = str(parts[2] or "").strip()
    if level not in {AccessLevel.FULL.value, AccessLevel.PARTIAL.value}:
        raise UserInputError(
            user_message="❌ Можно изменять только уровни `full` и `partial`."
        )

    result = update_acl_subject(level, subject, add=(action == "grant"))
    state = result["state"]
    verb = "выдан" if action == "grant" else "снят"
    changed_note = "обновлено" if result["changed"] else "без изменений"
    await message.reply(
        "✅ ACL обновлён.\n"
        f"- Уровень: `{level}`\n"
        f"- Subject: `{result['subject']}`\n"
        f"- Результат: `{verb}` / {changed_note}\n"
        f"- Full: `{', '.join(state.get('full', [])) if state.get('full') else '-'}`\n"
        f"- Partial: `{', '.join(state.get('partial', [])) if state.get('partial') else '-'}`"
    )


async def handle_role(bot: "KraabUserbot", message: Message) -> None:
    """Смена системного промпта (личности)."""
    args = message.text.split()
    if len(args) < 2 or args[1] == "list":
        await message.reply(f"🎭 **Роли:**\n{list_roles()}")
    else:
        role = args[1] if len(args) == 2 else args[2]
        if role in ROLES:
            bot.current_role = role
            await message.reply(f"🎭 Теперь я: `{role}`")
        else:
            raise UserInputError(user_message="❌ Роль не найдена.")


async def handle_voice(bot: "KraabUserbot", message: Message) -> None:
    """Переключение голосовых ответов."""
    bot.voice_mode = not bot.voice_mode
    await message.reply(f"🎙️ Голосовой режим: `{'ВКЛ' if bot.voice_mode else 'ВЫКЛ'}`")


async def handle_tech(bot: "KraabUserbot", message: Message) -> None:
    """
    Управляет owner/debug техническими хвостами в ответах userbot.

    Режимы:
    - `!tech` / `!tech status` — показать текущие флаги;
    - `!tech on` — включить factual тех-заметки, но оставить UX чистым;
    - `!tech off` / `!tech clean` — вернуть обычный чистый режим;
    - `!tech verbose` — включить тех-заметки и не скрывать сырой tool-шум.
    """
    access_profile = bot._get_access_profile(message.from_user) if message.from_user else None
    if not access_profile or access_profile.level is not AccessLevel.OWNER:
        raise UserInputError(user_message="🛠️ `!tech` доступна только владельцу.")

    raw_args = str(message.text or "").split(maxsplit=2)
    action = raw_args[1].strip().lower() if len(raw_args) > 1 else "status"

    def _bool_to_env(value: bool) -> str:
        return "1" if value else "0"

    def _apply_tech_flags(*, notices_enabled_value: bool, suppress_noise_value: bool) -> bool:
        """
        Сохраняет связанные debug-флаги без partial drift между runtime и `.env`.

        Если вторая запись падает, откатываем уже применённую первую, чтобы owner
        не получил ложный success-ответ при полурабочем состоянии.
        """
        previous_values = {
            "USERBOT_TECH_NOTICES_ENABLED": _bool_to_env(
                bool(getattr(config, "USERBOT_TECH_NOTICES_ENABLED", False))
            ),
            "USERBOT_SUPPRESS_NON_ACTIONABLE_TOOL_WARNINGS": _bool_to_env(
                bool(getattr(config, "USERBOT_SUPPRESS_NON_ACTIONABLE_TOOL_WARNINGS", True))
            ),
        }
        target_values = [
            ("USERBOT_TECH_NOTICES_ENABLED", _bool_to_env(notices_enabled_value)),
            ("USERBOT_SUPPRESS_NON_ACTIONABLE_TOOL_WARNINGS", _bool_to_env(suppress_noise_value)),
        ]
        applied_keys: list[str] = []
        for key, value in target_values:
            if not config.update_setting(key, value):
                for applied_key in reversed(applied_keys):
                    config.update_setting(applied_key, previous_values[applied_key])
                return False
            applied_keys.append(key)
        return True

    if action == "status":
        notices_enabled = bool(getattr(config, "USERBOT_TECH_NOTICES_ENABLED", False))
        suppress_noise = bool(getattr(config, "USERBOT_SUPPRESS_NON_ACTIONABLE_TOOL_WARNINGS", True))
        effective_mode = "verbose" if notices_enabled and not suppress_noise else (
            "on" if notices_enabled else "clean"
        )
        await message.reply(
            "🛠️ **Tech notices**\n"
            f"- mode: `{effective_mode}`\n"
            f"- notices_enabled: `{notices_enabled}`\n"
            f"- suppress_non_actionable_tool_warnings: `{suppress_noise}`\n\n"
            "Команды:\n"
            "`!tech on` — включить factual тех-заметки\n"
            "`!tech off` / `!tech clean` — убрать тех-хвосты\n"
            "`!tech verbose` — показать тех-заметки и не скрывать сырой tool-шум"
        )
        return

    if action == "on":
        if not _apply_tech_flags(notices_enabled_value=True, suppress_noise_value=True):
            await message.reply(
                "❌ Не удалось включить tech notices без drift.\n"
                "- Изменения откатил.\n"
                "- Проверь доступность записи в `.env` и попробуй ещё раз."
            )
            return
        await message.reply(
            "🛠️ Tech notices включены.\n"
            "- В конце ответов будет factual тех-плашка о route/model/provider.\n"
            "- Шумные хвосты вроде `Message failed` останутся скрыты."
        )
        return

    if action in {"off", "clean"}:
        if not _apply_tech_flags(notices_enabled_value=False, suppress_noise_value=True):
            await message.reply(
                "❌ Не удалось выключить tech notices без drift.\n"
                "- Изменения откатил.\n"
                "- Проверь доступность записи в `.env` и попробуй ещё раз."
            )
            return
        await message.reply(
            "🛠️ Tech notices выключены.\n"
            "- Ответы снова чистые, без тех-плашек.\n"
            "- Шумные tool-хвосты продолжат скрываться."
        )
        return

    if action == "verbose":
        if not _apply_tech_flags(notices_enabled_value=True, suppress_noise_value=False):
            await message.reply(
                "❌ Не удалось включить verbose debug без drift.\n"
                "- Изменения откатил.\n"
                "- Проверь доступность записи в `.env` и попробуй ещё раз."
            )
            return
        await message.reply(
            "🛠️ Verbose debug включён.\n"
            "- Тех-плашки добавляются в ответы.\n"
            "- Сырой tool-шум тоже сохраняется для отладки."
        )
        return

    raise UserInputError(user_message="🛠️ Формат: `!tech status|on|off|clean|verbose`")


async def handle_web(bot: "KraabUserbot", message: Message) -> None:
    """Автоматизация браузера."""
    from ..web_session import web_manager

    args = message.text.split()
    if len(args) < 2:
        from urllib.parse import quote

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


async def handle_sysinfo(bot: "KraabUserbot", message: Message) -> None:
    """Расширенная информация о хосте."""
    import platform

    import psutil

    text = f"🖥️ **System:** `{platform.system()}`\n🔥 **CPU:** `{psutil.cpu_percent()}%`"
    await message.reply(text)


async def handle_panel(bot: "KraabUserbot", message: Message) -> None:
    """Графическая панель управления."""
    await handle_status(bot, message)


async def handle_macos(bot: "KraabUserbot", message: Message) -> None:
    """
    Базовое управление macOS из owner/full-контура.

    Держим здесь только понятные и контролируемые действия:
    clipboard, уведомления, активные приложения, `open` и Finder reveal.
    Это даёт реальную пользу уже сейчас и служит фундаментом для следующего
    этапа с Calendar/Reminders/Notes.
    """
    del bot
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
        )
        await message.reply("\n".join(lines))
        return

    if sub == "clip":
        if len(parts) < 2:
            raise UserInputError(user_message="🍎 Формат: `!mac clip get` или `!mac clip set <текст>`")
        clip_action = parts[1].strip().lower()
        if clip_action == "get":
            text = await macos_automation.get_clipboard_text()
            await message.reply(f"📋 **Clipboard**\n{text if text else '(clipboard пуст)'}")
            return
        if clip_action == "set":
            text = parts[2].strip() if len(parts) > 2 else ""
            if not text:
                raise UserInputError(user_message="🍎 Формат: `!mac clip set <текст>`")
            await macos_automation.set_clipboard_text(text)
            await message.reply("📋 Clipboard обновлён.")
            return
        raise UserInputError(user_message="🍎 Формат: `!mac clip get` или `!mac clip set <текст>`")

    if sub == "notify":
        payload = args[len("notify"):].strip()
        if not payload:
            raise UserInputError(user_message="🍎 Формат: `!mac notify <текст>`")
        title = "Краб"
        body = payload
        if "|" in payload:
            title_part, body_part = payload.split("|", 1)
            title = title_part.strip() or "Краб"
            body = body_part.strip()
        await macos_automation.show_notification(title=title, message=body)
        await message.reply("🔔 Уведомление отправлено.")
        return

    if sub == "app":
        if len(parts) < 2:
            raise UserInputError(user_message="🍎 Формат: `!mac app front|list|open <имя>`")
        app_action = parts[1].strip().lower()
        if app_action == "front":
            info = await macos_automation.get_frontmost_app()
            await message.reply(
                "🪟 **Переднее приложение**\n"
                f"- app: `{info.get('app_name') or 'n/a'}`\n"
                f"- window: `{info.get('window_title') or 'n/a'}`"
            )
            return
        if app_action == "list":
            apps = await macos_automation.list_running_apps()
            if not apps:
                await message.reply("🪟 Не удалось получить список приложений.")
                return
            await message.reply("🪟 **Приложения**\n" + "\n".join(f"- `{item}`" for item in apps))
            return
        if app_action == "open":
            app_name = parts[2].strip() if len(parts) > 2 else ""
            if not app_name:
                raise UserInputError(user_message="🍎 Формат: `!mac app open <имя>`")
            opened = await macos_automation.open_app(app_name)
            await message.reply(f"🪟 Открываю `{opened}`.")
            return
        raise UserInputError(user_message="🍎 Формат: `!mac app front|list|open <имя>`")

    if sub == "open":
        target = args[len("open"):].strip()
        if not target:
            raise UserInputError(user_message="🍎 Формат: `!mac open <url|path>`")
        opened = await macos_automation.open_target(target)
        await message.reply(f"🌐 Открываю {opened.get('kind')}: `{opened.get('target')}`")
        return

    if sub == "finder":
        if len(parts) < 3 or parts[1].strip().lower() != "reveal":
            raise UserInputError(user_message="🍎 Формат: `!mac finder reveal <path>`")
        target = parts[2].strip()
        revealed = await macos_automation.reveal_in_finder(target)
        await message.reply(f"📁 Показываю в Finder: `{revealed}`")
        return

    raise UserInputError(user_message="🍎 Неизвестная команда. Напиши `!mac` для справки.")


async def handle_restart(bot: "KraabUserbot", message: Message) -> None:
    """Мягкая перезагрузка процесса."""
    await message.reply("🔄 Перезапускаюсь...")
    sys.exit(42)


async def handle_agent(bot: "KraabUserbot", message: Message) -> None:
    """Управление агентами: !agent new <name> <prompt>."""
    text = bot._get_command_args(message)
    if not text:
        raise UserInputError(
            user_message=(
                "🕵️‍♂️ Использование:\n"
                "- `!agent new <имя> <промпт>`\n"
                "- `!agent list`\n"
                "- `!agent swarm <тема>`\n"
                "- `!agent swarm loop [N] <тема>`"
            )
        )
    if text.startswith("list"):
        await message.reply(f"🕵️‍♂️ **Доступные агенты:**\n\n{list_roles()}")
        return
    if text.startswith("swarm"):
        swarm_args = text[5:].strip()
        if not swarm_args:
            raise UserInputError(user_message="🐝 Формат: `!agent swarm <тема>`")

        topic = swarm_args
        is_loop = False
        loop_rounds = 2
        if swarm_args.startswith("loop"):
            is_loop = True
            loop_payload = swarm_args[4:].strip()
            if not loop_payload:
                raise UserInputError(
                    user_message="🐝 Формат: `!agent swarm loop [N] <тема>`"
                )
            first, *rest = loop_payload.split(" ", 1)
            if first.isdigit():
                loop_rounds = int(first)
                topic = rest[0].strip() if rest else ""
            else:
                topic = loop_payload
            if not topic:
                raise UserInputError(
                    user_message="🐝 Формат: `!agent swarm loop [N] <тема>`"
                )

        max_rounds = int(getattr(config, "SWARM_LOOP_MAX_ROUNDS", 3) or 3)
        next_round_clip = int(getattr(config, "SWARM_LOOP_NEXT_ROUND_CLIP", 4000) or 4000)
        safe_rounds = max(1, min(loop_rounds, max_rounds))

        if is_loop:
            status = await message.reply(
                f"🐝 Запускаю роевой loop: {safe_rounds} раунд(а), роли аналитик → критик → интегратор..."
            )
        else:
            status = await message.reply("🐝 Запускаю роевой раунд: аналитик → критик → интегратор...")
        room = AgentRoom()
        role_prompt = get_role_prompt(getattr(bot, "current_role", "default"))
        room_chat_id = f"swarm:{message.chat.id}"
        router = _AgentRoomRouterAdapter(
            chat_id=room_chat_id,
            system_prompt=role_prompt,
        )
        if is_loop:
            result = await room.run_loop(
                topic,
                router,
                rounds=safe_rounds,
                max_rounds=max_rounds,
                next_round_clip=next_round_clip,
            )
        else:
            result = await room.run_round(topic, router)
        chunks = _split_text_for_telegram(result)
        await status.edit(chunks[0])
        for part in chunks[1:]:
            await message.reply(part)
        return
    if text.startswith("new"):
        parts = text[3:].strip().split(" ", 1)
        if len(parts) < 2:
            raise UserInputError(user_message="❌ Ошибка: укажите имя и промпт.")
        name = parts[0].strip()
        prompt = parts[1].strip().strip('"').strip("'")
        if save_role(name, prompt):
            await message.reply(
                f"🕵️‍♂️ **Агент создан:** `{name}`\n\nТеперь можно использовать: `стань {name}`"
            )
        else:
            await message.reply("❌ Ошибка при сохранении агента.")


async def handle_help(bot: "KraabUserbot", message: Message) -> None:
    """Справка по командам (v7.2 categories)."""
    text = """🦀 **Команды Краба**

**Core**
`!status` — статус системы
`!clear` — очистить историю диалога
`!config` — текущие настройки
`!set <KEY> <VAL>` — изменить настройку
`!restart` — перезапуск бота
`!help` — эта справка

**AI / Model**
`!model` — статус маршрутизации
`!model local` — принудительно локальная модель
`!model cloud` — принудительно облачная модель
`!model auto` — автоматический выбор
`!model set <model_id>` — выбрать конкретную модель (из `!model scan`)
`!model load <name>` — загрузить модель
`!model unload` — выгрузить модель
`!model scan` — список доступных моделей

**Tools**
`!search <query>` — веб-поиск
`!remember <text>` — запомнить факт
`!recall <query>` — вспомнить факт
`!acl ...` / `!access ...` — управление full/partial доступом (owner-only)
`!role [name|list]` — смена личности
`!remind <время> | <текст>` — поставить напоминание
`!reminders` — список активных напоминаний
`!rm_remind <id>` — удалить напоминание
`!cronstatus` — статус scheduler
`!inbox [list|status|ack|done|cancel|approve|reject|task|approval]` — owner-visible inbox / escalation

**System**
`!ls [path]` — список файлов
`!read <path>` — чтение файла
`!write <file> <content>` — запись файла
`!sysinfo` — информация о хосте
`!diagnose` — диагностика подключений

**Dev**
`!agent new <name> <prompt>` — создать агента
`!agent list` — список агентов
`!agent swarm <тема>` — роевой раунд (аналитик/критик/интегратор)
`!agent swarm loop [N] <тема>` — несколько роевых раундов (итеративная доработка)
`!voice` — голосовой режим
`!tech status|on|off|clean|verbose` — owner/debug тех-заметки в ответах
`!web` — управление браузером
`!panel` — панель управления (soon)
"""
    await message.reply(text)


async def handle_diagnose(bot: "KraabUserbot", message: Message) -> None:
    """Диагностика системы (!diagnose)."""
    msg = await message.reply("🏥 **Запускаю диагностику системы...**")
    report = []
    report.append("**Config:**")
    report.append(f"- OPENCLAW_URL: `{config.OPENCLAW_URL}`")
    report.append(f"- LM_STUDIO_URL: `{config.LM_STUDIO_URL}`")
    if await is_lm_studio_available(config.LM_STUDIO_URL, timeout=2.0):
        report.append("- LM Studio: ✅ OK (Available)")
    else:
        report.append("- LM Studio: ❌ Offline")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{config.OPENCLAW_URL}/health")
            if resp.status_code == 200:
                report.append("- OpenClaw: ✅ OK (Healthy)")
            else:
                report.append(f"- OpenClaw: ⚠️ Error ({resp.status_code})")
    except (httpx.RequestError, httpx.ConnectError, httpx.TimeoutException, OSError) as e:
        report.append(f"- OpenClaw: ❌ Unreachable ({str(e)})")
        report.append("  _Совет: Проверьте, запущен ли Gateway и совпадает ли порт (обычно 18792)_")
    await msg.edit("\n".join(report))


async def handle_remind(bot: "KraabUserbot", message: Message) -> None:
    """
    Добавляет reminder-задачу в runtime scheduler.

    Форматы:
    - `!remind 10m | купить воду`
    - `!remind через 20 минут проверить почту`
    - `!remind в 18:30 созвон`
    """
    if not bool(getattr(config, "SCHEDULER_ENABLED", False)):
        raise UserInputError(
            user_message=(
                "⏰ Scheduler сейчас выключен (`SCHEDULER_ENABLED=0`).\n"
                "Включи его (`!set SCHEDULER_ENABLED 1`) и перезапусти Krab."
            )
        )

    raw_args = bot._get_command_args(message)
    if not raw_args:
        raise UserInputError(
            user_message=(
                "⏰ Формат:\n"
                "`!remind <время> | <текст>`\n\n"
                "Примеры:\n"
                "- `!remind 10m | выпить воды`\n"
                "- `!remind через 20 минут проверить почту`\n"
                "- `!remind в 18:30 созвон`"
            )
        )

    time_spec, reminder_text = split_reminder_input(raw_args)
    if not time_spec or not reminder_text:
        raise UserInputError(
            user_message=(
                "⏰ Не удалось разобрать время/текст.\n"
                "Используй формат: `!remind <время> | <текст>`"
            )
        )

    try:
        due_at = parse_due_time(time_spec)
    except ValueError:
        raise UserInputError(
            user_message=(
                "❌ Не удалось распознать время.\n"
                "Поддерживается: `10m`, `через 20 минут`, `в 18:30`, `2026-03-05 09:00`."
            )
        )

    if hasattr(bot, "_sync_scheduler_runtime"):
        try:
            bot._sync_scheduler_runtime()
        except Exception as exc:  # noqa: BLE001
            logger.warning("scheduler_runtime_sync_failed_in_remind", error=str(exc))

    if not krab_scheduler.is_started:
        try:
            krab_scheduler.start()
        except RuntimeError:
            raise UserInputError(user_message="❌ Scheduler не запущен в runtime loop.")

    reminder_id = krab_scheduler.add_reminder(
        chat_id=str(message.chat.id),
        text=reminder_text,
        due_at=due_at,
    )
    due_label = due_at.astimezone().strftime("%d.%m.%Y %H:%M")
    await message.reply(
        "✅ Напоминание создано.\n"
        f"- ID: `{reminder_id}`\n"
        f"- Когда: `{due_label}`\n"
        f"- Текст: {reminder_text}"
    )


async def handle_reminders(bot: "KraabUserbot", message: Message) -> None:
    """Показывает pending reminders текущего чата."""
    rows = krab_scheduler.list_reminders(chat_id=str(message.chat.id))
    if not rows:
        await message.reply("⏰ Активных напоминаний нет.")
        return
    lines = ["⏰ **Активные напоминания:**"]
    for item in rows:
        due = str(item.get("due_at_iso") or "")
        text = str(item.get("text") or "")
        rid = str(item.get("reminder_id") or "")
        lines.append(f"- `{rid}` · `{due}` · {text}")
    payload = "\n".join(lines)
    chunks = _split_text_for_telegram(payload, limit=3600)
    await message.reply(chunks[0])
    for part in chunks[1:]:
        await message.reply(part)


async def handle_rm_remind(bot: "KraabUserbot", message: Message) -> None:
    """Удаляет reminder по ID."""
    raw_args = bot._get_command_args(message).strip()
    if not raw_args:
        raise UserInputError(user_message="🗑️ Формат: `!rm_remind <id>`")
    ok = krab_scheduler.remove_reminder(raw_args)
    if ok:
        await message.reply(f"🗑️ Напоминание `{raw_args}` удалено.")
    else:
        await message.reply(f"⚠️ Напоминание `{raw_args}` не найдено.")


async def handle_cronstatus(bot: "KraabUserbot", message: Message) -> None:
    """Отдает runtime-статус scheduler."""
    status = krab_scheduler.get_status()
    await message.reply(
        "🧭 **Scheduler status**\n"
        f"- enabled (config): `{status.get('scheduler_enabled')}`\n"
        f"- started: `{status.get('started')}`\n"
        f"- pending: `{status.get('pending_count')}`\n"
        f"- next_due_at: `{status.get('next_due_at') or '-'}`\n"
        f"- storage: `{status.get('storage_path')}`"
    )


async def handle_watch(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление proactive watch контуром.

    Команды:
    - `!watch status` — persisted состояние фонового watch;
    - `!watch now` — принудительно снять digest и записать его в общую память.
    """
    del bot
    raw_args = str(message.text or "").split(maxsplit=2)
    action = raw_args[1].strip().lower() if len(raw_args) > 1 else "status"

    if action == "status":
        status = proactive_watch.get_status()
        snapshot = status.get("last_snapshot") or {}
        route_model = str(snapshot.get("route_model") or snapshot.get("primary_model") or "n/a")
        await message.reply(
            "🛰️ **Proactive Watch**\n"
            f"- enabled: `{status.get('enabled')}`\n"
            f"- interval_sec: `{status.get('interval_sec')}`\n"
            f"- alert_cooldown_sec: `{status.get('alert_cooldown_sec')}`\n"
            f"- last_reason: `{status.get('last_reason') or '-'}`\n"
            f"- last_digest_ts: `{status.get('last_digest_ts') or '-'}`\n"
            f"- last_alert_ts: `{status.get('last_alert_ts') or '-'}`\n"
            f"- last_model: `{route_model}`"
        )
        return

    if action == "now":
        result = await proactive_watch.capture(manual=True, persist_memory=True, notify=False)
        suffix = "\n- Память: записано в workspace memory" if result.get("wrote_memory") else "\n- Память: запись пропущена"
        await message.reply(str(result.get("digest") or "watch digest unavailable") + suffix)
        return

    raise UserInputError(user_message="🛰️ Формат: `!watch status` или `!watch now`")


async def handle_memory(bot: "KraabUserbot", message: Message) -> None:
    """
    Короткий просмотр общей памяти OpenClaw без поиска по словам.

    Пока сознательно ограничиваемся read-only режимом:
    - `!remember` уже отвечает за запись фактов;
    - эта команда нужна для последних записей и owner-digest слоёв.
    """
    del bot
    raw_args = str(message.text or "").split(maxsplit=2)
    action = raw_args[1].strip().lower() if len(raw_args) > 1 else "recent"
    source_filter = raw_args[2].strip() if len(raw_args) > 2 else ""

    if action != "recent":
        raise UserInputError(user_message="🧠 Формат: `!memory recent [source_filter]`")

    rows = list_workspace_memory_entries(limit=8, source_filter=source_filter)
    if not rows:
        await message.reply("🧠 В общей памяти пока нет подходящих записей.")
        return
    lines = ["🧠 **Последние записи общей памяти**"]
    for item in rows:
        author_suffix = f":{item['author']}" if item.get("author") else ""
        lines.append(
            f"- `{item['date']} {item['time']}` [{item['source']}{author_suffix}] {item['text']}"
        )
    await message.reply("\n".join(lines))


async def handle_inbox(bot: "KraabUserbot", message: Message) -> None:
    """
    Owner-visible inbox и escalation foundation.

    Поддерживаем owner workflow-подмножество:
    - `!inbox` / `!inbox list` — открыть текущие open items;
    - `!inbox status` — краткий summary;
    - `!inbox ack <id>` — отметить как просмотренное;
    - `!inbox done <id>` — закрыть item;
    - `!inbox cancel <id>` — отменить item вручную.
    - `!inbox approve <id>` / `!inbox reject <id>` — принять решение по approval item;
    - `!inbox task <title> | <body>` — создать owner-task;
    - `!inbox taskfrom <source_id> | <title> | <body>` — эскалировать item в owner-task;
    - `!inbox approval <scope> | <title> | <body>` — создать approval-request.
    - `!inbox approvalfrom <source_id> | <scope> | <title> | <body>` — эскалировать item в approval.
    """
    del bot
    raw_args = str(message.text or "").split(maxsplit=2)
    action = raw_args[1].strip().lower() if len(raw_args) > 1 else "list"

    if action == "status":
        summary = inbox_service.get_summary()
        await message.reply(
            "📥 **Inbox / Escalation**\n"
            f"- operator: `{summary.get('operator_id')}`\n"
            f"- account_id: `{summary.get('account_id')}`\n"
            f"- open_items: `{summary.get('open_items')}`\n"
            f"- attention_items: `{summary.get('attention_items')}`\n"
            f"- pending_reminders: `{summary.get('pending_reminders')}`\n"
            f"- open_escalations: `{summary.get('open_escalations')}`\n"
            f"- pending_owner_tasks: `{summary.get('pending_owner_tasks')}`\n"
            f"- pending_approvals: `{summary.get('pending_approvals')}`\n"
            f"- pending_owner_requests: `{summary.get('pending_owner_requests')}`\n"
            f"- pending_owner_mentions: `{summary.get('pending_owner_mentions')}`\n"
            f"- state: `{summary.get('state_path')}`"
        )
        return

    if action in {"list", "open"}:
        rows = inbox_service.list_items(status="open", limit=8)
        if not rows:
            await message.reply("📥 Inbox сейчас пуст: открытых items нет.")
            return
        lines = ["📥 **Открытые inbox items**"]
        for item in rows:
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            due = str(meta.get("due_at_iso") or "").strip()
            due_suffix = f" · due `{due}`" if due else ""
            approval_scope = str((item.get("identity") or {}).get("approval_scope") or "").strip()
            approval_suffix = f" · scope `{approval_scope}`" if approval_scope and item["kind"] == "approval_request" else ""
            lines.append(
                f"- `{item['item_id']}` · `{item['kind']}` · `{item['severity']}`{due_suffix}{approval_suffix}\n"
                f"  {item['title']}"
            )
        await message.reply("\n".join(lines))
        return

    if action == "task":
        if len(raw_args) < 3 or "|" not in raw_args[2]:
            raise UserInputError(user_message="📥 Формат: `!inbox task <title> | <body>`")
        title, body = [part.strip() for part in raw_args[2].split("|", maxsplit=1)]
        if not title or not body:
            raise UserInputError(user_message="📥 Для task нужны и заголовок, и описание.")
        created = inbox_service.upsert_owner_task(title=title, body=body, source="telegram-owner")
        await message.reply(
            "📝 Owner-task создан.\n"
            f"- ID: `{created['item']['item_id']}`\n"
            f"- Title: {created['item']['title']}"
        )
        return

    if action == "taskfrom":
        if len(raw_args) < 3 or raw_args[2].count("|") < 2:
            raise UserInputError(user_message="📥 Формат: `!inbox taskfrom <source_id> | <title> | <body>`")
        source_item_id, title, body = [part.strip() for part in raw_args[2].split("|", maxsplit=2)]
        if not source_item_id or not title or not body:
            raise UserInputError(user_message="📥 Для taskfrom нужны source_id, заголовок и описание.")
        created = inbox_service.escalate_item_to_owner_task(
            source_item_id=source_item_id,
            title=title,
            body=body,
            source="telegram-owner",
            metadata={"requested_via": "telegram"},
        )
        if not created.get("ok"):
            raise UserInputError(user_message=f"📥 Source item `{source_item_id}` не найден.")
        await message.reply(
            "📝 Owner-task создан из inbox item.\n"
            f"- Source: `{source_item_id}`\n"
            f"- ID: `{created['item']['item_id']}`\n"
            f"- Trace: `{created['item']['identity']['trace_id']}`"
        )
        return

    if action == "approval":
        if len(raw_args) < 3 or raw_args[2].count("|") < 2:
            raise UserInputError(user_message="📥 Формат: `!inbox approval <scope> | <title> | <body>`")
        scope, title, body = [part.strip() for part in raw_args[2].split("|", maxsplit=2)]
        if not scope or not title or not body:
            raise UserInputError(user_message="📥 Для approval нужны scope, заголовок и описание.")
        created = inbox_service.upsert_approval_request(
            title=title,
            body=body,
            source="telegram-owner",
            approval_scope=scope,
            requested_action=title,
            metadata={"requested_via": "telegram"},
        )
        await message.reply(
            "🛂 Approval-request создан.\n"
            f"- ID: `{created['item']['item_id']}`\n"
            f"- Scope: `{scope}`\n"
            f"- Title: {created['item']['title']}"
        )
        return

    if action == "approvalfrom":
        if len(raw_args) < 3 or raw_args[2].count("|") < 3:
            raise UserInputError(user_message="📥 Формат: `!inbox approvalfrom <source_id> | <scope> | <title> | <body>`")
        source_item_id, scope, title, body = [part.strip() for part in raw_args[2].split("|", maxsplit=3)]
        if not source_item_id or not scope or not title or not body:
            raise UserInputError(user_message="📥 Для approvalfrom нужны source_id, scope, заголовок и описание.")
        created = inbox_service.escalate_item_to_approval_request(
            source_item_id=source_item_id,
            title=title,
            body=body,
            source="telegram-owner",
            approval_scope=scope,
            requested_action=title,
            metadata={"requested_via": "telegram"},
        )
        if not created.get("ok"):
            raise UserInputError(user_message=f"📥 Source item `{source_item_id}` не найден.")
        await message.reply(
            "🛂 Approval-request создан из inbox item.\n"
            f"- Source: `{source_item_id}`\n"
            f"- ID: `{created['item']['item_id']}`\n"
            f"- Scope: `{scope}`\n"
            f"- Trace: `{created['item']['identity']['trace_id']}`"
        )
        return

    if action not in {"ack", "done", "cancel", "approve", "reject"}:
        raise UserInputError(
            user_message=(
                "📥 Формат: "
                "`!inbox [list|status|ack <id>|done <id>|cancel <id>|approve <id>|reject <id>|task <title> | <body>|taskfrom <source_id> | <title> | <body>|approval <scope> | <title> | <body>|approvalfrom <source_id> | <scope> | <title> | <body>]`"
            )
        )

    if len(raw_args) < 3 or not raw_args[2].strip():
        raise UserInputError(user_message="📥 Укажи item id: `!inbox ack|done|cancel|approve|reject <id> [| note]`")
    target_payload = raw_args[2].strip()
    target_id, note = [part.strip() for part in target_payload.split("|", maxsplit=1)] if "|" in target_payload else (target_payload, "")
    if not target_id:
        raise UserInputError(user_message="📥 Укажи корректный item id: `!inbox ack|done|cancel|approve|reject <id> [| note]`")
    if action in {"approve", "reject"}:
        result = inbox_service.resolve_approval(
            target_id,
            approved=(action == "approve"),
            actor="telegram-owner",
            note=note,
        )
        target_status = "approved" if action == "approve" else "rejected"
    else:
        target_status = {"ack": "acked", "done": "done", "cancel": "cancelled"}[action]
        result = inbox_service.set_item_status(
            target_id,
            status=target_status,
            actor="telegram-owner",
            note=note,
        )
    if not result.get("ok"):
        if result.get("error") == "inbox_item_not_approval":
            raise UserInputError(user_message=f"📥 Item `{target_id}` не является approval-request.")
        raise UserInputError(user_message=f"📥 Item `{target_id}` не найден.")
    await message.reply(
        "✅ Inbox item обновлён.\n"
        f"- ID: `{target_id}`\n"
        f"- Новый статус: `{target_status}`"
        + (f"\n- Note: {note}" if note else "")
    )
