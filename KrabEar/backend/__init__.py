# -*- coding: utf-8 -*-
"""
Backend API shim для `KrabEar`.

Экспортирует минимальный набор классов:
- `StateStore`
- `BackendService`
"""

from .state_store import StateStore
from .service import BackendService

__all__ = ["StateStore", "BackendService"]

