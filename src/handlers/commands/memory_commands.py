# -*- coding: utf-8 -*-
"""
memory_commands - Phase 2 Wave 5 extraction (Session 27).

Memory-related userbot commands:
  !remember <текст>      — сохранить факт в workspace + vector memory
  !recall <запрос>       — поиск по workspace + vector + Memory Layer hybrid
  !mem <запрос>          — быстрый доступ к Memory Layer (search/stats/count/summary)
  !quote                 — цитаты (built-in random / save / my / list)
  !tag <тег>             — теги на сообщения (add/list/find/del)

Включает helpers и module-level state:
  _BUILTIN_QUOTES, _SAVED_QUOTES_PATH, _load_saved_quotes, _save_quotes,
  _TAGS_FILE, _load_tags, _save_tags, _make_msg_link,
  _MEM_HELP_TEXT, _MEM_SNIPPET_LEN, _mem_truncate,
  _mem_stats, _mem_count, _mem_summary, _mem_search,
  MEMORY_SEARCH_URL, _recall_memory_layer, _format_memory_layer_section.

Re-exported from command_handlers.py для обратной совместимости.

См. ``docs/CODE_SPLITS_PLAN.md`` Phase 2 - domain extractions.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
from typing import TYPE_CHECKING

import httpx
from pyrogram.types import Message

from ...core.exceptions import UserInputError
from ...core.logger import get_logger
from ...core.memory_validator import memory_validator
from ...core.openclaw_workspace import (
    append_workspace_memory_entry,
    recall_workspace_memory,
)
from ...memory_engine import memory_manager

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# !remember / !recall
# ---------------------------------------------------------------------------


async def handle_remember(bot: "KraabUserbot", message: Message) -> None:
    """Запомнить факт."""
    text = bot._get_command_args(message)
    if not text:
        raise UserInputError(user_message="🧠 Что запомнить? Напиши: `!remember <текст>`")
    # Memory Injection Validator: persistent-инструкции требуют !confirm от owner.
    author = str(getattr(getattr(message, "from_user", None), "username", "") or "")
    safe, warn_msg, _ = memory_validator.stage(text, source="userbot", author=author)
    if not safe:
        await message.reply(warn_msg)
        return
    try:
        workspace_saved = append_workspace_memory_entry(
            text,
            source="userbot",
            author=author,
        )
        vector_saved = memory_manager.save_fact(text)
        success = workspace_saved or vector_saved
        if success:
            await message.reply(f"🧠 **Запомнил:** `{text}`")
        else:
            await message.reply("❌ Ошибка памяти.")
    except (ValueError, RuntimeError, OSError) as e:
        await message.reply(f"❌ Critical Memory Error: {e}")


MEMORY_SEARCH_URL = (
    os.environ.get("KRAB_PANEL_URL", "http://127.0.0.1:8080").rstrip("/") + "/api/memory/search"
)


async def _recall_memory_layer(query: str, limit: int = 5) -> list[dict]:
    """Зовёт /api/memory/search (hybrid) и возвращает список результатов.

    Для надёжности использует httpx с коротким timeout. Все ошибки
    (недоступность endpoint'а, отсутствующая БД) проглатываются — вызов
    из handle_recall не должен ронять UX из-за опционального слоя.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                MEMORY_SEARCH_URL,
                params={"q": query, "mode": "hybrid", "limit": limit},
            )
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 — endpoint может быть не запущен
        logger.debug("recall_memory_layer_failed", error=str(exc))
        return []

    if not isinstance(data, dict) or not data.get("ok"):
        return []
    results = data.get("results") or []
    if not isinstance(results, list):
        return []
    return results[:limit]


def _format_memory_layer_section(results: list[dict]) -> str:
    """Форматирует результаты Memory Layer для вставки в !recall ответ."""
    lines: list[str] = []
    for i, r in enumerate(results, start=1):
        text = str(r.get("text") or "")
        preview = text[:150].replace("\n", " ").strip()
        mode = r.get("mode", "hybrid")
        score = r.get("score")
        try:
            score_str = f"{float(score):.2f}" if score is not None else "—"
        except (TypeError, ValueError):
            score_str = "—"
        lines.append(f"{i}. [{mode} score={score_str}]\n   `{preview}`")
    return "\n".join(lines)


# Baseline-снимки имён для дифференциации ``mc.<X>`` vs ``command_handlers.<X>``
# monkeypatch в ``handle_recall`` (Phase 2 split). Не патчить!
_RECALL_BASELINES: dict[str, object] = {
    "recall_workspace_memory": recall_workspace_memory,
    "memory_manager": memory_manager,
    "_recall_memory_layer": _recall_memory_layer,
}


async def handle_recall(bot: "KraabUserbot", message: Message) -> None:
    """Вспомнить факт — workspace + vector + Memory Layer archive (hybrid)."""
    text = bot._get_command_args(message)
    if not text:
        raise UserInputError(user_message="🧠 Что вспомнить? Напиши: `!recall <запрос>`")
    try:
        # Lazy lookup — тесты патчат либо `mc.<sym>` (новый namespace),
        # либо `command_handlers.<sym>` (исторический). Берём override
        # из текущего модуля (приоритет), затем из command_handlers.
        import sys

        _self_mod = sys.modules[__name__]
        try:
            from .. import command_handlers as _ch
        except Exception:  # noqa: BLE001
            _ch = None  # type: ignore[assignment]

        def _resolve(name: str, default):
            self_val = _self_mod.__dict__.get(name, default)
            orig = _RECALL_BASELINES.get(name, default)
            if self_val is not orig:
                return self_val  # patched в текущем модуле (mc)
            if _ch is not None:
                ch_val = getattr(_ch, name, default)
                if ch_val is not orig:
                    return ch_val  # patched в command_handlers
            return self_val

        _recall_fn = _resolve("recall_workspace_memory", recall_workspace_memory)
        _mm = _resolve("memory_manager", memory_manager)
        _rml = _resolve("_recall_memory_layer", _recall_memory_layer)
        workspace_facts = _recall_fn(text)
        vector_facts = _mm.recall(text)
        memory_layer_results = await _rml(text, limit=5)

        sections: list[str] = []
        if workspace_facts:
            sections.append(f"**OpenClaw workspace:**\n{workspace_facts}")
        if vector_facts and vector_facts not in workspace_facts:
            sections.append(f"**Local vector memory:**\n{vector_facts}")
        if memory_layer_results:
            sections.append(
                "**Memory Layer archive (hybrid):**\n"
                + _format_memory_layer_section(memory_layer_results)
            )
        facts = "\n\n".join(section for section in sections if section).strip()
        if facts:
            await message.reply(f"🧠 **Вспомнил:**\n\n{facts}")
        else:
            await message.reply("🧠 Ничего не нашел по этому запросу.")
    except (ValueError, RuntimeError, OSError) as e:
        await message.reply(f"❌ Recalling Error: {e}")


# ---------------------------------------------------------------------------
# !quote — built-in мотивационные цитаты + сохранение пользовательских
# ---------------------------------------------------------------------------

_BUILTIN_QUOTES: list[str] = [
    "Успех — это не конечная точка, провал — не смертельный исход. Важна лишь смелость продолжать. — Уинстон Черчилль",
    "В середине каждой трудности лежит возможность. — Альберт Эйнштейн",
    "Жизнь — это то, что происходит с тобой, пока ты строишь другие планы. — Джон Леннон",
    "Будь собой — все остальные роли уже заняты. — Оскар Уайльд",
    "Единственный способ делать великую работу — любить то, что ты делаешь. — Стив Джобс",
    "Не важно, как медленно ты идёшь, главное — не останавливаться. — Конфуций",
    "Верь, что можешь, — и ты уже на полпути. — Теодор Рузвельт",
    "Жизнь прожита не зря, если ты зажёг хотя бы одну свечу во тьме. — Ромен Роллан",
    "Сначала они тебя игнорируют, потом смеются над тобой, потом борются с тобой. Потом ты побеждаешь. — Махатма Ганди",
    "Человек рождён для счастья, как птица для полёта. — Владимир Короленко",
    "Всё, что нас не убивает, делает нас сильнее. — Фридрих Ницше",
    "Чтобы дойти до цели, надо прежде всего идти. — Оноре де Бальзак",
    "Мечтай, как будто ты будешь жить вечно. Живи, как будто ты умрёшь сегодня. — Джеймс Дин",
    "Не бойся медленно продвигаться вперёд. Бойся стоять на месте. — Китайская пословица",
    "Смелость — это не отсутствие страха, а решимость победить его. — Нельсон Мандела",
    "Каждый день — это новая возможность изменить свою жизнь. — Сэр Пол Маккартни",
    "Лучшее время, чтобы посадить дерево, было 20 лет назад. Второе лучшее время — сейчас. — Китайская пословица",
    "Ты не можешь вернуться назад и изменить начало, но ты можешь начать сейчас и изменить конец. — К.С. Льюис",
    "Величайшая слава в жизни — не в том, чтобы никогда не падать, а в том, чтобы каждый раз подниматься. — Нельсон Мандела",
    "Стремись не к тому, чтобы добиться успеха, а к тому, чтобы твоя жизнь имела смысл. — Альберт Эйнштейн",
    "Делай, что можешь, тем, что имеешь, там, где ты есть. — Теодор Рузвельт",
    "Счастье — это когда то, что ты думаешь, то, что ты говоришь, и то, что ты делаешь, находятся в гармонии. — Махатма Ганди",
    "Измени своё мышление, и ты изменишь свой мир. — Норман Пил",
    "Опыт — это то, что ты получаешь, когда не получаешь того, чего хотел. — Рэнди Пауш",
    "Проблема людей в том, что они слишком долго думают, прежде чем начать. — Конфуций",
    "Начни где стоишь. Используй что имеешь. Делай что можешь. — Артур Эш",
    "It does not matter how slowly you go as long as you do not stop. — Confucius",
    "In the middle of difficulty lies opportunity. — Albert Einstein",
    "The only way to do great work is to love what you do. — Steve Jobs",
    "Success is not final, failure is not fatal: It is the courage to continue that counts. — Winston Churchill",
    "Believe you can and you're halfway there. — Theodore Roosevelt",
    "The future belongs to those who believe in the beauty of their dreams. — Eleanor Roosevelt",
    "It always seems impossible until it's done. — Nelson Mandela",
    "You are never too old to set another goal or to dream a new dream. — C.S. Lewis",
    "The only limit to our realization of tomorrow will be our doubts of today. — Franklin D. Roosevelt",
    "Act as if what you do makes a difference. It does. — William James",
    "Hardships often prepare ordinary people for an extraordinary destiny. — C.S. Lewis",
    "Keep your eyes on the stars and your feet on the ground. — Theodore Roosevelt",
    "Life is what happens when you're busy making other plans. — John Lennon",
    "Happiness is when what you think, what you say, and what you do are in harmony. — Mahatma Gandhi",
    "Be the change you wish to see in the world. — Mahatma Gandhi",
    "The best time to plant a tree was 20 years ago. The second best time is now. — Chinese Proverb",
    "Dream as if you'll live forever. Live as if you'll die today. — James Dean",
    "Don't watch the clock; do what it does. Keep going. — Sam Levenson",
    "You miss 100% of the shots you don't take. — Wayne Gretzky",
    "The secret of getting ahead is getting started. — Mark Twain",
    "Whether you think you can or you think you can't, you're right. — Henry Ford",
    "Twenty years from now you will be more disappointed by the things you didn't do. — Mark Twain",
    "The way to get started is to quit talking and begin doing. — Walt Disney",
    "Innovation distinguishes between a leader and a follower. — Steve Jobs",
]

# Путь к файлу с пользовательскими цитатами
_SAVED_QUOTES_PATH = pathlib.Path.home() / ".openclaw" / "krab_runtime_state" / "saved_quotes.json"


def _quotes_path() -> pathlib.Path:
    """Lazy lookup пути к файлу цитат через parent namespace (Phase 2)."""
    from .. import command_handlers as _ch

    return getattr(_ch, "_SAVED_QUOTES_PATH", _SAVED_QUOTES_PATH)


def _load_saved_quotes() -> list[dict]:
    """Загружает сохранённые цитаты из JSON-файла."""
    path = _quotes_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_quotes(quotes: list[dict]) -> None:
    """Сохраняет список цитат в JSON-файл."""
    path = _quotes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(quotes, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def handle_quote(bot: "KraabUserbot", message: Message) -> None:
    """
    Команда цитат.

    Синтаксис:
      !quote              — случайная встроенная мотивационная цитата
      !quote save         — сохранить цитируемое сообщение (reply)
      !quote my           — случайная из сохранённых
      !quote list         — список всех сохранённых цитат
    """
    import random

    args = bot._get_command_args(message).strip().lower()

    # --- !quote save ---
    if args == "save":
        reply = message.reply_to_message
        if not reply:
            await message.reply("💬 Ответь на сообщение, которое хочешь сохранить как цитату.")
            return
        text = (reply.text or reply.caption or "").strip()
        if not text:
            await message.reply("💬 Сообщение не содержит текста.")
            return
        # Автор: имя пользователя или «неизвестно»
        sender = reply.from_user
        if sender:
            author = sender.first_name or ""
            if sender.last_name:
                author = f"{author} {sender.last_name}".strip()
            if not author and sender.username:
                author = f"@{sender.username}"
        else:
            author = "Неизвестно"
        saved = _load_saved_quotes()
        entry = {
            "text": text,
            "author": author,
            "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        saved.append(entry)
        _save_quotes(saved)
        await message.reply(f"✅ Цитата сохранена (#{len(saved)}):\n\n_{text}_\n— **{author}**")
        return

    # --- !quote my ---
    if args == "my":
        saved = _load_saved_quotes()
        if not saved:
            await message.reply(
                "📭 У тебя пока нет сохранённых цитат. Используй `!quote save` в reply."
            )
            return
        entry = random.choice(saved)
        text = entry.get("text", "")
        author = entry.get("author", "Неизвестно")
        await message.reply(f"💬 _{text}_\n— **{author}**")
        return

    # --- !quote list ---
    if args == "list":
        saved = _load_saved_quotes()
        if not saved:
            await message.reply("📭 Нет сохранённых цитат. Используй `!quote save` в reply.")
            return
        lines = []
        for i, entry in enumerate(saved, 1):
            text = entry.get("text", "")
            author = entry.get("author", "?")
            preview = text[:80] + "…" if len(text) > 80 else text
            lines.append(f"{i}. _{preview}_ — **{author}**")
        reply_text = "📚 **Сохранённые цитаты:**\n\n" + "\n".join(lines)
        await message.reply(reply_text)
        return

    # --- неизвестная подкоманда — справка ---
    if args and args not in ("save", "my", "list"):
        await message.reply(
            "💬 **!quote** — цитаты\n\n"
            "`!quote` — случайная мотивационная цитата\n"
            "`!quote save` — сохранить цитируемое сообщение (reply)\n"
            "`!quote my` — случайная из сохранённых\n"
            "`!quote list` — все сохранённые цитаты"
        )
        return

    # --- !quote (без аргументов) — случайная встроенная цитата ---
    quote = random.choice(_BUILTIN_QUOTES)
    await message.reply(f"💬 _{quote}_")


# ---------------------------------------------------------------------------
# !tag — теги на сообщения
# ---------------------------------------------------------------------------

_TAGS_FILE = pathlib.Path.home() / ".openclaw" / "krab_runtime_state" / "message_tags.json"


def _tags_path() -> pathlib.Path:
    """Возвращает путь к файлу тегов через parent namespace.

    Lazy lookup нужен чтобы тесты, патчащие ``command_handlers._TAGS_FILE``
    (исторический путь до Phase 2 split) видели свой override здесь.
    """
    from .. import command_handlers as _ch

    return getattr(_ch, "_TAGS_FILE", _TAGS_FILE)


def _load_tags() -> dict[str, dict[str, list[str]]]:
    """Загружает теги из JSON. Формат: {chat_id: {message_id: [tags]}}."""
    path = _tags_path()
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_tags(data: dict[str, dict[str, list[str]]]) -> None:
    """Сохраняет теги в JSON-файл."""
    path = _tags_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_msg_link(chat_id: int, message_id: int) -> str:
    """Формирует ссылку на сообщение Telegram."""
    if chat_id < 0:
        # Супергруппы/каналы: -100XXXXXXXXXX → t.me/c/XXXXXXXXXX/id
        numeric = str(chat_id).lstrip("-")
        if numeric.startswith("100"):
            numeric = numeric[3:]
        return f"https://t.me/c/{numeric}/{message_id}"
    return f"https://t.me/c/{chat_id}/{message_id}"


async def handle_tag(bot: "KraabUserbot", message: Message) -> None:
    """
    !tag <тег>              — в reply → добавляет тег к сообщению
    !tag list               — все теги (уникальные) с количеством
    !tag find <тег>         — сообщения с тегом (ссылки)
    !tag del <тег>          — удалить тег с сообщения (в reply)
    """
    raw_args = bot._get_command_args(message).strip()
    parts = raw_args.split(None, 1)

    chat_id = message.chat.id

    # --- !tag list ---
    if not parts or parts[0].lower() == "list":
        tags_data = _load_tags()
        chat_key = str(chat_id)
        chat_tags = tags_data.get(chat_key, {})
        # Собираем все теги с подсчётом
        counter: dict[str, int] = {}
        for tag_list in chat_tags.values():
            for t in tag_list:
                counter[t] = counter.get(t, 0) + 1
        if not counter:
            await message.reply("🏷 Тегов нет. Используй `!tag <тег>` в reply на сообщение.")
            return
        lines = [f"• `{t}` — {n} сообщ." for t, n in sorted(counter.items())]
        await message.reply("🏷 **Теги в этом чате:**\n" + "\n".join(lines))
        return

    subcommand = parts[0].lower()

    # --- !tag find <тег> ---
    if subcommand == "find":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи тег: `!tag find <тег>`")
        needle = parts[1].strip().lower()
        tags_data = _load_tags()
        chat_key = str(chat_id)
        chat_tags = tags_data.get(chat_key, {})
        matches = [
            int(msg_id)
            for msg_id, tag_list in chat_tags.items()
            if needle in [t.lower() for t in tag_list]
        ]
        if not matches:
            await message.reply(f"🔍 Нет сообщений с тегом `{needle}`.")
            return
        links = [_make_msg_link(chat_id, mid) for mid in sorted(matches)]
        header = f"🔍 Сообщения с тегом `{needle}` ({len(links)}):"
        await message.reply(header + "\n" + "\n".join(links))
        return

    # --- !tag del <тег> ---
    if subcommand == "del":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи тег: `!tag del <тег>` в reply на сообщение")
        tag = parts[1].strip()
        replied = message.reply_to_message
        if replied is None:
            raise UserInputError(user_message="❌ Ответь на сообщение командой `!tag del <тег>`")
        msg_id = str(replied.id)
        chat_key = str(chat_id)
        tags_data = _load_tags()
        tag_list = tags_data.get(chat_key, {}).get(msg_id, [])
        if tag not in tag_list:
            raise UserInputError(user_message=f"❌ Тег `{tag}` не найден на этом сообщении.")
        tag_list.remove(tag)
        if tag_list:
            tags_data[chat_key][msg_id] = tag_list
        else:
            del tags_data[chat_key][msg_id]
            if not tags_data[chat_key]:
                del tags_data[chat_key]
        _save_tags(tags_data)
        await message.reply(f"🗑 Тег `{tag}` удалён с сообщения.")
        return

    # --- !tag <тег> в reply — добавить тег ---
    tag = parts[0].strip()
    replied = message.reply_to_message
    if replied is None:
        raise UserInputError(user_message="❌ Ответь на сообщение командой `!tag <тег>`")
    msg_id = str(replied.id)
    chat_key = str(chat_id)
    tags_data = _load_tags()
    chat_tags = tags_data.setdefault(chat_key, {})
    tag_list = chat_tags.setdefault(msg_id, [])
    if tag in tag_list:
        await message.reply(f"ℹ️ Тег `{tag}` уже есть на этом сообщении.")
        return
    tag_list.append(tag)
    _save_tags(tags_data)
    await message.reply(f"🏷 Тег `{tag}` добавлен.")


# ---------------------------------------------------------------------------
# !mem — быстрый доступ к Memory Layer (search/stats/count/summary)
# ---------------------------------------------------------------------------

_MEM_SNIPPET_LEN = 200
_MEM_HELP_TEXT = (
    "🧠 **!mem** — быстрый поиск в Memory Layer\n\n"
    "`!mem <запрос>` — гибридный поиск, топ-5 результатов\n"
    "`!mem stats` — статистика архива (total/encoded/size/чаты)\n"
    "`!mem count [chat_id]` — количество сообщений (опционально по чату)\n"
    "`!mem summary <chat_id>` — первое/последнее сообщение + счётчик\n"
    "`!mem help` — эта справка"
)


def _mem_truncate(text: str, max_len: int = _MEM_SNIPPET_LEN) -> str:
    """Обрезает сниппет до max_len символов."""
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


async def handle_mem(bot: "KraabUserbot", message: Message) -> None:
    """
    !mem — быстрый доступ к Memory Layer.

    Субкоманды:
      !mem <запрос>         — hybrid search (HybridRetriever), топ-5
      !mem stats            — статистика archive.db
      !mem count [chat_id]  — количество сообщений
      !mem summary <chat_id>— первое/последнее + count через LLM или plain stats
      !mem help             — справка
    """
    from ...core.command_registry import bump_command

    bump_command("mem")

    raw = str(message.text or "").strip()
    parts = raw.split(maxsplit=2)
    # parts[0] == "!mem" (или "mem"), parts[1] == subcommand/query, parts[2] == rest
    sub = parts[1].strip() if len(parts) > 1 else ""
    rest = parts[2].strip() if len(parts) > 2 else ""

    if not sub or sub.lower() == "help":
        del bot
        await message.reply(_MEM_HELP_TEXT)
        return

    if sub.lower() == "stats":
        del bot
        await _mem_stats(message)
        return

    if sub.lower() == "count":
        del bot
        chat_id_arg = rest or None
        await _mem_count(message, chat_id_arg)
        return

    if sub.lower() == "summary":
        if not rest:
            await message.reply("❌ Укажите chat_id: `!mem summary <chat_id>`")
            return
        await _mem_summary(bot, message, rest)
        return

    # Всё остальное — поисковый запрос (sub + rest).
    del bot
    query = (sub + (" " + rest if rest else "")).strip()
    await _mem_search(message, query)


async def _mem_stats(message: Message) -> None:
    """Форматирует статистику archive.db и отправляет reply."""
    from ...core.memory_stats import collect_memory_stats

    stats = collect_memory_stats()
    if not stats.get("exists"):
        await message.reply(f"📭 Memory Layer: архив не найден.\n`{stats.get('path', '—')}`")
        return

    total_msgs = stats.get("total_messages", 0)
    total_chunks = stats.get("total_chunks", 0)
    encoded = stats.get("encoded_chunks", 0)
    size_mb = stats.get("db_size_mb", 0.0)
    coverage = stats.get("encoding_coverage_pct", 0.0)
    oldest = stats.get("oldest_message_ts") or "—"
    newest = stats.get("newest_message_ts") or "—"
    top_chats = stats.get("top_chats") or []

    lines = [
        "🧠 **Memory Layer — статистика**",
        "",
        f"📨 Сообщений: **{total_msgs:,}**",
        f"🗂 Чанков: **{total_chunks:,}** (закодировано: {encoded:,}, {coverage}%)",
        f"💾 Размер: **{size_mb} МБ**",
        f"🕐 Диапазон: {str(oldest)[:19]} → {str(newest)[:19]}",
    ]
    if top_chats:
        lines.append("")
        lines.append("**Топ чатов:**")
        for c in top_chats[:5]:
            lines.append(f"  `{c['chat_id']}` — {c['count']:,} сообщений")

    reply = "\n".join(lines)
    if len(reply) > 4000:
        reply = reply[:3997] + "…"
    await message.reply(reply)


async def _mem_count(message: Message, chat_id_arg: str | None) -> None:
    """Считает сообщения в archive.db (опционально — по chat_id)."""
    import sqlite3

    from ...core.memory_stats import default_archive_db_path

    db_path = default_archive_db_path()
    if not db_path.exists():
        await message.reply("📭 Memory Layer: архив не найден.")
        return

    # Валидация chat_id
    if chat_id_arg is not None:
        try:
            int(chat_id_arg)
        except ValueError:
            await message.reply(f"❌ Некорректный chat_id: `{chat_id_arg}`. Ожидается число.")
            return

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            if chat_id_arg is None:
                row = conn.execute("SELECT COUNT(*) FROM messages").fetchone()
                count = int(row[0]) if row else 0
                await message.reply(f"🧠 Memory Layer: **{count:,}** сообщений всего.")
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE chat_id = ?",
                    (chat_id_arg,),
                ).fetchone()
                count = int(row[0]) if row else 0
                await message.reply(
                    f"🧠 Memory Layer: **{count:,}** сообщений в чате `{chat_id_arg}`."
                )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        await message.reply(f"❌ Ошибка чтения архива: {exc}")


async def _mem_summary(bot: "KraabUserbot", message: Message, chat_id_arg: str) -> None:
    """
    Краткая сводка по чату в archive.db:
    первое/последнее сообщение, счётчик, + LLM-резюме если доступен.
    """
    import sqlite3

    from ...core.memory_stats import default_archive_db_path

    # Валидация chat_id
    try:
        int(chat_id_arg)
    except ValueError:
        await message.reply(f"❌ Некорректный chat_id: `{chat_id_arg}`. Ожидается число.")
        return

    db_path = default_archive_db_path()
    if not db_path.exists():
        await message.reply("📭 Memory Layer: архив не найден.")
        return

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt, MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts "
                "FROM messages WHERE chat_id = ?",
                (chat_id_arg,),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        await message.reply(f"❌ Ошибка чтения архива: {exc}")
        return

    if row is None or (row["cnt"] or 0) == 0:
        await message.reply(f"📭 Чат `{chat_id_arg}` не найден в Memory Layer.")
        return

    count = int(row["cnt"])
    first_ts = str(row["first_ts"] or "—")[:19]
    last_ts = str(row["last_ts"] or "—")[:19]

    lines = [
        f"🧠 **Memory summary** — чат `{chat_id_arg}`",
        "",
        f"📨 Сообщений: **{count:,}**",
        f"🕐 Первое: `{first_ts}`",
        f"🕑 Последнее: `{last_ts}`",
    ]

    # Пробуем LLM-резюме (best-effort, не обязательно).
    try:
        from ...openclaw_client import openclaw_client as _oc

        if _oc is not None:
            llm_prompt = (
                f"Дай краткое резюме (2-3 предложения) для архивного чата {chat_id_arg}: "
                f"{count:,} сообщений с {first_ts} по {last_ts}. "
                "Опиши вероятную активность и временной диапазон."
            )
            llm_reply = await _oc.ask(llm_prompt, max_tokens=200)
            if llm_reply and llm_reply.strip():
                lines.append("")
                lines.append("**Резюме (AI):**")
                lines.append(_mem_truncate(llm_reply.strip(), 500))
    except Exception:  # noqa: BLE001
        pass  # LLM не обязателен

    del bot
    reply = "\n".join(lines)
    if len(reply) > 4000:
        reply = reply[:3997] + "…"
    await message.reply(reply)


async def _mem_search(message: Message, query: str) -> None:
    """Гибридный поиск через HybridRetriever, возвращает топ-5."""
    import asyncio

    from ...core.memory_retrieval import HybridRetriever

    retriever = HybridRetriever()
    try:
        results = await asyncio.to_thread(retriever.search, query, top_k=5)
    except Exception as exc:  # noqa: BLE001
        await message.reply(f"❌ Ошибка поиска: {exc}")
        return
    finally:
        retriever.close()

    if not results:
        await message.reply(f"🔍 По запросу «{query}» ничего не найдено в Memory Layer.")
        return

    lines = [f"🧠 **!mem** — топ результатов для «{_mem_truncate(query, 60)}»", ""]
    for i, r in enumerate(results, 1):
        ts_str = r.timestamp.strftime("%Y-%m-%d %H:%M") if r.timestamp else "—"
        snippet = _mem_truncate(r.text_redacted or "", _MEM_SNIPPET_LEN)
        score_pct = int(r.score * 100)
        lines.append(f"**{i}.** `{r.chat_id}` · {ts_str} · {score_pct}%\n> {snippet}")
        lines.append("")

    reply = "\n".join(lines).rstrip()
    if len(reply) > 4000:
        reply = reply[:3997] + "…"
    await message.reply(reply)


__all__ = [
    "MEMORY_SEARCH_URL",
    "_BUILTIN_QUOTES",
    "_MEM_HELP_TEXT",
    "_MEM_SNIPPET_LEN",
    "_SAVED_QUOTES_PATH",
    "_TAGS_FILE",
    "_format_memory_layer_section",
    "_load_saved_quotes",
    "_load_tags",
    "_make_msg_link",
    "_mem_count",
    "_mem_search",
    "_mem_stats",
    "_mem_summary",
    "_mem_truncate",
    "_recall_memory_layer",
    "_save_quotes",
    "_save_tags",
    "handle_mem",
    "handle_quote",
    "handle_recall",
    "handle_remember",
    "handle_tag",
]
