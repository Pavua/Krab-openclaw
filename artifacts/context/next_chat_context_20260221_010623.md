# Контекст для нового диалога (anti-413)

- Дата: 2026-02-21 01:06:23 CET
- Ветка: `codex/queue-forward-reactions-policy`
- Коммит: `6e409aa`

## 413-safe TL;DR (вставлять первым сообщением)

```text
[CHECKPOINT]
branch=codex/queue-forward-reactions-policy
head=6e409aa
focus=стабилизация каналов + web/control + приемка внешних задач
done=основные R-пакеты интегрированы, есть web control center и runtime API
next=принять свежие правки, прогнать targeted pytest, зафиксировать handoff
risks=шумные изменения из параллельных окон, возможные конфликты UI/API-контрактов
```

## Статус изменений (кратко)

```text
 M .ralphy/.agent-rules.md
 M .ralphy/templates/Dependencies.md
 M .ralphy/templates/Instruction.md
 M ".ralphy/templates/Project Specs_AGENTS_UPDATED.md"
 M ".ralphy/templates/Project Specs_PRD_Krab_Ultimate.md"
 M .ralphy/templates/prd-template-basic.md
 M .runtime/krab_core.lock
 M HANDOVER.md
 M artifacts/memory/312322764/history.jsonl
 M docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R6_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R9_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_LONG_R12_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R4_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R5_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R6_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R7_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R8_RU.md
 M scratch/openclaw_official/data/config/openclaw.json
 M scratch/openclaw_official/src/agents/context-window-guard.ts
 M src/web/index.html
?? .markdownlint.json
?? REPORTS_R12.md
?? docs/ops_incident_runbook.md
?? scripts/pre_release_smoke.command
?? scripts/test_fallback.py
?? scripts/test_fallback_v2.py
?? scripts/test_openclaw_auth.py
?? tests/test_openclaw_model_autoswitch.py
?? tests/test_signal_ops_guard.py
```

## Diff summary

```text
 .ralphy/.agent-rules.md                            |   3 -
 .ralphy/templates/Dependencies.md                  |   8 +-
 .ralphy/templates/Instruction.md                   |  50 +++++-----
 .ralphy/templates/Project Specs_AGENTS_UPDATED.md  |  66 ++++++-------
 .../templates/Project Specs_PRD_Krab_Ultimate.md   | 102 ++++++++++-----------
 .ralphy/templates/prd-template-basic.md            |  84 ++---------------
 .runtime/krab_core.lock                            |   2 +-
 HANDOVER.md                                        |  28 +++---
 artifacts/memory/312322764/history.jsonl           |  79 ++++++++++++++++
 docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R6_RU.md |  53 ++++++-----
 docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R9_RU.md |  12 +++
 ...ERNAL_PROMPT_GEMINI3PRO_FRONTEND_LONG_R12_RU.md |  37 +++++---
 docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R4_RU.md  |  20 ++--
 docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R5_RU.md  |  40 ++++----
 docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R6_RU.md  |  48 +++++-----
 docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R7_RU.md  |  44 ++++-----
 docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R8_RU.md  |  44 ++++-----
 .../openclaw_official/data/config/openclaw.json    |   4 +-
 .../src/agents/context-window-guard.ts             |   4 +-
 src/web/index.html                                 |  34 ++++---
 20 files changed, 400 insertions(+), 362 deletions(-)
```

## Последние коммиты

```text
6e409aa fix(router): robust force_cloud error handling for telegram
7158ffd feat(ops): add one-click pre-release smoke runner with runtime diagnostics
85b230f fix(watchdog): add soft-heal cooldown to prevent repeated RAM unload storms
47bcb16 feat(backend): accept Back12 resilience hardening and web ops lifecycle APIs
fb78adb feat(web): accept Front12 UX hardening and fix duplicate OpenClaw handlers
5dab2d7 feat(ops): add one-click anti-413 transition pack workflow
9506db9 chore(parallel): add long-run external prompts for backend and frontend R12
690eee7 feat(backend): add local runtime lifecycle APIs, watchdog soft-healing, and diagnostics
9514b78 feat(web): accept Front10 control center UX and prototype id parity
62d2750 chore(git): harden ignore rules for secrets and certs
```

## Ключевые команды проверки

- `pytest -q tests/test_model_router_output_sanitizer.py tests/test_model_set_parser.py tests/test_forward_context.py tests/test_auto_reply_queue.py tests/test_handlers.py -k 'forward_context or auto_reply'`
- `./openclaw_runtime_repair.command`
- `./openclaw_signal_register.command`

## Следующий шаг (для нового чата)

Продолжай с приоритетом Signal link/pairing и стабилизации каналов. Сначала проверь runtime, потом Signal, затем обнови HANDOVER/ROADMAP только по факту верификации.

## Какие файлы приложить в новый диалог

1. `artifacts/context/next_chat_context_20260221_010623.md`
2. `AGENTS.md`
3. `HANDOVER.md`
4. `ROADMAP.md`
5. `docs/CHAT_TRANSITION_PLAYBOOK_RU.md`
