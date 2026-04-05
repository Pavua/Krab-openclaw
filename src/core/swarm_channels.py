# -*- coding: utf-8 -*-
"""
src/core/swarm_channels.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Live-трансляция свёрм-раундов в Telegram-группы + перехват директив владельца.

Зачем:
- видеть работу каждой команды в реальном времени (не ждать конца раунда)
- вмешиваться на ходу — сообщение в группу инжектируется как директива
- сохранять историю обсуждений в Telegram естественным образом

Связь с проектом:
- конфигурация: SWARM_TEAM_CHATS в .env (формат: "traders:-100xxx,coders:-100yyy")
- вызывается из swarm.py (AgentRoom.run_round) через broadcast callback
- intervention перехватывается из userbot_bridge
- хранилище: ~/.openclaw/krab_runtime_state/swarm_channels.json (mapping team→chat_id)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .logger import get_logger

logger = get_logger(__name__)

_STATE_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "swarm_channels.json"


def _parse_team_chats_env(raw: str) -> dict[str, int]:
    """
    Парсит SWARM_TEAM_CHATS env var.

    Формат: "traders:-1001234567890,coders:-1009876543210"
    Возвращает: {"traders": -1001234567890, "coders": -1009876543210}
    """
    result: dict[str, int] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        team, chat_id_str = pair.split(":", 1)
        team = team.strip().lower()
        try:
            result[team] = int(chat_id_str.strip())
        except ValueError:
            logger.warning("swarm_channels_bad_chat_id", team=team, raw=chat_id_str)
    return result


class SwarmChannels:
    """
    Управляет Telegram-группами для живой трансляции свёрм-раундов.

    Lifecycle:
    1. bind(client, owner_id) — привязка Pyrogram-клиента
    2. register_team_chat(team, chat_id) — маппинг команда→группа
    3. broadcast_role_step(...) — публикация шага роли в группу
    4. get_pending_intervention(team) — забрать директиву владельца

    Intervention:
    Когда owner пишет в swarm-группу во время раунда, сообщение
    сохраняется в очередь. AgentRoom забирает его через
    get_pending_intervention() и инжектирует в контекст.
    """

    def __init__(self) -> None:
        self._team_chats: dict[str, int] = {}
        self._client: Any = None
        self._owner_id: int = 0
        self._interventions: dict[str, list[str]] = {}  # team -> [messages]
        self._active_rounds: dict[str, float] = {}  # team -> start_time
        self._load()

    # -- lifecycle ------------------------------------------------------------

    def bind(self, client: Any, owner_id: int) -> None:
        """Привязывает Pyrogram-клиент и ID владельца."""
        self._client = client
        self._owner_id = owner_id

        # Загружаем маппинг из env
        env_chats = _parse_team_chats_env(os.getenv("SWARM_TEAM_CHATS", ""))
        if env_chats:
            self._team_chats.update(env_chats)
            self._save()
            logger.info("swarm_channels_bound", teams=list(self._team_chats.keys()),
                        owner_id=owner_id)

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for team, chat_id in data.get("team_chats", {}).items():
                self._team_chats[team] = int(chat_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_channels_load_failed", error=str(exc))

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {"team_chats": self._team_chats}
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as exc:  # noqa: BLE001
            logger.error("swarm_channels_save_failed", error=str(exc))

    @property
    def _path(self) -> Path:
        return _STATE_PATH

    # -- public API -----------------------------------------------------------

    def register_team_chat(self, team: str, chat_id: int) -> None:
        """Регистрирует группу для команды."""
        self._team_chats[team.lower()] = chat_id
        self._save()
        logger.info("swarm_channels_registered", team=team, chat_id=chat_id)

    def get_team_chat(self, team: str) -> int | None:
        """Возвращает chat_id группы для команды."""
        return self._team_chats.get(team.lower())

    def get_all_team_chats(self) -> dict[str, int]:
        """Все зарегистрированные команды и их группы."""
        return dict(self._team_chats)

    def is_swarm_chat(self, chat_id: int) -> str | None:
        """Если chat_id — swarm-группа, возвращает имя команды. Иначе None."""
        for team, cid in self._team_chats.items():
            if cid == chat_id:
                return team
        return None

    def mark_round_active(self, team: str) -> None:
        """Отмечает начало раунда — с этого момента слушаем intervention."""
        self._active_rounds[team.lower()] = time.monotonic()
        self._interventions.pop(team.lower(), None)

    def mark_round_done(self, team: str) -> None:
        """Раунд завершён — больше не принимаем intervention."""
        self._active_rounds.pop(team.lower(), None)

    def is_round_active(self, team: str) -> bool:
        """Идёт ли сейчас раунд для команды."""
        return team.lower() in self._active_rounds

    # -- broadcast ------------------------------------------------------------

    async def broadcast_role_step(
        self,
        *,
        team: str,
        role_name: str,
        role_emoji: str,
        role_title: str,
        text: str,
        is_start: bool = False,
        is_end: bool = False,
    ) -> None:
        """
        Публикует шаг роли в группу команды.

        Вызывается из AgentRoom.run_round() после каждого ответа роли.
        """
        chat_id = self.get_team_chat(team)
        if not chat_id or not self._client:
            return

        if is_start:
            msg = f"🐝 **Начинаю раунд**\nТема будет в следующем сообщении..."
        elif is_end:
            msg = f"✅ **Раунд завершён**"
        else:
            msg = f"**{role_emoji} {role_title}:**\n{text}"

        # Обрезаем до Telegram-лимита
        if len(msg) > 4000:
            msg = msg[:3950] + "\n\n[...обрезано]"

        try:
            await self._client.send_message(chat_id, msg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_channels_broadcast_failed",
                           team=team, role=role_name, error=str(exc))

    async def broadcast_round_start(self, *, team: str, topic: str) -> None:
        """Анонс начала раунда."""
        chat_id = self.get_team_chat(team)
        if not chat_id or not self._client:
            return
        try:
            await self._client.send_message(
                chat_id,
                f"🐝 **Новый раунд**\n📋 Тема: _{topic[:200]}_\n\n"
                f"💡 Напиши сообщение в этот чат чтобы направить команду.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_channels_round_start_failed", team=team, error=str(exc))

    async def broadcast_round_end(self, *, team: str, summary: str) -> None:
        """Итог раунда."""
        chat_id = self.get_team_chat(team)
        if not chat_id or not self._client:
            return
        short = summary[:500] + ("..." if len(summary) > 500 else "")
        try:
            await self._client.send_message(
                chat_id,
                f"✅ **Раунд завершён**\n\n{short}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_channels_round_end_failed", team=team, error=str(exc))

    # -- intervention ---------------------------------------------------------

    def add_intervention(self, team: str, text: str) -> None:
        """
        Сохраняет директиву владельца для активного раунда.

        Вызывается из userbot_bridge при получении сообщения в swarm-группу.
        """
        key = team.lower()
        if key not in self._active_rounds:
            return  # нет активного раунда — игнорируем
        if key not in self._interventions:
            self._interventions[key] = []
        self._interventions[key].append(text.strip())
        logger.info("swarm_channels_intervention_added", team=team, text_len=len(text))

    def get_pending_intervention(self, team: str) -> str:
        """
        Забирает все накопленные директивы владельца.

        Возвращает пустую строку если директив нет.
        После вызова очередь очищается.
        """
        key = team.lower()
        messages = self._interventions.pop(key, [])
        if not messages:
            return ""
        combined = "\n".join(messages)
        return f"\n\n👑 **Директива владельца:**\n{combined}\n"

    # -- formatting -----------------------------------------------------------

    def format_status(self) -> str:
        """Статус для !swarm channels."""
        if not self._team_chats:
            return (
                "📡 Swarm-группы не настроены.\n\n"
                "Создай группы в Telegram и добавь в `.env`:\n"
                "`SWARM_TEAM_CHATS=traders:-100xxx,coders:-100yyy`\n\n"
                "Или используй `!swarm setchat traders` в нужной группе."
            )
        lines = ["📡 **Swarm-группы:**\n"]
        for team, chat_id in self._team_chats.items():
            active = "🟢 раунд идёт" if self.is_round_active(team) else "⚪"
            lines.append(f"**{team}** → `{chat_id}` {active}")
        return "\n".join(lines)


# Singleton
swarm_channels = SwarmChannels()
