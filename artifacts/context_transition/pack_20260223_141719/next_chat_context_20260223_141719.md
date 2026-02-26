# Контекст для нового диалога (anti-413)

- Дата: 2026-02-23 14:17:19 CET
- Ветка: `codex/queue-forward-reactions-policy`
- Коммит: `17d2fc8`

## 413-safe TL;DR (вставлять первым сообщением)

```text
[CHECKPOINT]
branch=codex/queue-forward-reactions-policy
head=17d2fc8
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
 M HANDOVER.md
 M MIGRATION.md
 M README.md
 M config/soul.md
 M docs/CHAT_TRANSITION_PLAYBOOK_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R6_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R9_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_LONG_R12_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_LONG_R13_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R4_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R5_RU.md
 M docs/EXTERNAL_PROMPT_NANOBANANA_UI_RU.md
 M docs/FRONTEND_R16_REPORT_RU.md
 M docs/Project_V5_Singularity.md
 M scratch/HANDOVER_KRAB_EAR.md
 M scratch/MIGRATION_STATUS.md
 M scratch/VPN_INSTRUCTIONS.md
 M scratch/openclaw_official/data/config/openclaw.json
 M scratch/openclaw_official/src/agents/context-window-guard.ts
 M src/core/model_manager.py
 M src/core/openclaw_client.py
 M src/core/swarm.py
 M src/core/task_queue.py
 M src/handlers/ai.py
 M src/handlers/commands.py
 M src/modules/perceptor.py
 M src/modules/web_app.py
 M src/web/index.html
 M src/web/prototypes/nano/index_redesign.html
 M task.md
 M tests/test_lms_control.py
 M tests/test_model_router_output_sanitizer.py
 M tests/test_model_router_phase_d.py
 M tests/test_model_router_stream_fallback.py
 M tests/test_openclaw_client_health.py
 M tests/test_web_app.py
?? .markdownlint.json
?? .playwright-cli/
?? REPORTS_R12.md
?? apply_r13.py
?? apply_r13_css.py
?? apply_r13_proper.py
?? docs/ANTIGRAVITY_TASK_PACK_R14_RU.md
?? docs/BACKEND_R16_REPORT_RU.md
?? docs/CODEX_WORKSTREAM_R14_RU.md
?? docs/R13_FRONTEND_REPORT_RU.md
?? docs/ops_incident_runbook.md
?? scripts/pre_release_smoke.command
?? scripts/test_fallback.py
?? scripts/test_fallback_v2.py
?? scripts/test_openclaw_auth.py
?? tests/test_r16_queue_sla_abort.py
?? tests/test_r17_agent_room.py
?? tests/test_r17_cloud_diagnostics.py
?? verify_r15.command
?? verify_r16.command
```

## Diff summary

```text
 .ralphy/.agent-rules.md                            |   8 +-
 .ralphy/templates/Dependencies.md                  |   8 +-
 .ralphy/templates/Instruction.md                   |  50 +--
 .ralphy/templates/Project Specs_AGENTS_UPDATED.md  |  66 ++--
 .../templates/Project Specs_PRD_Krab_Ultimate.md   | 102 ++---
 .ralphy/templates/prd-template-basic.md            |  84 +----
 HANDOVER.md                                        | 105 ++++--
 MIGRATION.md                                       |  16 +-
 README.md                                          |  36 +-
 config/soul.md                                     |  21 +-
 docs/CHAT_TRANSITION_PLAYBOOK_RU.md                |  14 +
 docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R6_RU.md |  53 +--
 docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R9_RU.md |  12 +
 ...ERNAL_PROMPT_GEMINI3PRO_FRONTEND_LONG_R12_RU.md |  37 +-
 ...ERNAL_PROMPT_GEMINI3PRO_FRONTEND_LONG_R13_RU.md |  30 +-
 docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R4_RU.md  |  20 +-
 docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R5_RU.md  |  40 +-
 docs/EXTERNAL_PROMPT_NANOBANANA_UI_RU.md           |   2 +-
 docs/FRONTEND_R16_REPORT_RU.md                     |  10 +-
 docs/Project_V5_Singularity.md                     |   5 +
 scratch/HANDOVER_KRAB_EAR.md                       |   1 +
 scratch/MIGRATION_STATUS.md                        |   6 +-
 scratch/VPN_INSTRUCTIONS.md                        |  66 ++--
 .../openclaw_official/data/config/openclaw.json    |   4 +-
 .../src/agents/context-window-guard.ts             |   4 +-
 src/core/model_manager.py                          | 418 +++++++++++++++++++--
 src/core/openclaw_client.py                        | 154 +++++++-
 src/core/swarm.py                                  | 107 ++++++
 src/core/task_queue.py                             |  23 +-
 src/handlers/ai.py                                 |  47 +++
 src/handlers/commands.py                           | 115 ++++++
 src/modules/perceptor.py                           |  14 +
 src/modules/web_app.py                             | 174 +++++++++
 src/web/index.html                                 | 232 +++++++++++-
 src/web/prototypes/nano/index_redesign.html        | 101 ++++-
 task.md                                            |   4 +
 tests/test_lms_control.py                          | 108 ++++++
 tests/test_model_router_output_sanitizer.py        |  15 +
 tests/test_model_router_phase_d.py                 |  32 ++
 tests/test_model_router_stream_fallback.py         |  45 ++-
 tests/test_openclaw_client_health.py               |  54 ++-
 tests/test_web_app.py                              | 111 +++++-
 42 files changed, 2138 insertions(+), 416 deletions(-)
```

## Последние коммиты

```text
17d2fc8 feat(r17): harden cloud tier fallback and add key diagnostics workflow
fbc7e2f docs: fix markdown linting in frontend R6-R8 prompts
d885dff feat(frontend): R16 sprint - Cloud UI & Queue Stability Dashboard
556c6e1 feat(r15): add cloud preflight gate and runtime ops metrics
5356b7c feat: R15 frontend polish - Ops Center v2 & Control UX
5b41440 fix(cloud): fail-fast guardrails for force_cloud routing
b9f37a4 feat(web): polish control panel UI and keep prototype compatibility
14177bc fix(runtime): harden watchdog gateway recovery and improve OpenClaw error diagnostics
595f00f feat(r14): Backend Stability Sprint A-D
b9e4d07 feat(email): add one-click mail access diagnostic
```

## Ключевые команды проверки

- `pytest -q tests/test_model_router_output_sanitizer.py tests/test_model_set_parser.py tests/test_forward_context.py tests/test_auto_reply_queue.py tests/test_handlers.py -k 'forward_context or auto_reply'`
- `./openclaw_runtime_repair.command`
- `./openclaw_signal_register.command`

## Следующий шаг (для нового чата)

Продолжай с приоритетом Signal link/pairing и стабилизации каналов. Сначала проверь runtime, потом Signal, затем обнови HANDOVER/ROADMAP только по факту верификации.

## Какие файлы приложить в новый диалог

1. `artifacts/context/next_chat_context_20260223_141719.md`
2. `AGENTS.md`
3. `HANDOVER.md`
4. `ROADMAP.md`
5. `docs/CHAT_TRANSITION_PLAYBOOK_RU.md`
