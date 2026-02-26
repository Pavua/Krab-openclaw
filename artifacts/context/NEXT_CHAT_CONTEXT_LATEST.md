# Контекст для нового диалога (anti-413)

- Дата: 2026-02-24 15:01:33 CET
- Ветка: `codex/queue-forward-reactions-policy`
- Коммит: `17aa400`

## 413-safe TL;DR (вставлять первым сообщением)

```text
[CHECKPOINT]
branch=codex/queue-forward-reactions-policy
head=17aa400
focus=стабилизация каналов + web/control + приемка внешних задач
done=основные R-пакеты интегрированы, есть web control center и runtime API
next=принять свежие правки, прогнать targeted pytest, зафиксировать handoff
risks=шумные изменения из параллельных окон, возможные конфликты UI/API-контрактов
```

## Статус изменений (кратко)

```text
 M .env.example
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
 M docs/ANTIGRAVITY_BACKLOG_V8.md
 M docs/CHAT_TRANSITION_PLAYBOOK_RU.md
 M docs/E2E_THREE_PROJECTS.md
 M docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R6_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R9_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_LONG_R12_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_LONG_R13_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R4_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R5_RU.md
 M docs/EXTERNAL_PROMPT_NANOBANANA_UI_RU.md
 M docs/FRONTEND_R16_REPORT_RU.md
 M docs/Project_V5_Singularity.md
 M krab_core_daemon_start.command
 M krab_core_daemon_status.command
 M krab_core_daemon_stop.command
 M scratch/HANDOVER_KRAB_EAR.md
 M scratch/MIGRATION_STATUS.md
 M scratch/VPN_INSTRUCTIONS.md
 M scratch/openclaw_official/data/config/openclaw.json
 M scratch/openclaw_official/src/agents/context-window-guard.ts
 M scripts/live_ecosystem_e2e.py
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
 M start_krab_ear_backend.command
 M status_krab_ear_backend.command
 M stop_krab_ear_backend.command
 M task.md
 M tests/test_lms_control.py
 M tests/test_model_local_health_probe.py
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
?? docs/ANTIGRAVITY_TASK_PACK_R18_RU.md
?? docs/ANTIGRAVITY_TASK_PACK_R19_2WINDOWS_RU.md
?? docs/ANTIGRAVITY_TASK_PACK_R20_2WINDOWS_RU.md
?? docs/ANTIGRAVITY_TASK_PACK_R21_2WINDOWS_RU.md
?? docs/BACKEND_R16_REPORT_RU.md
?? docs/CODEX_WORKSTREAM_R14_RU.md
?? docs/EXTERNAL_PROMPT_AG_R19_BACKEND_RU.md
?? docs/EXTERNAL_PROMPT_AG_R19_FRONTEND_RU.md
?? docs/EXTERNAL_PROMPT_AG_R20_BACKEND_RU.md
?? docs/EXTERNAL_PROMPT_AG_R20_FRONTEND_RU.md
?? docs/EXTERNAL_PROMPT_AG_R21_BACKEND_RU.md
?? docs/EXTERNAL_PROMPT_AG_R21_FRONTEND_RU.md
?? docs/R13_FRONTEND_REPORT_RU.md
?? docs/ops_incident_runbook.md
?? output/
?? scripts/krab_core_health_watch.command
?? scripts/krab_core_health_watch.py
?? scripts/live_channel_smoke.command
?? scripts/live_channel_smoke.py
?? scripts/lmstudio_idle_guard.command
?? scripts/lmstudio_idle_guard.py
?? scripts/pre_release_smoke.command
?? scripts/r20_merge_gate.command
?? scripts/r20_merge_gate.py
?? scripts/test_fallback.py
?? scripts/test_fallback_v2.py
?? scripts/test_openclaw_auth.py
?? tests/test_krab_core_health_watch.py
?? tests/test_live_channel_smoke.py
?? tests/test_lmstudio_idle_guard.py
?? tests/test_r16_queue_sla_abort.py
?? tests/test_r17_agent_room.py
?? tests/test_r17_cloud_diagnostics.py
?? verify_r15.command
?? verify_r16.command
```

## Diff summary

```text
 .env.example                                       |   2 +
 .ralphy/.agent-rules.md                            |  16 +-
 .ralphy/templates/Dependencies.md                  |   8 +-
 .ralphy/templates/Instruction.md                   |  42 +-
 .ralphy/templates/Project Specs_AGENTS_UPDATED.md  |  66 +--
 .../templates/Project Specs_PRD_Krab_Ultimate.md   | 102 ++--
 .ralphy/templates/prd-template-basic.md            |  84 +---
 HANDOVER.md                                        | 514 +++++++++++++++++++--
 MIGRATION.md                                       |  16 +-
 README.md                                          |  36 +-
 config/soul.md                                     |  18 +-
 docs/ANTIGRAVITY_BACKLOG_V8.md                     |   5 +-
 docs/CHAT_TRANSITION_PLAYBOOK_RU.md                |  14 +
 docs/E2E_THREE_PROJECTS.md                         |   2 +-
 docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R6_RU.md |  53 ++-
 docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R9_RU.md |  12 +
 ...ERNAL_PROMPT_GEMINI3PRO_FRONTEND_LONG_R12_RU.md |  37 +-
 ...ERNAL_PROMPT_GEMINI3PRO_FRONTEND_LONG_R13_RU.md |  30 +-
 docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R4_RU.md  |  20 +-
 docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R5_RU.md  |  40 +-
 docs/EXTERNAL_PROMPT_NANOBANANA_UI_RU.md           |   2 +-
 docs/FRONTEND_R16_REPORT_RU.md                     |  10 +-
 docs/Project_V5_Singularity.md                     |  90 ++--
 krab_core_daemon_start.command                     |  96 +++-
 krab_core_daemon_status.command                    |  42 ++
 krab_core_daemon_stop.command                      |  38 +-
 scratch/HANDOVER_KRAB_EAR.md                       |  68 +--
 scratch/MIGRATION_STATUS.md                        |  19 +-
 scratch/VPN_INSTRUCTIONS.md                        |  66 +--
 .../openclaw_official/data/config/openclaw.json    |   4 +-
 .../src/agents/context-window-guard.ts             |   4 +-
 scripts/live_ecosystem_e2e.py                      |   5 +-
 src/core/model_manager.py                          | 455 ++++++++++++++++--
 src/core/openclaw_client.py                        | 199 +++++++-
 src/core/swarm.py                                  | 107 +++++
 src/core/task_queue.py                             |  23 +-
 src/handlers/ai.py                                 |  65 ++-
 src/handlers/commands.py                           | 115 +++++
 src/modules/perceptor.py                           |  14 +
 src/modules/web_app.py                             | 190 ++++++++
 src/web/index.html                                 | 333 ++++++++++++-
 src/web/prototypes/nano/index_redesign.html        | 101 +++-
 start_krab_ear_backend.command                     | 120 +++++
 status_krab_ear_backend.command                    |  28 ++
 stop_krab_ear_backend.command                      | 102 +++-
 task.md                                            |   3 +
 tests/test_lms_control.py                          | 108 +++++
 tests/test_model_local_health_probe.py             |  43 ++
 tests/test_model_router_output_sanitizer.py        |  15 +
 tests/test_model_router_phase_d.py                 |  32 ++
 tests/test_model_router_stream_fallback.py         |  45 +-
 tests/test_openclaw_client_health.py               | 102 +++-
 tests/test_web_app.py                              | 121 ++++-
 53 files changed, 3344 insertions(+), 538 deletions(-)
```

## Последние коммиты

```text
17aa400 feat(R20): укрепление EcosystemHealthService — per-source timeout guard, degraded-флаг, _diagnostics.latency_summary
17d2fc8 feat(r17): harden cloud tier fallback and add key diagnostics workflow
fbc7e2f docs: fix markdown linting in frontend R6-R8 prompts
d885dff feat(frontend): R16 sprint - Cloud UI & Queue Stability Dashboard
556c6e1 feat(r15): add cloud preflight gate and runtime ops metrics
5356b7c feat: R15 frontend polish - Ops Center v2 & Control UX
5b41440 fix(cloud): fail-fast guardrails for force_cloud routing
b9f37a4 feat(web): polish control panel UI and keep prototype compatibility
14177bc fix(runtime): harden watchdog gateway recovery and improve OpenClaw error diagnostics
595f00f feat(r14): Backend Stability Sprint A-D
```

## Ключевые команды проверки

- `pytest -q tests/test_model_router_output_sanitizer.py tests/test_model_set_parser.py tests/test_forward_context.py tests/test_auto_reply_queue.py tests/test_handlers.py -k 'forward_context or auto_reply'`
- `./openclaw_runtime_repair.command`
- `./openclaw_signal_register.command`

## Следующий шаг (для нового чата)

Продолжай с приоритетом Signal link/pairing и стабилизации каналов. Сначала проверь runtime, потом Signal, затем обнови HANDOVER/ROADMAP только по факту верификации.

## Какие файлы приложить в новый диалог

1. `artifacts/context/next_chat_context_20260224_150133.md`
2. `AGENTS.md`
3. `HANDOVER.md`
4. `ROADMAP.md`
5. `docs/CHAT_TRANSITION_PLAYBOOK_RU.md`
