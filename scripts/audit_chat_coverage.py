#!/usr/bin/env python3
"""Compare archive.db message counts per chat vs actual Telegram counts.

Output: docs/CHAT_COVERAGE_AUDIT.md table sorted by delta (under-indexed on top):
| Chat title | DB count | Telegram count | Delta | % coverage |

Если Telegram API access недоступен (нет pyrogram client) — просто
сравни внутри archive.db с expected: выведи любые чаты где count < 10.

Usage: venv/bin/python scripts/audit_chat_coverage.py [--threshold 100]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

DEFAULT_ARCHIVE_DB = Path("~/.openclaw/krab_memory/archive.db").expanduser()
DEFAULT_THRESHOLD = 100
DOCS_DIR = Path(__file__).parent.parent / "docs"
OUTPUT_MD = DOCS_DIR / "CHAT_COVERAGE_AUDIT.md"


# ---------------------------------------------------------------------------
# Чтение archive.db
# ---------------------------------------------------------------------------


def read_archive_chats(db_path: Path) -> list[dict[str, Any]]:
    """Возвращает список чатов с количеством сообщений из archive.db.

    Читает в режиме read-only через URI mode.
    """
    if not db_path.exists():
        return []

    try:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        # Fallback без URI mode
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

    try:
        # Пробуем объединить messages + chats для получения title
        try:
            rows = conn.execute(
                """
                SELECT
                    m.chat_id,
                    COALESCE(c.title, m.chat_id) AS title,
                    COUNT(*)            AS db_count
                FROM messages m
                LEFT JOIN chats c USING(chat_id)
                GROUP BY m.chat_id
                ORDER BY db_count DESC
                """
            ).fetchall()
        except sqlite3.OperationalError:
            # Если таблица chats недоступна — только messages
            rows = conn.execute(
                """
                SELECT chat_id, chat_id AS title, COUNT(*) AS db_count
                FROM messages
                GROUP BY chat_id
                ORDER BY db_count DESC
                """
            ).fetchall()

        return [
            {
                "chat_id": r["chat_id"],
                "title": r["title"] or r["chat_id"],
                "db_count": int(r["db_count"]),
            }
            for r in rows
        ]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Telegram truth (pyrogram)
# ---------------------------------------------------------------------------


async def fetch_telegram_counts(
    chat_ids: list[str],
) -> dict[str, int | None]:
    """Запрашивает реальное количество сообщений у Telegram через pyrogram.

    Возвращает dict{chat_id -> count | None}.
    None означает что чат недоступен / API не инициализирован.
    """
    result: dict[str, int | None] = {cid: None for cid in chat_ids}

    try:
        # Пробуем найти pyrogram клиент через userbot_bridge
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from src.userbot_bridge import client as pyrogram_client  # type: ignore[import]

        if pyrogram_client is None or not pyrogram_client.is_connected:
            return result

        for chat_id in chat_ids:
            try:
                # get_chat_history с limit=1 даёт доступ к total_count через messages count
                # Используем search_messages count approach
                peer_id = int(chat_id)
                # messages.search с limit=0 возвращает total_count в некоторых версиях pyrogram
                # Надёжнее — iter_messages с limit=1 и count через get_chat
                await pyrogram_client.get_chat(peer_id)
                # members_count не то что нужно; используем messages count из get_history
                # Pyrogram API: messages.get_history возвращает count как total
                history = await pyrogram_client.invoke(
                    __import__(
                        "pyrogram.raw.functions.messages", fromlist=["GetHistory"]
                    ).GetHistory(
                        peer=await pyrogram_client.resolve_peer(peer_id),
                        offset_id=0,
                        offset_date=0,
                        add_offset=0,
                        limit=1,
                        max_id=0,
                        min_id=0,
                        hash=0,
                    )
                )
                count = getattr(history, "count", None)
                if count is not None:
                    result[chat_id] = int(count)
            except Exception:  # noqa: BLE001
                # Недоступный чат — оставляем None
                pass

    except Exception:  # noqa: BLE001
        # pyrogram недоступен — все значения остаются None
        pass

    return result


# ---------------------------------------------------------------------------
# Основная логика аудита
# ---------------------------------------------------------------------------


def build_audit_rows(
    archive_chats: list[dict[str, Any]],
    tg_counts: dict[str, int | None],
    threshold: int,
) -> list[dict[str, Any]]:
    """Строит строки аудита, сортируя по:
    1. Чаты с известным TG count — по delta (under-indexed on top)
    2. Чаты с db_count < threshold и unknown TG — по db_count ASC
    """
    rows = []
    for chat in archive_chats:
        cid = chat["chat_id"]
        db_count = chat["db_count"]
        tg_count = tg_counts.get(cid)

        if tg_count is not None:
            delta = tg_count - db_count
            coverage_pct = round(100.0 * db_count / tg_count, 1) if tg_count > 0 else 100.0
            tg_str = str(tg_count)
            delta_str = str(delta)
            pct_str = f"{coverage_pct}%"
        else:
            delta = None
            coverage_pct = None
            tg_str = "unknown"
            delta_str = "—"
            pct_str = "—"

        rows.append(
            {
                "chat_id": cid,
                "title": chat["title"],
                "db_count": db_count,
                "tg_count": tg_count,
                "tg_str": tg_str,
                "delta": delta,
                "delta_str": delta_str,
                "coverage_pct": coverage_pct,
                "pct_str": pct_str,
                "under_threshold": db_count < threshold,
            }
        )

    # Сортировка: known TG first (по delta desc = under-indexed on top),
    # затем unknown (по db_count asc = наименее индексированные вверху)
    known = [r for r in rows if r["tg_count"] is not None]
    unknown = [r for r in rows if r["tg_count"] is None]

    known.sort(key=lambda r: r["delta"], reverse=True)
    unknown.sort(key=lambda r: r["db_count"])

    return known + unknown


def generate_markdown(
    rows: list[dict[str, Any]],
    threshold: int,
    db_path: Path,
    generated_at: str,
) -> str:
    """Генерирует Markdown отчёт аудита покрытия чатов."""

    under_threshold = [r for r in rows if r["under_threshold"]]
    total_chats = len(rows)
    total_db_msgs = sum(r["db_count"] for r in rows)

    lines = [
        "# Chat Coverage Audit",
        "",
        f"**Дата:** {generated_at}  ",
        f"**archive.db:** `{db_path}`  ",
        f"**Threshold (мало сообщений):** < {threshold}  ",
        f"**Всего чатов в archive.db:** {total_chats}  ",
        f"**Всего сообщений в archive.db:** {total_db_msgs}  ",
        f"**Чатов ниже порога {threshold}:** {len(under_threshold)}",
        "",
        "## Таблица покрытия",
        "",
        "Сортировка: under-indexed (большой delta) сверху. "
        "Чаты без Telegram truth отмечены «unknown».",
        "",
        "| Chat title | chat_id | DB count | TG count | Delta | % coverage |",
        "|-----------|---------|----------|----------|-------|------------|",
    ]

    for r in rows:
        # Обрезаем длинные заголовки
        title = r["title"][:40] if len(r["title"]) > 40 else r["title"]
        # Escape pipe chars
        title = title.replace("|", "\\|")
        lines.append(
            f"| {title} | {r['chat_id']} | {r['db_count']} "
            f"| {r['tg_str']} | {r['delta_str']} | {r['pct_str']} |"
        )

    if not rows:
        lines.append("| _(нет данных)_ | — | — | — | — | — |")

    lines += [
        "",
        "## Чаты с недостаточным покрытием",
        "",
    ]

    if under_threshold:
        lines.append(
            f"Найдено **{len(under_threshold)}** чатов с < {threshold} сообщений в archive.db:"
        )
        lines.append("")
        for r in under_threshold:
            tg_note = f" (TG: {r['tg_str']})" if r["tg_str"] != "unknown" else " (TG: неизвестно)"
            lines.append(f"- `{r['chat_id']}` — **{r['title']}**: {r['db_count']} msg{tg_note}")
    else:
        lines.append(f"Все чаты имеют ≥ {threshold} сообщений. Под-индексирования не обнаружено.")

    lines += [
        "",
        "## Рекомендации по бэкфиллу",
        "",
        "Для чатов с Delta > 1000 или coverage < 50%:",
        "",
        "1. Запустить `scripts/bootstrap_memory.py --chat-id <id>` для принудительной индексации",
        "2. Проверить `indexer_state` для данного chat_id",
        "3. Убедиться что чат не исключён из `MEMORY_WHITELIST`",
        "4. Использовать `!memory rebuild` или `/api/memory/indexer/backfill`",
        "",
        "---",
        f"_Сгенерировано скриптом `scripts/audit_chat_coverage.py` — {generated_at}_",
    ]

    return "\n".join(lines) + "\n"


def run_audit(
    db_path: Path = DEFAULT_ARCHIVE_DB,
    threshold: int = DEFAULT_THRESHOLD,
    skip_telegram: bool = False,
    output: Path = OUTPUT_MD,
) -> dict[str, Any]:
    """Основная точка входа (синхронная обёртка).

    Возвращает dict с данными аудита (также используется /api/memory/coverage-audit).
    """
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    archive_chats = read_archive_chats(db_path)

    chat_ids = [c["chat_id"] for c in archive_chats]

    # Telegram truth
    if skip_telegram or not chat_ids:
        tg_counts: dict[str, int | None] = {cid: None for cid in chat_ids}
    else:
        try:
            tg_counts = asyncio.run(fetch_telegram_counts(chat_ids))
        except RuntimeError:
            # Уже в event loop (например, тест)
            tg_counts = {cid: None for cid in chat_ids}

    rows = build_audit_rows(archive_chats, tg_counts, threshold)

    md = generate_markdown(rows, threshold, db_path, generated_at)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(md, encoding="utf-8")
        print(f"Записан отчёт: {output}")

    under = [r for r in rows if r["under_threshold"]]

    return {
        "generated_at": generated_at,
        "db_path": str(db_path),
        "threshold": threshold,
        "total_chats": len(rows),
        "total_db_messages": sum(r["db_count"] for r in rows),
        "under_threshold_count": len(under),
        "rows": rows,
        "markdown_path": str(output) if output else None,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Аудит покрытия чатов в archive.db")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_ARCHIVE_DB,
        help=f"Путь к archive.db (default: {DEFAULT_ARCHIVE_DB})",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD,
        help="Порог «мало сообщений» (default: 100)",
    )
    parser.add_argument(
        "--skip-telegram",
        action="store_true",
        help="Не обращаться к Telegram API (только archive.db)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_MD,
        help=f"Куда записать Markdown отчёт (default: {OUTPUT_MD})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Дополнительно вывести JSON на stdout",
    )
    args = parser.parse_args()

    result = run_audit(
        db_path=args.db,
        threshold=args.threshold,
        skip_telegram=args.skip_telegram,
        output=args.output,
    )

    print(
        f"\nИтог: {result['total_chats']} чатов, "
        f"{result['total_db_messages']} сообщений в DB, "
        f"{result['under_threshold_count']} под порогом {result['threshold']}"
    )

    if result["under_threshold_count"] > 0:
        print("\nЧаты с недостаточным покрытием:")
        for r in result["rows"]:
            if r["under_threshold"]:
                print(f"  [{r['chat_id']}] {r['title']}: {r['db_count']} msg")

    if args.json_output:
        # Убираем не-JSON-friendly поля перед выводом
        safe = {k: v for k, v in result.items() if k != "rows"}
        safe["rows"] = [
            {k: v for k, v in row.items() if k not in ("tg_count", "delta", "coverage_pct")}
            for row in result["rows"]
        ]
        print("\n" + json.dumps(safe, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
