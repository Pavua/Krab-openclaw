# -*- coding: utf-8 -*-
"""
Runtime-status mixin для `KraabUserbot`.

Второй шаг декомпозиции `src/userbot_bridge.py` (session 4+, 2026-04-09).
Содержит детерминированные fast-path методы для ответа на вопросы о текущем
состоянии runtime: маршрут, модель, capability, команды, интеграции,
truthful self-check. Все данные берутся из живого runtime (model_manager,
openclaw_client, config), а не из LLM.

Замечания:
- `_current_runtime_primary_model` — module-level helper в userbot_bridge.py,
  импортируется лениво внутри тела метода, чтобы избежать циклических зависимостей.
- Class-level атрибуты (`self.router`, `self._startup_state`, etc.) остаются
  в `KraabUserbot` и доступны через MRO.

См. `docs/USERBOT_BRIDGE_SPLIT_PROPOSAL.md` для полной стратегии разбиения.
"""

from __future__ import annotations

from ..config import config
from ..core.access_control import (
    OWNER_ONLY_COMMANDS,
    AccessLevel,
    AccessProfile,
)
from ..core.capability_registry import resolve_access_mode
from ..core.scheduler import krab_scheduler
from ..integrations.macos_automation import macos_automation
from ..model_manager import model_manager
from ..openclaw_client import openclaw_client


class RuntimeStatusMixin:
    """
    Детерминированные runtime-status методы для fast-path ответов.

    Mixin для `KraabUserbot`: содержит _looks_like_*_question детекторы,
    _build_runtime_*_status генераторы и get_runtime_state. Все методы
    отдают фактические данные из живого runtime без вызова LLM.
    """

    def get_runtime_state(self) -> dict:
        """
        Возвращает runtime-состояние userbot для health/lite и handoff.
        """
        client_connected = bool(self.client and self.client.is_connected)
        me_username = getattr(self.me, "username", None) if self.me else None
        me_id = getattr(self.me, "id", None) if self.me else None
        return {
            "startup_state": self._startup_state,
            "startup_error_code": self._startup_error_code,
            "startup_error": self._startup_error,
            "client_connected": client_connected,
            "authorized_user": me_username,
            "authorized_user_id": me_id,
            "voice_profile": self.get_voice_runtime_profile(),
            "translator_profile": self.get_translator_runtime_profile(),
            "translator_session": self.get_translator_session_state(),
        }

    @staticmethod
    def _looks_like_model_status_question(text: str) -> bool:
        """Отключено по просьбе пользователя (все вопросы уходят в LLM)."""
        return False

        low = str(text or "").strip().lower()  # noqa: F811
        if not low:
            return False
        patterns = [
            "на какой модел",
            "какой моделью",
            "какая модель",
            "на чем работаешь",
            "через какую модель",
            "какой модель",
        ]
        return any(p in low for p in patterns)

    @staticmethod
    def _looks_like_capability_status_question(text: str) -> bool:
        """Отключено по просьбе пользователя (все вопросы уходят в LLM)."""
        return False

        low = str(text or "").strip().lower()  # noqa: F811
        if not low:
            return False
        patterns = [
            "что ты уме",
            "что уже уме",
            "что ты уже уме",
            "что ты ещё уме",
            "что ты еще уме",
            "что ты не уме",
            "что еще не уме",
            "что ещё не уме",
            "что уже можешь",
            "что можешь",
            "какие у тебя возможности",
            "что умеет краб",
            "что краб умеет",
        ]
        return any(pattern in low for pattern in patterns)

    @staticmethod
    def _looks_like_commands_question(text: str) -> bool:
        """Отключено по просьбе пользователя (все вопросы уходят в LLM)."""
        return False

        low = str(text or "").strip().lower()  # noqa: F811
        if not low:
            return False
        patterns = [
            "какие команды",
            "список команд",
            "что есть из команд",
            "какие у тебя команды",
            "что умеешь по командам",
            "какие у тебя есть команды",
            "что можно через команды",
        ]
        return any(pattern in low for pattern in patterns)

    @staticmethod
    def _looks_like_integrations_question(text: str) -> bool:
        """Отключено по просьбе пользователя (все вопросы уходят в LLM)."""
        return False

        low = str(text or "").strip().lower()  # noqa: F811
        if not low:
            return False
        patterns = [
            "какие интеграции",
            "что подключено",
            "какие инструменты",
            "какие сервисы",
            "какие mcp",
            "какие у тебя mcp",
            "какие у тебя интеграции",
            "чем ты подключен",
            "что у тебя подключено",
        ]
        return any(pattern in low for pattern in patterns)

    @staticmethod
    def _looks_like_runtime_truth_question(text: str) -> bool:
        """
        Отключено по просьбе пользователя (все вопросы уходят в LLM).
        """
        return False

        low = str(text or "").strip().lower()
        if not low:
            return False
        # Живой кейс из owner-чата: запросы вида "проведи полную диагностику"
        # раньше не попадали в truthful fast-path и уходили в свободную LLM-
        # генерацию, из-за чего пользователь видел мусор вроде "контекст потерян"
        # вместо реального self-check. Поэтому явно считаем диагностические
        # формулировки runtime-вопросом.
        patterns = [
            "проверка связи",
            "проверь связь",
            "что работает",
            "что у тебя работает",
            "что работает, а что нет",
            "проверь что работает",
            "проверь все",
            "проверь всё",
            "проведи диагностику",
            "полную диагностику",
            "диагностику рантайма",
            "диагностику runtime",
            "runtime self-check",
            "сделай self-check",
            "самопровер",
            "работает ли cron",
            "работает ли крон",
            "cron у тебя уже работает",
            "крон у тебя уже работает",
            "доступ к браузеру",
            "есть ли браузер",
            "можешь использовать браузер",
            "есть ли интернет",
            "доступ к интернету",
        ]
        return any(pattern in low for pattern in patterns)

    @staticmethod
    def _build_runtime_model_status(route: dict) -> str:
        """Формирует детерминированный статус маршрута по фактическим runtime-метаданным."""
        channel = str(route.get("channel", "unknown"))
        model = str(route.get("model", "unknown"))
        provider = str(route.get("provider", "unknown"))
        tier = str(route.get("active_tier", "-"))
        if channel == "local_direct":
            mode = "local_direct (LM Studio)"
        elif channel == "openclaw_local":
            mode = "openclaw_local"
        elif channel == "openclaw_cloud":
            mode = "openclaw_cloud"
        else:
            mode = channel
        return (
            "🧭 Фактический runtime-маршрут:\n"
            f"- Канал: `{mode}`\n"
            f"- Модель: `{model}`\n"
            f"- Провайдер: `{provider}`\n"
            f"- Cloud tier: `{tier}`"
        )

    @staticmethod
    def _resolve_runtime_access_mode(
        *,
        is_allowed_sender: bool,
        access_level: str | AccessLevel | None,
    ) -> str:
        """Нормализует access_level для truthful runtime-summary."""
        return resolve_access_mode(
            is_allowed_sender=is_allowed_sender,
            access_level=access_level,
        )

    def _build_runtime_capability_status(
        self,
        *,
        is_allowed_sender: bool,
        access_level: str | AccessLevel | None = None,
    ) -> str:
        """
        Возвращает детерминированный capability-отчёт по реальному runtime.

        Принципы:
        - не обещаем то, чего реально нет;
        - не отдаём опасные owner-only возможности посторонним чатам;
        - не строим "roadmap", а описываем текущее состояние.
        """
        current_model = str(model_manager.get_current_model() or "").strip()
        route_meta = {}
        if hasattr(openclaw_client, "get_last_runtime_route"):
            try:
                route_meta = openclaw_client.get_last_runtime_route() or {}
            except Exception:
                route_meta = {}

        route_channel = str(route_meta.get("channel", "") or "").strip()
        route_model = str(route_meta.get("model", "") or "").strip()
        active_model = (
            current_model
            or route_model
            or str(getattr(config, "LOCAL_PREFERRED_MODEL", "") or "").strip()
        )
        access_mode = self._resolve_runtime_access_mode(
            is_allowed_sender=is_allowed_sender,
            access_level=access_level,
        )

        abilities: list[str] = [
            "- Отвечать на вопросы, объяснять сложные темы, писать тексты и помогать с кодом.",
            f"- Работать локально через LM Studio. Сейчас активная локальная модель: `{active_model or 'не определена'}`.",
            "- Поддерживать контекст диалога в текущей сессии и держать историю разговора.",
            "- Разбирать фото и скриншоты, когда доступен vision-маршрут.",
        ]

        if bool(getattr(config, "SCHEDULER_ENABLED", False)):
            abilities.append(
                "- Ставить напоминания и отложенные задачи через `!remind`, `!reminders`, `!rm_remind`."
            )
        if access_mode in {AccessLevel.OWNER.value, AccessLevel.FULL.value}:
            abilities.extend(
                [
                    "- Искать информацию в вебе по команде `!search`.",
                    "- Запоминать и вспоминать факты по командам `!remember` и `!recall`.",
                    "- Снимать owner-digest, читать последние записи общей памяти и вести owner-visible inbox через `!watch`, `!memory recent`, `!inbox`.",
                    "- Работать с файлами по путям через `!ls`, `!read`, `!write`.",
                    "- Выполнять базовые действия в macOS через `!mac` (clipboard, notifications, apps, Finder/open, Notes, Reminders, Calendar).",
                    "- Управлять браузерным/веб-контуром через `!web` и открывать панель через `!panel`.",
                    "- Управлять voice-профилем ответов через `!voice` (вкл/выкл, скорость, голос, delivery).",
                    "- Управлять product-профилем переводчика через `!translator` (языки, mode, strategy, call-flags, quick phrases).",
                ]
            )
        elif access_mode == AccessLevel.PARTIAL.value:
            abilities.extend(
                [
                    "- Искать информацию в вебе по команде `!search`.",
                    "- Показывать truthful runtime-статус и безопасные help-команды.",
                    "- Работать в изолированном контуре без owner-only инструментов.",
                ]
            )
        else:
            abilities.extend(
                [
                    "- Давать структурированные ответы в виде списков, планов, кратких инструкций и пояснений.",
                    "- Работать как текстовый ассистент без раскрытия внутренних owner-инструментов.",
                ]
            )

        limitations: list[str] = [
            "- Актуальные данные из интернета подтягиваю не автоматически в каждом ответе, а через явный инструментальный маршрут или команду.",
            "- Не выполняю физические действия в реальном мире, но могу делать ограниченные системные действия внутри macOS по явной owner-команде.",
            "- Не запоминаю всю переписку навсегда автоматически: долговременная память у меня точечная и управляется отдельно.",
            "- Качество анализа фото зависит от того, какая модель и какой маршрут сейчас доступны.",
        ]
        if access_mode in {AccessLevel.OWNER.value, AccessLevel.FULL.value}:
            limitations.append(
                "- Голосовой ingress уже работает, но полноценный live-call/WebRTC-контур ещё не доведён до финального режима."
            )
            limitations.append(
                "- Работа с файлами идёт через команды и пути, а не как полностью бесшовная загрузка любых вложений в обычном диалоге."
            )
        elif access_mode == AccessLevel.PARTIAL.value:
            limitations.append(
                "- Частичный доступ не открывает файловый контур, браузерное управление, панель, конфиги и admin-команды."
            )
        else:
            limitations.append(
                "- Системные инструменты вроде файлов, браузера и admin-команд доступны только доверенному контуру владельца."
            )

        route_note = ""
        if route_channel or route_model:
            route_note = (
                "\n\n🧭 **Текущий runtime-статус**\n"
                f"- Канал: `{route_channel or 'unknown'}`\n"
                f"- Модель: `{route_model or active_model or 'unknown'}`"
            )

        return (
            "🦀 **Что я уже умею сейчас**\n"
            + "\n".join(abilities)
            + "\n\n🧩 **Что пока ограничено**\n"
            + "\n".join(limitations)
            + route_note
            + "\n\nЕсли хочешь, я могу отдельно показать список **команд**, **инструментов владельца** или **реальных активных интеграций** в этом runtime."
        )

    def _build_runtime_commands_status(
        self,
        *,
        is_allowed_sender: bool,
        access_level: str | AccessLevel | None = None,
    ) -> str:
        """
        Возвращает truth-summary по доступным Telegram-командам.

        Для гостевого контура не раскрываем owner-only/admin команды.
        """
        access_mode = self._resolve_runtime_access_mode(
            is_allowed_sender=is_allowed_sender,
            access_level=access_level,
        )
        if access_mode == AccessLevel.PARTIAL.value:
            return (
                "🧭 **Команды частичного доступа**\n"
                "- `!help`\n"
                "- `!search <запрос>`\n"
                "- `!status`\n\n"
                "🔒 **Что недоступно в этом контуре**\n"
                "- Управление моделями, памятью, файлами, браузером, панелью и runtime-конфигом.\n"
                "- Owner/full-команды для диагностики, записи файлов и глобальных изменений."
            )
        if access_mode not in {AccessLevel.OWNER.value, AccessLevel.FULL.value}:
            return (
                "🦀 **Что доступно в обычном диалоге**\n"
                "- Свободные текстовые запросы без спецкоманд.\n"
                "- Вопросы, объяснения, помощь с текстом и кодом.\n"
                "- Уточняющие запросы по текущему диалогу.\n\n"
                "🔒 **Что скрыто в этом контуре**\n"
                "- Служебные команды владельца для управления моделями, файлами, вебом и панелью.\n"
                "- Внутренние admin-инструменты и файловый доступ.\n\n"
                "Если нужен именно список owner-команд, его можно показать только в доверенном чате."
            )

        core_commands = [
            "`!status`, `!clear`, `!config`, `!help`",
        ]
        model_commands = [
            "`!model`, `!model local`, `!model cloud`, `!model auto`, `!model set <model_id>`, `!model load <name>`, `!model unload`, `!model scan`",
        ]
        tool_commands = [
            "`!search <запрос>`, `!remember <текст>`, `!recall <запрос>`, `!watch status|now`, `!memory recent [source]`, `!inbox [list|status|ack|done|cancel]`, `!role`, `!agent ...`",
        ]
        system_commands = [
            "`!ls [path]`, `!read <path>`, `!write <file> <content>`, `!sysinfo`, `!diagnose`, `!web`, `!panel`, `!voice ...`, `!translator ...`, `!mac ...`",
        ]
        if bool(getattr(config, "SCHEDULER_ENABLED", False)):
            tool_commands.append(
                "`!remind <время> | <текст>`, `!reminders`, `!rm_remind <id>`, `!cronstatus`"
            )

        body = (
            "🧭 **Команды, которые реально доступны сейчас**\n"
            "\n**Core**\n- "
            + "\n- ".join(core_commands)
            + "\n\n**AI / Model**\n- "
            + "\n- ".join(model_commands)
            + "\n\n**Tools**\n- "
            + "\n- ".join(tool_commands)
            + "\n\n**System / Dev**\n- "
            + "\n- ".join(system_commands)
        )
        if access_mode == AccessLevel.OWNER.value:
            body += (
                "\n\n**Owner-only admin**\n"
                "- `!set <KEY> <VAL>`\n"
                "- `!restart`\n"
                "- `!acl ...` / `!access ...`"
            )
        elif OWNER_ONLY_COMMANDS:
            body += (
                "\n\n🔒 **Что оставлено только владельцу**\n- `!set`, `!restart`, `!acl`, `!access`"
            )
        return (
            body
            + "\n\nЕсли хочешь, я могу следующим сообщением показать короткую шпаргалку **по каждой команде с примерами**."
        )

    async def _build_runtime_integrations_status(
        self,
        *,
        is_allowed_sender: bool,
        access_level: str | AccessLevel | None = None,
    ) -> str:
        """
        Возвращает truth-summary по активным интеграциям и инструментам runtime.

        Здесь избегаем ложных обещаний:
        - MCP считаем "configured", если у managed-launch нет missing env;
        - внешние инструменты, требующие owner-доступ, не раскрываем в гостевом контуре.
        """
        # Ленивый импорт: тесты патчат resolve_managed_server_launch на
        # модуле mcp_registry; прямой import в mixin создаст отдельную ссылку,
        # невидимую для monkeypatch.
        from ..core.mcp_registry import resolve_managed_server_launch

        local_model = str(model_manager.get_current_model() or "").strip()
        openclaw_ok = await openclaw_client.health_check()
        scheduler_on = bool(getattr(config, "SCHEDULER_ENABLED", False))
        brave_ready = not bool(resolve_managed_server_launch("brave-search").get("missing_env"))
        context7_ready = not bool(resolve_managed_server_launch("context7").get("missing_env"))
        firecrawl_ready = not bool(resolve_managed_server_launch("firecrawl").get("missing_env"))
        browser_ready = not bool(
            resolve_managed_server_launch("openclaw-browser").get("missing_env")
        )
        chrome_profile_ready = not bool(
            resolve_managed_server_launch("chrome-profile").get("missing_env")
        )
        access_mode = self._resolve_runtime_access_mode(
            is_allowed_sender=is_allowed_sender,
            access_level=access_level,
        )

        public_lines = [
            f"- OpenClaw Gateway: {'ON' if openclaw_ok else 'OFF'}",
            f"- LM Studio local: {'ON' if local_model else 'IDLE'}"
            + (f" (`{local_model}`)" if local_model else ""),
            f"- Scheduler / reminders: {'ON' if scheduler_on else 'OFF'}",
            "- Голосовой TTS-ответ: ON",
        ]

        if access_mode == AccessLevel.PARTIAL.value:
            return (
                "🔌 **Текущие интеграции Краба**\n"
                + "\n".join(public_lines)
                + f"\n- Web search (Brave): {'configured' if brave_ready else 'missing key'}"
                + "\n- Owner-only MCP, браузерный контроль, файловый доступ и расширенный tool-контур скрыты в этом чате."
            )
        if access_mode not in {AccessLevel.OWNER.value, AccessLevel.FULL.value}:
            return (
                "🔌 **Текущие интеграции Краба**\n"
                + "\n".join(public_lines)
                + "\n- Внешние owner-инструменты и расширенный tool-контур скрыты в этом чате."
            )

        owner_lines = [
            f"- Web search (Brave): {'configured' if brave_ready else 'missing key'}",
            f"- Context7 docs: {'configured' if context7_ready else 'missing key'}",
            f"- Firecrawl: {'configured' if firecrawl_ready else 'missing key / credits'}",
            f"- Browser relay MCP: {'configured' if browser_ready else 'missing config'}",
            f"- Chrome profile DevTools: {'configured' if chrome_profile_ready else 'missing config'}",
            f"- macOS automation: {'configured' if macos_automation.is_available() else 'unavailable'}",
            "- Memory engine: ON",
            f"- Proactive watch: {'ON' if bool(getattr(config, 'PROACTIVE_WATCH_ENABLED', False)) else 'OFF'}",
            "- Файловый MCP-контур: ON",
        ]
        return (
            "🔌 **Реальные интеграции и инструменты runtime**\n"
            + "\n".join(public_lines + owner_lines)
            + "\n\nЕсли хочешь, я могу отдельно показать статус в формате **что работает / что требует ключ / что требует баланс**."
        )

    async def _build_runtime_truth_status(
        self,
        *,
        is_allowed_sender: bool,
        access_level: str | AccessLevel | None = None,
    ) -> str:
        """
        Собирает короткий truthful self-check без вызова LLM.

        Это сводка по самым важным для пользователя вещам:
        - отвечает ли транспорт;
        - какой фактический маршрут/модель были последними;
        - включён ли scheduler;
        - что можно утверждать про браузер и интернет без фантазий.
        """
        # Ленивые импорты: _current_runtime_primary_model — module-level helper
        # в userbot_bridge.py; resolve_managed_server_launch — из mcp_registry.
        from ..core.mcp_registry import resolve_managed_server_launch
        from ..userbot_bridge import _current_runtime_primary_model

        route_meta = {}
        if hasattr(openclaw_client, "get_last_runtime_route"):
            try:
                route_meta = openclaw_client.get_last_runtime_route() or {}
            except Exception:
                route_meta = {}

        openclaw_ok = await openclaw_client.health_check()
        local_model = str(model_manager.get_current_model() or "").strip()
        route_channel = str(route_meta.get("channel", "") or "").strip()
        route_model = str(route_meta.get("model", "") or "").strip()
        route_provider = str(route_meta.get("provider", "") or "").strip()
        scheduler_on = bool(getattr(config, "SCHEDULER_ENABLED", False))
        scheduler_started = bool(getattr(krab_scheduler, "is_started", False))
        browser_ready = not bool(
            resolve_managed_server_launch("openclaw-browser").get("missing_env")
        )
        chrome_profile_ready = not bool(
            resolve_managed_server_launch("chrome-profile").get("missing_env")
        )
        brave_ready = not bool(resolve_managed_server_launch("brave-search").get("missing_env"))
        access_mode = self._resolve_runtime_access_mode(
            is_allowed_sender=is_allowed_sender,
            access_level=access_level,
        )

        route_line = (
            f"`{route_channel}`"
            if route_channel
            else "ещё не подтверждён в этом канале (self-check не гоняет LLM-маршрут)"
        )
        model_line = (
            f"`{route_model or local_model}`"
            if (route_model or local_model)
            else "ещё не подтверждена"
        )
        primary_hint = ""
        try:
            model_info = (
                self.router.get_model_info() if hasattr(self, "router") and self.router else {}
            )
        except Exception:
            model_info = {}
        if isinstance(model_info, dict):
            primary_hint = str(model_info.get("current_model", "") or "").strip()
        if not primary_hint:
            primary_hint = _current_runtime_primary_model()

        lines: list[str] = [
            "🧭 **Фактический runtime self-check**",
            f"- Gateway / transport: {'ON' if openclaw_ok else 'OFF'}",
            "- Текущий канал: Python Telegram userbot (primary transport)",
            f"- Последний маршрут: {route_line}",
            f"- Последняя модель: {model_line}",
        ]
        if route_provider:
            lines.append(f"- Провайдер: `{route_provider}`")
        if primary_hint and not route_model:
            lines.append(f"- Primary по runtime: `{primary_hint}`")
        if scheduler_on and scheduler_started:
            lines.append("- Scheduler / reminders: включён и подтверждён runtime-стартом")
        elif scheduler_on:
            lines.append("- Scheduler / reminders: включён, но runtime-старт ещё не подтверждён")
        else:
            lines.append("- Scheduler / reminders: выключен")
        lines.append(
            "- Браузерный контур: "
            + (
                "сконфигурирован, но доступ к конкретной вкладке надо подтверждать отдельным действием"
                if browser_ready or chrome_profile_ready
                else "не подтверждён"
            )
        )
        lines.append(
            "- Интернет / веб-поиск: "
            + (
                "доступен через инструментальный маршрут по явному запросу"
                if access_mode
                in {AccessLevel.OWNER.value, AccessLevel.FULL.value, AccessLevel.PARTIAL.value}
                and brave_ready
                else "не подтверждается как постоянный фоновой доступ"
            )
        )
        if scheduler_on and scheduler_started and openclaw_ok:
            lines.append("- Cron / heartbeat: scheduler активен, transport живой.")
        elif scheduler_on and scheduler_started:
            lines.append(
                "- Cron / heartbeat: scheduler активен, но transport сейчас не подтверждён."
            )
        else:
            lines.append(
                "- Cron / heartbeat: без подтверждённого scheduler runtime не считаю их рабочими."
            )

        return "\n".join(lines)

    @staticmethod
    def _build_command_access_denied_text(command_name: str, access_profile: AccessProfile) -> str:
        """Возвращает понятное сообщение при попытке вызвать недоступную команду."""
        command = str(command_name or "").strip().lower()
        if access_profile.level == AccessLevel.PARTIAL:
            return (
                f"🔒 Команда `!{command}` недоступна в режиме частичного доступа.\n"
                "Сейчас доступны: `!status`, `!help`, `!search <запрос>`.\n"
                "Для расширения прав владелец должен перевести контакт в full-доступ."
            )
        return (
            f"🔒 Команда `!{command}` доступна только доверенному контуру Краба.\n"
            "В обычном диалоге доступны свободные сообщения, а служебные команды скрыты."
        )
