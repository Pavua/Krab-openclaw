# -*- coding: utf-8 -*-
"""Prometheus metrics для Krab — facade модуль (Wave 83).

Логика разнесена в пакет `src.core.metrics.*` по Wave-tied submodules:
- process — uptime, FloodWait, session corruption, startup, agent engine,
  Wave 51-A fallback counters, Wave 55-C timing histograms, Idea 23
  handler latency, guest LLM skip, adaptive rerank.
- google_bypass — Wave 20-B Google direct bypass.
- memory — Wave 22 + Wave 74 retrieval.
- thread_coherence — Feature K.
- probes — Wave 70 KraabUserbot weakref.
- smart_routing — Wave 73 5-stage pipeline.
- launchd — Wave 75 (on-scrape render).
- token_cost — Wave 78 FinOps tokens & cost.
- krab_ear — Wave 79 (on-scrape render).
- collect — `collect_metrics()` orchestrator.

Facade re-exports все публичные и протестированные private символы (включая
`_*`) для обратной совместимости со всеми существующими импортами
`from src.core.prometheus_metrics import ...`.
"""

from __future__ import annotations

from .metrics import *  # noqa: F401,F403 — facade re-exports
from .metrics import __all__  # noqa: F401 — публичный API
