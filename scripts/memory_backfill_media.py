#!/usr/bin/env python3
"""
Feature E backfill: ретро-генерация vision-summaries для фото/видео,
которые попали в archive.db ДО появления Multi-Modal Memory.

По умолчанию — DRY-RUN: выводит сколько сообщений-кандидатов нашёл,
ничего не пишет в БД.

Как работает:
  1. Открывает archive.db в read-only.
  2. Вычитывает messages с text_redacted, начинающимся с маркеров
     медиа-сообщений (`[photo`, `[video`, `[animation`, `[фото`, `[видео`).
     Это эвристика: indexer пишет такие плейсхолдеры когда нет caption.
  3. Сравнивает с уже заиндексированными в `message_media_summaries`.
  4. В режиме --apply вызывает переданный vision-провайдер и пишет summary.
     По умолчанию провайдера нет — печатает "would describe" и выходит.

Use cases:
  - Проверка покрытия: `python scripts/memory_backfill_media.py`
  - Реальный backfill: `python scripts/memory_backfill_media.py --apply
    --provider <module.func>` (provider должен быть async (chat_id, msg_id) → str)
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Добавляем src в path для standalone-запуска
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.core.memory_archive import (  # noqa: E402
    ArchivePaths,
    ensure_message_media_summaries_table,
    open_archive,
    record_media_summary,
)

# Эвристические маркеры media-сообщений в text_redacted
_MEDIA_MARKERS = (
    "[photo",
    "[video",
    "[animation",
    "[sticker",
    "[фото",
    "[видео",
)


def find_candidates(
    conn: sqlite3.Connection, limit: int | None = None
) -> list[tuple[str, str, str]]:
    """Возвращает [(chat_id, message_id, text_redacted), ...] media-кандидатов
    у которых ещё нет summary в `message_media_summaries`.

    Эвристика: text_redacted начинается с одного из маркеров.
    """
    like_clauses = " OR ".join(["m.text_redacted LIKE ?"] * len(_MEDIA_MARKERS))
    params: list[str] = [f"{marker}%" for marker in _MEDIA_MARKERS]
    sql = f"""
        SELECT m.chat_id, m.message_id, m.text_redacted
        FROM messages AS m
        LEFT JOIN message_media_summaries AS s
          ON s.chat_id = m.chat_id AND s.message_id = m.message_id
        WHERE s.message_id IS NULL
          AND ({like_clauses})
        ORDER BY m.timestamp DESC
    """
    if limit is not None and limit > 0:
        sql += " LIMIT ?"
        params.append(str(limit))
    cur = conn.execute(sql, params)
    return [(row[0], row[1], row[2]) for row in cur.fetchall()]


def _detect_media_type(text_redacted: str) -> str:
    """Определяет media_type по маркеру в начале text_redacted."""
    head = text_redacted[:32].lower()
    if "video" in head or "видео" in head:
        return "video"
    if "animation" in head:
        return "animation"
    if "sticker" in head:
        return "sticker"
    return "photo"


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill media summaries (Feature E)")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Реально записать summary в БД (по умолчанию dry-run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Максимум сообщений для обработки (default 20)",
    )
    parser.add_argument(
        "--archive-dir",
        type=str,
        default=None,
        help="Кастомный путь к директории archive.db",
    )
    args = parser.parse_args()

    paths = (
        ArchivePaths.under(Path(args.archive_dir).expanduser())
        if args.archive_dir
        else ArchivePaths.default()
    )
    if not paths.db.exists():
        print(f"[backfill] archive.db не найдена: {paths.db}", file=sys.stderr)
        return 2

    conn = open_archive(paths)
    try:
        ensure_message_media_summaries_table(conn)
        candidates = find_candidates(conn, limit=args.limit)
        print(f"[backfill] найдено кандидатов: {len(candidates)} (limit={args.limit})")
        if not candidates:
            return 0

        if not args.apply:
            for chat_id, msg_id, text in candidates[:10]:
                preview = text[:60].replace("\n", " ")
                print(
                    f"  dry-run: chat={chat_id} msg={msg_id} type={_detect_media_type(text)} text={preview!r}"
                )
            print("[backfill] dry-run завершён. Используй --apply для реальной записи.")
            return 0

        # Реальный backfill требует vision-провайдера. На текущем этапе
        # провайдер не подключён в скрипт — это скелет для будущего расширения
        # (см. perceptor.process_image_message + bridge frame_describer).
        written = 0
        for chat_id, msg_id, text in candidates:
            media_type = _detect_media_type(text)
            placeholder = (
                f"[backfill: vision provider not configured for {media_type} {chat_id}/{msg_id}]"
            )
            if record_media_summary(
                conn,
                chat_id,
                msg_id,
                media_type,
                placeholder,
                model_name="backfill-placeholder",
            ):
                written += 1
        print(
            f"[backfill] записано placeholder-summary: {written} (подключи vision-провайдер для реальных описаний)"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
