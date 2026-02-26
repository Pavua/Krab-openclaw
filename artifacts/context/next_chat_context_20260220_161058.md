# Контекст для нового диалога (anti-413)

- Дата: 2026-02-20 16:10:58 CET
- Ветка: `codex/queue-forward-reactions-policy`
- Коммит: `427ec1f`

## Статус изменений (кратко)

```text
 M .runtime/krab_core.lock
 M HANDOVER.md
 M ROADMAP.md
 M artifacts/memory/312322764/history.jsonl
 M docs/OPENCLAW_DASHBOARD_PLAYBOOK_RU.md
 M openclaw_signal_register.command
 M scripts/openclaw_channels_skills_bootstrap.py
 M src/core/model_manager.py
 M src/handlers/ai.py
 M src/skills/web_search.py
 M tests/test_model_router_output_sanitizer.py
 M tests/test_model_set_parser.py
?? docs/CHAT_TRANSITION_PLAYBOOK_RU.md
?? openclaw_runtime_repair.command
?? prepare_next_chat_context.command
```

## Diff summary

```text
 .runtime/krab_core.lock                       |   2 +-
 HANDOVER.md                                   |  30 +++
 ROADMAP.md                                    |   8 +
 artifacts/memory/312322764/history.jsonl      |  68 ++++++
 docs/OPENCLAW_DASHBOARD_PLAYBOOK_RU.md        |  41 ++++
 openclaw_signal_register.command              |  92 +++++++-
 scripts/openclaw_channels_skills_bootstrap.py | 146 +++++++++++++
 src/core/model_manager.py                     |  60 ++++-
 src/handlers/ai.py                            | 301 +++++++++++++++++++++++++-
 src/skills/web_search.py                      |  15 +-
 tests/test_model_router_output_sanitizer.py   |  34 +++
 tests/test_model_set_parser.py                |  27 ++-
 12 files changed, 803 insertions(+), 21 deletions(-)
```

## Ключевые команды проверки

- `pytest -q tests/test_model_router_output_sanitizer.py tests/test_model_set_parser.py tests/test_forward_context.py tests/test_auto_reply_queue.py tests/test_handlers.py -k 'forward_context or auto_reply'`
- `./openclaw_runtime_repair.command`
- `./openclaw_signal_register.command`

## Следующий шаг (для нового чата)

Продолжай с приоритетом Signal link/pairing и стабилизации каналов. Сначала проверь runtime, потом Signal, затем обнови HANDOVER/ROADMAP только по факту верификации.
