# -*- coding: utf-8 -*-
"""
APIRouter modules для Owner Panel — Phase 1 scaffold (Session 24).

Phase 1 — создаём structure для будущей extraction из
``src/modules/web_app.py`` (15,822 LOC, 257 endpoints) → 12 router-modules.

⚠️ Имя ``web_routers/`` (не ``web_app/``) — потому что
``src/modules/web_app.py`` существует как модуль, и Python не позволяет
одновременно иметь file и directory с одним именем. См.
``docs/CODE_SPLITS_PLAN.md`` § "Architecture decision" — оригинальный
план упоминал ``web_app/``, адаптация здесь.

На момент Phase 1:
- _context.py: RouterContext dataclass для DI.
- _helpers.py: promoted @classmethod helpers (Phase 2 заполнение).
- остальные модули — НЕ созданы.

Backward compatibility: ``web_app.py`` остаётся нетронутым.
"""

from __future__ import annotations
