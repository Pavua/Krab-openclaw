# -*- coding: utf-8 -*-
"""
Глобальные pytest-настройки для тестового контура Krab.

Зачем:
- Подавляем известный внешний DeprecationWarning из pyrogram, который не относится
  к коду проекта и не влияет на стабильность функционала.
"""

import warnings


warnings.filterwarnings(
    "ignore",
    message="There is no current event loop",
    category=DeprecationWarning,
)

