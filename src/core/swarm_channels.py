# -*- coding: utf-8 -*-
"""
src/core/swarm_channels.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Live-трансляция свёрм-раундов в Telegram Forum Group + перехват директив владельца.

Два режима доставки:
1. **Forum Topics** (рекомендуемый) — одна supergroup с is_forum=True,
   каждая команда пишет в свой топик (message_thread_id).
2. **Legacy** — отдельная группа на каждую команду (SWARM_TEAM_CHATS).

Конфигурация:
- SWARM_FORUM_CHAT_ID   — chat_id форум-группы (включает Forum Topics режим)
- SWARM_TEAM_CHATS      — fallback: "traders:-100xxx,coders:-100yyy"

Вызывается из swarm.py (AgentRoom.run_round) через broadcast callback.
Intervention перехватывается из userbot_bridge.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

_STATE_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "swarm_channels.json"

# Эмодзи для топиков — Telegram принимает только определённые цвета
_TOPIC_ICON_COLORS = {
    "traders": 0x6FB9F0,  # голубой
    "coders": 0xFFD67E,  # жёлтый
    "analysts": 0xCB86DB,  # фиолетовый
    "creative": 0x8EEE98,  # зелёный
    "crossteam": 0xFF93B2,  # розовый
}

# Определения топиков для авто-создания
_FORUM_TOPICS = [
    {"key": "traders", "title": "💰 Traders", "icon_color": 0x6FB9F0},
    {"key": "coders", "title": "💻 Coders", "icon_color": 0xFFD67E},
    {"key": "analysts", "title": "📊 Analysts", "icon_color": 0xCB86DB},
    {"key": "creative", "title": "💡 Creative", "icon_color": 0x8EEE98},
    {"key": "crossteam", "title": "📡 Cross-team", "icon_color": 0xFF93B2},
]


def _parse_team_chats_env(raw: str) -> dict[str, int]:
    """
    Парсит SWARM_TEAM_CHATS env var (legacy формат).

    Формат: "traders:-1001234567890,coders:-1009876543210"
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
    Управляет доставкой свёрм-трансляций в Telegram.

    Поддерживает два режима:
    - Forum Topics: одна группа, message_thread_id per team
    - Legacy: отдельные группы per team

    Lifecycle:
    1. bind(client, owner_id)
    2. setup_forum() или register_team_chat()
    3. broadcast_role_step() / broadcast_round_start/end()
    4. get_pending_intervention()
    """

    def __init__(self) -> None:
        # Forum Topics
        self._forum_chat_id: int | None = None
        self._team_topics: dict[str, int] = {}  # team -> topic_id

        # Legacy: отдельные группы
        self._team_chats: dict[str, int] = {}

        self._client: Any = None
        self._team_clients: dict[str, Any] = {}  # team → per-team Pyrogram Client
        self._owner_id: int = 0
        self._interventions: dict[str, list[str]] = {}
        self._active_rounds: dict[str, float] = {}
        self._load()

    @property
    def is_forum_mode(self) -> bool:
        """True если настроен Forum Topics режим."""
        return self._forum_chat_id is not None and len(self._team_topics) > 0

    # -- lifecycle ------------------------------------------------------------

    def bind(self, client: Any, owner_id: int) -> None:
        """Привязывает Pyrogram-клиент и ID владельца."""
        self._client = client
        self._owner_id = owner_id

        # Forum mode из env
        forum_env = os.getenv("SWARM_FORUM_CHAT_ID", "").strip()
        if forum_env:
            try:
                self._forum_chat_id = int(forum_env)
            except ValueError:
                logger.warning("swarm_channels_bad_forum_chat_id", raw=forum_env)

        # Legacy groups из env
        env_chats = _parse_team_chats_env(os.getenv("SWARM_TEAM_CHATS", ""))
        if env_chats:
            self._team_chats.update(env_chats)

        self._save()
        mode = "forum" if self.is_forum_mode else ("legacy" if self._team_chats else "none")
        logger.info("swarm_channels_bound", mode=mode, owner_id=owner_id)

    def bind_team_client(self, team: str, client: Any) -> None:
        """Привязывает per-team Pyrogram Client для отправки от имени команды."""
        self._team_clients[team.lower()] = client
        logger.info("swarm_team_client_bound", team=team)

    def unbind_team_client(self, team: str) -> None:
        """Убирает per-team client."""
        self._team_clients.pop(team.lower(), None)
        logger.info("swarm_team_client_unbound", team=team)

    def _resolve_client(self, team: str) -> Any:
        """Выбирает Pyrogram Client для команды (fallback на основной)."""
        team_cl = self._team_clients.get(team.lower())
        if team_cl is not None:
            try:
                if team_cl.is_connected:
                    return team_cl
            except Exception:  # noqa: BLE001
                pass
            logger.warning("swarm_team_client_unavailable_fallback", team=team)
        return self._client

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        if not _STATE_PATH.exists():
            return
        try:
            data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
            # Forum
            if "forum_chat_id" in data and data["forum_chat_id"]:
                self._forum_chat_id = int(data["forum_chat_id"])
            for team, tid in data.get("team_topics", {}).items():
                self._team_topics[team] = int(tid)
            # Legacy
            for team, cid in data.get("team_chats", {}).items():
                self._team_chats[team] = int(cid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_channels_load_failed", error=str(exc))

    def _save(self) -> None:
        try:
            _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "forum_chat_id": self._forum_chat_id,
                "team_topics": self._team_topics,
                "team_chats": self._team_chats,
            }
            tmp = _STATE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(_STATE_PATH)
        except Exception as exc:  # noqa: BLE001
            logger.error("swarm_channels_save_failed", error=str(exc))

    # -- forum setup ----------------------------------------------------------

    async def setup_forum(self) -> dict[str, Any]:
        """
        Создаёт Forum supergroup и топики для всех команд.

        Возвращает dict с chat_id и topic_ids.
        Использует raw Pyrogram API для Forum Topics.

        Telegram API обновляется чаще чем Pyrogram, поэтому ToggleForum
        может требовать параметры (tabs), которых нет в текущей TL-схеме.
        В этом случае создаём обычную supergroup и просим владельца
        включить Topics вручную (Settings → Topics).
        """
        if not self._client:
            raise RuntimeError("SwarmChannels не привязан к клиенту. Вызови bind() сначала.")

        from pyrogram import raw  # noqa: PLC0415

        # 1. Создаём supergroup
        result = await self._client.create_supergroup("🐝 Krab Swarm")
        chat_id = result.id
        logger.info("swarm_forum_group_created", chat_id=chat_id)

        # 2. Включаем Forum mode (с fallback'ами)
        peer = await self._client.resolve_peer(chat_id)
        forum_enabled = await self._try_enable_forum(peer)

        # 3. Добавляем owner в группу
        if self._owner_id:
            try:
                owner_peer = await self._client.resolve_peer(self._owner_id)
                await self._client.invoke(
                    raw.functions.channels.InviteToChannel(
                        channel=peer,
                        users=[owner_peer],
                    )
                )
                logger.info("swarm_forum_owner_added", owner_id=self._owner_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("swarm_forum_owner_invite_failed", error=str(exc))

        # 4. Создаём топики (только если forum включен)
        topic_ids: dict[str, int] = {}
        if forum_enabled:
            topic_ids = await self._create_topics(chat_id)

        # 5. Сохраняем
        self._forum_chat_id = chat_id
        self._team_topics = topic_ids
        self._save()

        # 6. Приветственное сообщение
        if forum_enabled and topic_ids:
            await self._send_message(
                chat_id,
                "🐝 **Krab Swarm Forum активирован!**\n\n"
                "Каждая команда работает в своём топике.\n"
                "Пиши в топик во время раунда — Краб подхватит как директиву.\n\n"
                f"Команды: {', '.join(topic_ids.keys())}",
            )
        else:
            await self._send_message(
                chat_id,
                "🐝 **Krab Swarm группа создана!**\n\n"
                "⚠️ Не удалось включить Topics автоматически.\n"
                "Включи вручную: **Group Settings → Topics → Enable**\n"
                "Затем набери `!swarm setup` в этой группе.",
            )

        return {
            "chat_id": chat_id,
            "topic_ids": topic_ids,
            "forum_enabled": forum_enabled,
        }

    async def _try_enable_forum(self, peer: Any) -> bool:
        """Пытается включить Forum mode несколькими способами."""
        from pyrogram import raw  # noqa: PLC0415

        # Способ 1: стандартный ToggleForum (Layer 158)
        try:
            await self._client.invoke(
                raw.functions.channels.ToggleForum(channel=peer, enabled=True)
            )
            logger.info("swarm_forum_enabled", method="toggle_forum")
            return True
        except Exception as exc1:  # noqa: BLE001
            logger.warning("swarm_toggle_forum_failed", error=str(exc1))

        # Способ 2: пробуем создать топик напрямую (иногда это авто-включает Forum)
        try:
            await self._invoke_create_topic(peer, "General", None)
            logger.info("swarm_forum_enabled", method="create_topic_auto")
            return True
        except Exception as exc2:  # noqa: BLE001
            logger.warning("swarm_create_topic_auto_failed", error=str(exc2))

        return False

    async def setup_topics_in_existing(self, chat_id: int) -> dict[str, int]:
        """
        Создаёт топики в уже существующей Forum-группе.

        Полезно если пользователь создал группу вручную и включил Topics.
        """
        if not self._client:
            raise RuntimeError("SwarmChannels не привязан к клиенту.")

        topic_ids = await self._create_topics(chat_id)
        self._forum_chat_id = chat_id
        self._team_topics = topic_ids
        self._save()
        return topic_ids

    async def _create_topics(self, chat_id: int) -> dict[str, int]:
        """Создаёт топики для всех команд, переиспользуя существующие."""
        peer = await self._client.resolve_peer(chat_id)
        existing = await self._get_existing_topics(chat_id)
        topic_ids: dict[str, int] = {}

        for topic_def in _FORUM_TOPICS:
            # Пробуем найти существующий топик по названию
            matched = False
            for ex in existing:
                if topic_def["title"].lower() in ex["title"].lower():
                    topic_ids[topic_def["key"]] = ex["id"]
                    matched = True
                    logger.info(
                        "swarm_forum_topic_matched", team=topic_def["key"], topic_id=ex["id"]
                    )
                    break

            if not matched:
                try:
                    topic_result = await self._invoke_create_topic(
                        peer,
                        topic_def["title"],
                        topic_def.get("icon_color"),
                    )
                    topic_id = _extract_topic_id(topic_result)
                    if topic_id:
                        topic_ids[topic_def["key"]] = topic_id
                        logger.info(
                            "swarm_forum_topic_created", team=topic_def["key"], topic_id=topic_id
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "swarm_forum_topic_create_failed", team=topic_def["key"], error=repr(exc)
                    )
                    if not topic_ids:
                        raise

        return topic_ids

    async def _invoke_create_topic(
        self,
        peer: Any,
        title: str,
        icon_color: int | None,
    ) -> Any:
        """Создаёт топик через raw API (pyrofork 2.3+)."""
        from pyrogram import raw  # noqa: PLC0415

        kwargs: dict[str, Any] = {
            "title": title,
            "random_id": self._client.rnd_id(),
            "peer": peer,
        }
        if icon_color is not None:
            kwargs["icon_color"] = icon_color

        return await self._client.invoke(raw.functions.messages.CreateForumTopic(**kwargs))

    async def _get_existing_topics(self, chat_id: int) -> list[dict[str, Any]]:
        """Получает список существующих топиков в Forum-группе (pyrofork 2.3+)."""
        from pyrogram import raw  # noqa: PLC0415

        try:
            peer = await self._client.resolve_peer(chat_id)
            result = await self._client.invoke(
                raw.functions.messages.GetForumTopics(
                    peer=peer,
                    offset_date=0,
                    offset_id=0,
                    offset_topic=0,
                    limit=100,
                )
            )
            topics = []
            for t in getattr(result, "topics", []):
                topics.append(
                    {
                        "id": t.id,
                        "title": t.title,
                        "icon_color": getattr(t, "icon_color", None),
                    }
                )
            return topics
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_channels_get_topics_failed", error=str(exc))
            return []

    # -- message sending (forum-aware) ----------------------------------------

    async def _send_message(
        self,
        chat_id: int,
        text: str,
        topic_id: int | None = None,
        *,
        client: Any = None,
    ) -> None:
        """Отправляет сообщение в чат, опционально в конкретный топик форума (pyrofork 2.3+)."""
        _cl = client or self._client
        if not _cl:
            return

        if len(text) > 4000:
            text = text[:3950] + "\n\n[...обрезано]"

        if topic_id:
            try:
                await _cl.send_message(
                    chat_id,
                    text,
                    message_thread_id=topic_id,
                )
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("swarm_channels_thread_send_failed", error=str(exc))
                # fallback: raw API с top_msg_id
                try:
                    from pyrogram import raw  # noqa: PLC0415

                    peer = await _cl.resolve_peer(chat_id)
                    await _cl.invoke(
                        raw.functions.messages.SendMessage(
                            peer=peer,
                            message=text,
                            random_id=_cl.rnd_id(),
                            top_msg_id=topic_id,
                        )
                    )
                    return
                except Exception as raw_exc:  # noqa: BLE001
                    logger.warning("swarm_channels_raw_send_failed", error=str(raw_exc))

        # Обычная отправка (без топика или оба способа с топиком не сработали)
        await _cl.send_message(chat_id, text)

    def _resolve_destination(self, team: str) -> tuple[int | None, int | None]:
        """
        Определяет куда слать: (chat_id, topic_id).

        Forum mode: один chat_id + topic_id per team.
        Legacy mode: отдельный chat_id per team, topic_id=None.
        """
        team_lower = team.lower()

        # Forum mode — приоритет
        if self._forum_chat_id and team_lower in self._team_topics:
            return self._forum_chat_id, self._team_topics[team_lower]

        # Legacy mode
        if team_lower in self._team_chats:
            return self._team_chats[team_lower], None

        # Crossteam fallback: если команда не зарегистрирована, шлём в crossteam
        if self._forum_chat_id and "crossteam" in self._team_topics:
            return self._forum_chat_id, self._team_topics["crossteam"]

        return None, None

    # -- public API -----------------------------------------------------------

    def register_team_chat(self, team: str, chat_id: int) -> None:
        """Регистрирует отдельную группу для команды (legacy)."""
        self._team_chats[team.lower()] = chat_id
        self._save()
        logger.info("swarm_channels_registered", team=team, chat_id=chat_id)

    def register_forum_topic(self, team: str, topic_id: int) -> None:
        """Регистрирует топик для команды в Forum-группе."""
        self._team_topics[team.lower()] = topic_id
        self._save()
        logger.info("swarm_channels_topic_registered", team=team, topic_id=topic_id)

    def get_team_chat(self, team: str) -> int | None:
        """Возвращает chat_id для команды (legacy совместимость)."""
        return self._team_chats.get(team.lower())

    def get_all_team_chats(self) -> dict[str, int]:
        """Все зарегистрированные команды и их группы."""
        return dict(self._team_chats)

    def is_swarm_chat(self, chat_id: int) -> str | None:
        """Если chat_id — swarm-группа/форум, возвращает имя команды. Иначе None."""
        # Forum mode: все команды в одной группе
        if self._forum_chat_id and chat_id == self._forum_chat_id:
            return "_forum"  # специальный маркер

        # Legacy: отдельные группы
        for team, cid in self._team_chats.items():
            if cid == chat_id:
                return team
        return None

    def resolve_team_from_topic(self, topic_id: int) -> str | None:
        """По topic_id определяет команду (для Forum mode)."""
        for team, tid in self._team_topics.items():
            if tid == topic_id:
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

    # -- broadcast (forum-aware) ----------------------------------------------

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
        """Публикует шаг роли в группу/топик команды."""
        chat_id, topic_id = self._resolve_destination(team)
        if not chat_id or not self._client:
            return
        cl = self._resolve_client(team)

        if is_start:
            msg = "🐝 **Начинаю раунд**\nТема будет в следующем сообщении..."
        elif is_end:
            msg = "✅ **Раунд завершён**"
        else:
            msg = f"**{role_emoji} {role_title}:**\n{text}"

        try:
            await self._send_message(chat_id, msg, topic_id=topic_id, client=cl)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "swarm_channels_broadcast_failed", team=team, role=role_name, error=str(exc)
            )

    async def broadcast_round_start(self, *, team: str, topic: str) -> None:
        """Анонс начала раунда."""
        chat_id, topic_id = self._resolve_destination(team)
        if not chat_id or not self._client:
            return
        cl = self._resolve_client(team)
        try:
            await self._send_message(
                chat_id,
                f"🐝 **Новый раунд**\n📋 Тема: _{topic[:200]}_\n\n"
                f"💡 Напиши сообщение чтобы направить команду.",
                topic_id=topic_id,
                client=cl,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_channels_round_start_failed", team=team, error=str(exc))

    async def broadcast_round_end(self, *, team: str, summary: str) -> None:
        """Итог раунда."""
        chat_id, topic_id = self._resolve_destination(team)
        if not chat_id or not self._client:
            return
        cl = self._resolve_client(team)
        short = summary[:500] + ("..." if len(summary) > 500 else "")
        try:
            await self._send_message(
                chat_id,
                f"✅ **Раунд завершён**\n\n{short}",
                topic_id=topic_id,
                client=cl,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_channels_round_end_failed", team=team, error=str(exc))

    async def broadcast_delegation(self, *, source_team: str, target_team: str, topic: str) -> None:
        """Уведомление о делегировании между командами."""
        # Шлём в crossteam топик (или source team если crossteam нет)
        chat_id, topic_id = self._resolve_destination("crossteam")
        if not chat_id:
            chat_id, topic_id = self._resolve_destination(source_team)
        if not chat_id or not self._client:
            return
        cl = self._resolve_client(source_team)
        try:
            await self._send_message(
                chat_id,
                f"📡 **Делегирование**\n"
                f"От: **{source_team}** → **{target_team}**\n"
                f"Задача: _{topic[:200]}_",
                topic_id=topic_id,
                client=cl,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_channels_delegation_broadcast_failed", error=str(exc))

    # -- intervention ---------------------------------------------------------

    def add_intervention(self, team: str, text: str) -> None:
        """
        Сохраняет директиву владельца для активного раунда.

        Вызывается из userbot_bridge при получении сообщения в swarm-группу/топик.
        """
        key = team.lower()
        if key not in self._active_rounds:
            return
        if key not in self._interventions:
            self._interventions[key] = []
        self._interventions[key].append(text.strip())
        logger.info("swarm_channels_intervention_added", team=team, text_len=len(text))

    def get_pending_intervention(self, team: str) -> str:
        """
        Забирает все накопленные директивы владельца.

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
        lines: list[str] = []

        if self.is_forum_mode:
            lines.append(f"📡 **Swarm Forum** (chat: `{self._forum_chat_id}`)\n")
            for team, topic_id in self._team_topics.items():
                active = "🟢 раунд" if self.is_round_active(team) else "⚪"
                lines.append(f"  **{team}** → topic `{topic_id}` {active}")
            if self._team_chats:
                lines.append("\n📡 **Legacy-группы** (не используются при Forum mode):")
                for team, cid in self._team_chats.items():
                    lines.append(f"  **{team}** → `{cid}`")
            if self._team_clients:
                lines.append("\n🤖 **Team accounts:**")
                for t, cl in self._team_clients.items():
                    connected = getattr(cl, "is_connected", False) if cl else False
                    icon = "🟢" if connected else "🔴"
                    lines.append(f"  {icon} **{t}**")
            return "\n".join(lines)

        if self._team_chats:
            lines.append("📡 **Swarm-группы (legacy):**\n")
            for team, cid in self._team_chats.items():
                active = "🟢 раунд" if self.is_round_active(team) else "⚪"
                lines.append(f"**{team}** → `{cid}` {active}")
            return "\n".join(lines)

        return (
            "📡 Swarm-группы не настроены.\n\n"
            "**Рекомендуемый способ:**\n"
            "`!swarm setup` — создать Forum-группу с топиками\n\n"
            "**Ручной способ:**\n"
            "1. Создай группу и включи Topics\n"
            "2. `!swarm setup` в этой группе\n\n"
            "**Legacy:**\n"
            "`SWARM_TEAM_CHATS=traders:-100xxx,coders:-100yyy` в `.env`"
        )


def _extract_topic_id(updates: Any) -> int | None:
    """Извлекает topic_id из ответа CreateForumTopic (MTProto Updates)."""
    # Ответ — Updates, содержащий service message о создании топика
    for update in getattr(updates, "updates", []):
        msg = getattr(update, "message", None)
        if msg and getattr(msg, "id", None):
            return msg.id
    return None


# Singleton
swarm_channels = SwarmChannels()
