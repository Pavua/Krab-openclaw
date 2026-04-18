# -*- coding: utf-8 -*-
"""
conftest.py — изоляция интеграционных тестов от env-зависимостей.

Гарантирует, что TELEGRAM_API_ID имеет валидное целое значение
для тестов, которые импортируют userbot_bridge (падает на int("")).
"""
from __future__ import annotations

import os


# Ставим дефолты до импорта модулей если переменные пустые
if not os.environ.get("TELEGRAM_API_ID"):
    os.environ["TELEGRAM_API_ID"] = "0"
if not os.environ.get("TELEGRAM_API_HASH"):
    os.environ["TELEGRAM_API_HASH"] = "test_hash"
