# Antigravity Backlog v8 (своя зона)

## Sprint Block A: Telegram Control Hardening (DONE)

1. [x] Улучшить `!summaryx` UX для ЛС:
   - явные ошибки по правам/недоступности чата,
   - аккуратный fallback picker.
2. [x] Добавить regression-тесты для негативных кейсов summary.
3. [x] Привести сообщения команд к единообразию (коротко + actionable).

## 🛡️ Phase 10: Swarm & MCP (Agentic OS Part 2)

**Goal:** Transform Krab into a "Swarm of Specialists" using MCP servers and hierarchical agent coordination.

### Core Architecture (Swarm Core)

- **Implement Swarm Orchestrator:**
  - Define inter-agent protocol.
  - Implement task delegation via `AgentRouter`.

## Sprint Block C: Voice Telegram Control (DONE)

1. [x] Усилить `!callstatus` деталями состояния.
2. [x] В `!calldiag` добавить сжатый “что делать дальше”.
3. [x] Тесты на ожидаемые ответы при offline Voice Gateway.

## Sprint Block D: Provisioning UX (DONE)

1. [x] Улучшить `!provision` ответы:
   - чёткий next-step после draft/preview.
2. [x] Добавить guardrails для confirm-flow.
3. [x] Тесты на ошибки валидации полей.

## Acceptance для Antigravity потока

1. Нет правок в зоне Codex.
2. `python scripts/check_workstream_overlap.py` -> overlap 0.
3. Тесты своей зоны зеленые.
