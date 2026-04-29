# -*- coding: utf-8 -*-
"""
Domain modules для команд Telegram userbot.

Phase 1 scaffold (Session 24): создаём structure для будущей extraction
из ``src/handlers/command_handlers.py`` (19,637 LOC, 175+ команд) → 11
domain-modules. См. ``docs/CODE_SPLITS_PLAN.md``.

На момент Phase 1:
- _shared.py: общие утилиты (_reply_tech, _parse_toggle_arg, и т.п.) —
  copy of helpers из command_handlers.py для будущего перехода.
- остальные модули — НЕ созданы (extraction в Phase 2+).

Backward compatibility: ``command_handlers.py`` остаётся нетронутым
с оригинальными функциями. _shared.py — additive scaffold, не replacement.
Будущие extraction phases удалят дубликаты после миграции callers.
"""

from __future__ import annotations
