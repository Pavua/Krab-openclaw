# -*- coding: utf-8 -*-
"""Helpers для /api/memory/heatmap.

Выделено отдельным модулем чтобы тесты могли вызвать ту же логику
формирования SQL bucket-выражения, что и боевой endpoint.
"""

from __future__ import annotations


def build_bucket_sql_expr(bucket_hours: int) -> str:
    """Возвращает SQL-выражение, агрегирующее timestamp в bucket из ``bucket_hours`` часов.

    Колонка ``timestamp`` в archive.db хранится как ISO-8601 TEXT
    ("2026-04-10T08:00:00Z"), поэтому используем sqlite ``strftime('%s', ...)``
    для перевода в unix-эпоху.

    Случай ``bucket_hours == 24`` сохраняет историческую форму вывода
    ``YYYY-MM-DD`` для backward-совместимости с дашбордом V4.
    Иначе результат — ISO-8601 datetime начала bucket'а в UTC
    (``YYYY-MM-DDTHH:MM:SSZ``), выровненный по unix-эпохе.
    """
    if bucket_hours <= 0:
        raise ValueError("bucket_hours must be positive")

    # Backward-compat: суточные bucket'ы рендерим как YYYY-MM-DD.
    if bucket_hours == 24:
        return "strftime('%Y-%m-%d', timestamp)"

    seconds = bucket_hours * 3600
    # (epoch // seconds) * seconds — выравнивание начала bucket'а.
    # strftime('%Y-%m-%dT%H:%M:%SZ', ts, 'unixepoch') — обратная конвертация.
    return (
        f"strftime('%Y-%m-%dT%H:%M:%SZ', "
        f"(CAST(strftime('%s', timestamp) AS INTEGER) / {seconds}) * {seconds}, "
        f"'unixepoch')"
    )
