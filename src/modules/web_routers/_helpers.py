# -*- coding: utf-8 -*-
"""
Promoted helpers для router модулей — Phase 1 scaffold (Session 24).

Phase 1 — пустой placeholder. В Phase 2+ сюда переедут @classmethod
helpers из ``WebApp`` которые НЕ зависят от ``self.deps`` (т.е. чистые
функции типа конфигурации, normalization, file lookup).

Кандидаты на promotion (примерные, точная карта в Phase 2 audit):
- ``_project_root()``
- ``_load_openclaw_models_json()``
- ``_normalize_route_meta()``
- ``_format_runtime_state()``

См. ``docs/CODE_SPLITS_PLAN.md`` § "Critical details" → @classmethod chain.
"""

from __future__ import annotations
