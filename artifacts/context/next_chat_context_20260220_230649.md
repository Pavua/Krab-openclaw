# Контекст для нового диалога (anti-413)

- Дата: 2026-02-20 23:06:49 CET
- Ветка: `codex/queue-forward-reactions-policy`
- Коммит: `9506db9`

## 413-safe TL;DR (вставлять первым сообщением)

```text
[CHECKPOINT]
branch=codex/queue-forward-reactions-policy
head=9506db9
focus=стабилизация каналов + web/control + приемка внешних задач
done=основные R-пакеты интегрированы, есть web control center и runtime API
next=принять свежие правки, прогнать targeted pytest, зафиксировать handoff
risks=шумные изменения из параллельных окон, возможные конфликты UI/API-контрактов
```

## Статус изменений (кратко)

```text
 M .ralphy/.agent-rules.md
 M .runtime/krab_core.lock
 M HANDOVER.md
 M artifacts/memory/312322764/history.jsonl
 M docs/CHAT_TRANSITION_PLAYBOOK_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R6_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R9_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R4_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R5_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R6_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R7_RU.md
 M docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R8_RU.md
 M prepare_next_chat_context.command
 M scripts/validate_web_runtime_parity.py
 M src/core/ecosystem_health.py
 M src/core/model_manager.py
 M src/core/watchdog.py
 M src/modules/web_app.py
 M src/web/index.html
 M src/web/prototypes/nano/index_redesign.html
 M src/web/prototypes/nano/nano_theme.css
 M tests/test_web_app_r10.py
?? REPORTS_R12.md
?? build_transition_pack.command
?? scripts/build_transition_pack.py
?? tests/test_health_stage_d.py
?? tests/test_model_router_phase_a.py
?? tests/test_watchdog_stage_c.py
?? tests/test_web_api_stage_b.py
```

## Diff summary

```text
 .ralphy/.agent-rules.md                            |   3 -
 .runtime/krab_core.lock                            |   2 +-
 HANDOVER.md                                        |   2 +-
 artifacts/memory/312322764/history.jsonl           |  70 ++++
 docs/CHAT_TRANSITION_PLAYBOOK_RU.md                |   3 +-
 docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R6_RU.md |  53 +--
 docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R9_RU.md |  12 +
 docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R4_RU.md  |  20 +-
 docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R5_RU.md  |  40 +--
 docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R6_RU.md  |  48 ++-
 docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R7_RU.md  |  44 ++-
 docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R8_RU.md  |  44 ++-
 prepare_next_chat_context.command                  |  49 ++-
 scripts/validate_web_runtime_parity.py             |   4 +-
 src/core/ecosystem_health.py                       |  10 +-
 src/core/model_manager.py                          | 364 +++++++++------------
 src/core/watchdog.py                               |  47 ++-
 src/modules/web_app.py                             |  87 ++++-
 src/web/index.html                                 | 195 +++++------
 src/web/prototypes/nano/index_redesign.html        | 222 +++++++++++--
 src/web/prototypes/nano/nano_theme.css             |  22 ++
 tests/test_web_app_r10.py                          |  18 +-
 22 files changed, 855 insertions(+), 504 deletions(-)
```

## Последние коммиты

```text
9506db9 chore(parallel): add long-run external prompts for backend and frontend R12
690eee7 feat(backend): add local runtime lifecycle APIs, watchdog soft-healing, and diagnostics
9514b78 feat(web): accept Front10 control center UX and prototype id parity
62d2750 chore(git): harden ignore rules for secrets and certs
b08d785 feat: integrate R9 delivery, web control center hardening, and stability fixes
427ec1f feat(stt+channels): improve punctuation/accuracy and add signal/whatsapp one-click ops
a83d7b2 fix(openclaw): safe launchd restart command for prod gateway
f36cb1d chore(openclaw): add one-click key sync command and troubleshooting note
74f69b6 feat(models): cloud scan via openclaw cli + daemon command controls
4d45b5a Web panel: model catalog/apply UX + attachment upload + stable startup
```

## Ключевые команды проверки

- `pytest -q tests/test_model_router_output_sanitizer.py tests/test_model_set_parser.py tests/test_forward_context.py tests/test_auto_reply_queue.py tests/test_handlers.py -k 'forward_context or auto_reply'`
- `./openclaw_runtime_repair.command`
- `./openclaw_signal_register.command`

## Следующий шаг (для нового чата)

Продолжай с приоритетом Signal link/pairing и стабилизации каналов. Сначала проверь runtime, потом Signal, затем обнови HANDOVER/ROADMAP только по факту верификации.

## Какие файлы приложить в новый диалог

1. `artifacts/context/next_chat_context_20260220_230649.md`
2. `AGENTS.md`
3. `HANDOVER.md`
4. `ROADMAP.md`
5. `docs/CHAT_TRANSITION_PLAYBOOK_RU.md`
