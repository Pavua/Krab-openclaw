#!/usr/bin/env python3
"""
Запуск paper trading бота Краба.

Скрипт нужен как тонкая оболочка над `src.trading.paper_bot`, чтобы его можно
было запускать из Terminal, тестов, cron/LaunchAgent и macOS `.command` файла
одинаковым способом.
"""

from __future__ import annotations

from src.trading.paper_bot import main

if __name__ == "__main__":
    raise SystemExit(main())
