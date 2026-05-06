#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 40-A: nightly audit entry point для LaunchAgent ai.krab.nightly-audit.

Запускается в 03:00 local, выполняет 8 audit-проверок параллельно,
шлёт markdown-отчёт в Saved Messages если найдены warn/critical.
"""
import asyncio
import sys

sys.path.insert(0, "/Users/pablito/Antigravity_AGENTS/Краб")

from src.core.nightly_self_audit import main

sys.exit(asyncio.run(main()))
