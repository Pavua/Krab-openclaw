# Shared Repo Drift Decision Tree

## Быстрая логика

1. Совпадают ли branch и HEAD между текущей копией и `/Users/Shared/Antigravity_AGENTS/Краб`?
   - Если да, это не branch drift. Смотри права и writable-state.
   - Если нет, зафиксируй, какая копия является активной рабочей.
2. Нужен ли быстрый helper coding loop?
   - Если да, проверь `/Users/Shared/Antigravity_AGENTS/Краб-active`.
3. Есть ли риск live runtime ownership confusion?
   - Если да, не переходи в live-режим до switchover checklist.

## Что не делать

- не лечить drift через жёсткий reset;
- не пытаться уравнять account-local `~/.openclaw`;
- не править права на всё дерево, если конфликт в одном path.
