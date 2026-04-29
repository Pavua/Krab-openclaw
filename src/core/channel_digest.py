# -*- coding: utf-8 -*-
"""
channel_digest.py — Channel auto-curated digest (Idea 6).

Pure builder для daily summary: вычитывает archive.db за окно `hours_back`
и собирает markdown-дайджест из активных групповых чатов для последующей
публикации в собственный Telegram канал Краба.

Контракт:
- Ничего не публикует — только формирует строку.
- Секции с пустыми данными пропускаются.
- Если archive.db недоступна или таблиц нет — возвращает пустую строку.
- Псевдонимизация: имя чата подменяется на "chat <chat_id>" (или короткий
  идентификатор приватного чата). Owner может маппить chat_id на читаемые
  заголовки уже на стороне публикатора.

Секции:
1. Hottest topics — по количеству сообщений за период (proxy для
   "горячести", т.к. в archive.messages нет reaction-count напрямую).
2. Insights — chunk'и с positive_count из response_feedback.
3. Voice/audio summaries — записи из message_media_summaries
   (media_type IN photo/video/voice/audio/animation).

Использование (планируемое):
    builder = ChannelDigestBuilder()
    md = builder.build_digest(source_chats=[-100123, -100456], hours_back=24)
    # позже cron-задача публикует md в канал Краба
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .logger import get_logger
from .memory_archive import DEFAULT_ARCHIVE_PATH

logger = get_logger(__name__)

# Глобальный cap для всего дайджеста (символов).
CHANNEL_DIGEST_MAX_CHARS: int = 4000
# Default лимиты на секцию.
DIGEST_DEFAULT_MAX_ITEMS: int = 5
# Минимальное число сообщений в чате, чтобы попасть в hot topics.
DIGEST_MIN_MESSAGES_FOR_HOT: int = 3
# Преview-длина текста в insights/media (символов).
DIGEST_PREVIEW_CHARS: int = 180


@dataclass(frozen=True)
class HotTopic:
    """Чат-кандидат для секции hottest topics."""

    chat_id: str
    message_count: int
    sample_text: str  # короткое preview одного из сообщений


@dataclass(frozen=True)
class InsightItem:
    """Insight: ответ Краба с положительным фидбеком."""

    chat_id: str
    message_id: str
    positive_count: int
    text_preview: str


@dataclass(frozen=True)
class MediaSummaryItem:
    """Vision/audio summary из message_media_summaries."""

    chat_id: str
    message_id: str
    media_type: str
    summary: str


def _pseudonimize(chat_id: str) -> str:
    """Маскируем chat_id под безопасный label.

    Для groups/supergroups (-100…) показываем последние 6 цифр.
    Для приватных (positive int) — sha1-style 6 hex chars от str(chat_id).
    """
    raw = str(chat_id)
    if raw.startswith("-100"):
        suffix = raw[-6:] if len(raw) >= 6 else raw
        return f"group_{suffix}"
    # Приватные / каналы — короткий хеш, без раскрытия user_id.
    import hashlib

    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6]
    return f"private_{digest}"


def _truncate(text: str, limit: int) -> str:
    """Обрезаем текст до limit символов с многоточием."""
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


class ChannelDigestBuilder:
    """Builder для дайджеста; pure (никаких side-effects кроме чтения БД).

    Параметры:
        archive_path: путь к archive.db (для тестов можно подменить).
        now_fn: callable для текущего времени (UTC); тесты подменяют.
    """

    def __init__(
        self,
        archive_path: Path | None = None,
        now_fn=None,
    ) -> None:
        self._archive_path = Path(archive_path) if archive_path else DEFAULT_ARCHIVE_PATH
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def build_digest(
        self,
        *,
        source_chats: list[int] | None = None,
        hours_back: int = 24,
        max_items: int = DIGEST_DEFAULT_MAX_ITEMS,
    ) -> str:
        """Главный entry-point: возвращает markdown-дайджест.

        Если ни одна секция ничего не нашла — возвращает пустую строку,
        чтобы caller мог легко skip публикацию.
        """
        if not self._archive_path.exists():
            logger.debug("channel_digest_archive_missing", path=str(self._archive_path))
            return ""

        since_iso = (self._now_fn() - timedelta(hours=max(1, hours_back))).isoformat()

        try:
            conn = sqlite3.connect(
                f"file:{self._archive_path}?mode=ro",
                uri=True,
                timeout=5.0,
            )
        except sqlite3.Error as exc:
            logger.warning(
                "channel_digest_open_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return ""

        try:
            hot = self._collect_hot_topics(conn, since_iso, source_chats, max_items)
            insights = self._collect_insights(conn, since_iso, source_chats, max_items)
            media = self._collect_media_summaries(conn, since_iso, source_chats, max_items)
        finally:
            try:
                conn.close()
            except sqlite3.Error:
                pass

        return self._format_markdown(hot, insights, media, hours_back=hours_back)

    # ------------------------------------------------------------------
    # Сбор данных
    # ------------------------------------------------------------------

    def _chat_filter_clause(self, source_chats: list[int] | None) -> tuple[str, list[str]]:
        """SQL where-clause + список параметров для фильтра по chat_id.

        Если source_chats пуст / None — фильтр не накладывается.
        """
        if not source_chats:
            return "", []
        placeholders = ",".join(["?"] * len(source_chats))
        params = [str(c) for c in source_chats]
        return f"AND chat_id IN ({placeholders})", params

    def _collect_hot_topics(
        self,
        conn: sqlite3.Connection,
        since_iso: str,
        source_chats: list[int] | None,
        max_items: int,
    ) -> list[HotTopic]:
        """Топ-чаты по count(messages) за окно (proxy для активности)."""
        clause, params = self._chat_filter_clause(source_chats)
        sql = f"""
            SELECT chat_id, COUNT(*) AS cnt
              FROM messages
             WHERE timestamp >= ?
               {clause}
             GROUP BY chat_id
            HAVING cnt >= ?
             ORDER BY cnt DESC
             LIMIT ?
        """
        try:
            rows = conn.execute(
                sql,
                [since_iso, *params, DIGEST_MIN_MESSAGES_FOR_HOT, max_items],
            ).fetchall()
        except sqlite3.Error as exc:
            logger.warning(
                "channel_digest_hot_query_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []

        result: list[HotTopic] = []
        for chat_id, cnt in rows:
            sample = self._fetch_sample_message(conn, chat_id, since_iso)
            result.append(
                HotTopic(
                    chat_id=str(chat_id),
                    message_count=int(cnt),
                    sample_text=sample,
                )
            )
        return result

    def _fetch_sample_message(
        self,
        conn: sqlite3.Connection,
        chat_id: str,
        since_iso: str,
    ) -> str:
        """Берём одно длинное сообщение из чата для preview."""
        try:
            row = conn.execute(
                """
                SELECT text_redacted
                  FROM messages
                 WHERE chat_id = ?
                   AND timestamp >= ?
                   AND length(text_redacted) > 30
                 ORDER BY length(text_redacted) DESC
                 LIMIT 1
                """,
                (chat_id, since_iso),
            ).fetchone()
        except sqlite3.Error:
            return ""
        return _truncate(row[0] if row else "", DIGEST_PREVIEW_CHARS)

    def _collect_insights(
        self,
        conn: sqlite3.Connection,
        since_iso: str,
        source_chats: list[int] | None,
        max_items: int,
    ) -> list[InsightItem]:
        """Inсайты: ответы Краба с positive_count > 0 за окно."""
        clause, params = self._chat_filter_clause(source_chats)
        # Для feedback берём last_updated_at в окне (фидбек получен недавно).
        sql = f"""
            SELECT rf.chat_id, rf.message_id, rf.positive_count,
                   COALESCE(m.text_redacted, '') AS text
              FROM response_feedback AS rf
              LEFT JOIN messages AS m
                     ON m.chat_id = rf.chat_id
                    AND m.message_id = rf.message_id
             WHERE rf.last_updated_at >= ?
               AND rf.positive_count > 0
               {clause.replace("chat_id", "rf.chat_id")}
             ORDER BY rf.positive_count DESC, rf.last_updated_at DESC
             LIMIT ?
        """
        try:
            rows = conn.execute(sql, [since_iso, *params, max_items]).fetchall()
        except sqlite3.Error as exc:
            # Таблицы может не быть на старых БД — graceful degrade.
            logger.debug(
                "channel_digest_insights_query_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []

        return [
            InsightItem(
                chat_id=str(chat_id),
                message_id=str(message_id),
                positive_count=int(pos),
                text_preview=_truncate(text or "", DIGEST_PREVIEW_CHARS),
            )
            for chat_id, message_id, pos, text in rows
        ]

    def _collect_media_summaries(
        self,
        conn: sqlite3.Connection,
        since_iso: str,
        source_chats: list[int] | None,
        max_items: int,
    ) -> list[MediaSummaryItem]:
        """Voice/audio/video summary за окно."""
        clause, params = self._chat_filter_clause(source_chats)
        sql = f"""
            SELECT chat_id, message_id, media_type, summary
              FROM message_media_summaries
             WHERE generated_at >= ?
               {clause}
             ORDER BY generated_at DESC
             LIMIT ?
        """
        try:
            rows = conn.execute(sql, [since_iso, *params, max_items]).fetchall()
        except sqlite3.Error as exc:
            logger.debug(
                "channel_digest_media_query_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []

        return [
            MediaSummaryItem(
                chat_id=str(chat_id),
                message_id=str(message_id),
                media_type=str(media_type or "media"),
                summary=_truncate(summary or "", DIGEST_PREVIEW_CHARS),
            )
            for chat_id, message_id, media_type, summary in rows
        ]

    # ------------------------------------------------------------------
    # Форматирование
    # ------------------------------------------------------------------

    def _format_markdown(
        self,
        hot: list[HotTopic],
        insights: list[InsightItem],
        media: list[MediaSummaryItem],
        *,
        hours_back: int,
    ) -> str:
        """Собираем markdown с эмоджи-заголовками секций."""
        if not hot and not insights and not media:
            return ""

        now = self._now_fn()
        lines: list[str] = []
        date_label = now.strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"# Krab daily digest — {date_label}")
        lines.append(f"_Окно: последние {hours_back}ч._")
        lines.append("")

        if hot:
            lines.append("## 🔥 Hottest topics")
            for item in hot:
                label = _pseudonimize(item.chat_id)
                lines.append(f"- **{label}** — {item.message_count} сообщений")
                if item.sample_text:
                    lines.append(f"  > {item.sample_text}")
            lines.append("")

        if insights:
            lines.append("## 💡 Insights")
            for item in insights:
                label = _pseudonimize(item.chat_id)
                lines.append(f"- **{label}** (👍 ×{item.positive_count}): {item.text_preview}")
            lines.append("")

        if media:
            lines.append("## 🎙️ Voice & media summaries")
            for item in media:
                label = _pseudonimize(item.chat_id)
                lines.append(f"- **{label}** [{item.media_type}]: {item.summary}")
            lines.append("")

        text = "\n".join(lines).rstrip() + "\n"
        if len(text) > CHANNEL_DIGEST_MAX_CHARS:
            text = text[: CHANNEL_DIGEST_MAX_CHARS - 1].rstrip() + "…\n"
        return text


# Module-level singleton (по паттерну остальных core-сервисов).
channel_digest_builder = ChannelDigestBuilder()
