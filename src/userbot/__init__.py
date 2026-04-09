# -*- coding: utf-8 -*-
"""
`src.userbot` — пакет mixin-модулей, в которые пошагово декомпонуется
`src/userbot_bridge.py` (см. `docs/USERBOT_BRIDGE_SPLIT_PROPOSAL.md`).

Каждый mixin — чистый класс без собственного `__init__`, подмешивается
в `KraabUserbot` через множественное наследование. `self.*` namespace
остаётся общим, поэтому методы mixin'ов могут свободно обращаться
к состоянию, инициализированному в `KraabUserbot.__init__`.
"""
