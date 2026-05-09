# Test Coverage Gaps — Krab (12.04.2026)

## Методология
- Строки кода: `wc -l` на исходниках (без `.bak`)
- Тесты: подсчёт `def test_` в `tests/unit/`
- Покрытие: low (<5 тестов или нет файла), medium (5-20), high (>20)

---

## Таблица: модули src/

### src/*.py (корневые)

| Модуль | Строк | Тест-файл | Тестов | Покрытие |
|--------|-------|-----------|--------|----------|
| `openclaw_client.py` | 3275 | test_openclaw_client.py + test_openclaw_runtime_repair.py | 59+67=126 | **high** |
| `userbot_bridge.py` | 3021 | test_userbot_*.py (много файлов) | ~200+ | **high** |
| `model_manager.py` | 1117 | test_model_manager.py | 30 | **high** |
| `mcp_client.py` | 364 | test_mcp_client.py | 19 | **medium** |
| `config.py` | 646 | test_config.py + test_config_core.py | 4+19=23 | **medium** |
| `memory_engine.py` | 194 | test_memory_engine.py + test_memory_engine_resilience.py | 19+3=22 | **high** |
| `employee_templates.py` | 190 | test_employee_templates.py | 14 | **medium** |
| `reserve_bot.py` | 239 | test_reserve_bot.py | 28 | **high** |
| `mcp_hammerspoon_server.py` | 192 | test_mcp_hammerspoon_server.py | 15 | **medium** |
| `mcp_panel_server.py` | 234 | — | 0 | **LOW** |
| `cache_manager.py` | 166 | test_cache_manager.py | 12 | **medium** |
| `search_engine.py` | 34 | test_search_engine.py | 9 | **high** |
| `voice_engine.py` | 122 | test_voice_engine.py | 1 | **LOW** |
| `web_session.py` | 140 | — | 0 | **LOW** |
| `main.py` | 69 | — | 0 | **LOW** |

### src/core/*.py

| Модуль | Строк | Тест-файл | Тестов | Покрытие |
|--------|-------|-----------|--------|----------|
| `inbox_service.py` | 1687 | test_inbox_service.py | 26 | **medium** |
| `provider_manager.py` | 1065 | test_provider_manager.py + test_core_provider_manager.py | 57+56=113 | **high** |
| `auth_recovery_readiness.py` | 915 | test_auth_recovery_readiness.py | 18 | **medium** |
| `capability_registry.py` | 826 | test_capability_registry.py + test_capability_registry_web_endpoints.py | 4+5=9 | **LOW** |
| `scheduler.py` | 687 | test_scheduler.py + test_swarm_scheduler.py | 26+11=37 | **high** |
| `swarm_channels.py` | 684 | test_swarm_channels.py | 41 | **high** |
| `proactive_watch.py` | 541 | test_proactive_watch.py | 16 | **medium** |
| `access_control.py` | 495 | test_access_control.py | 17 | **medium** |
| `swarm.py` | 441 | test_core_swarm.py + test_swarm_core.py | 30+30=60 | **high** |
| `swarm_scheduler.py` | 420 | test_swarm_scheduler.py | 11 | **medium** |
| `shared_repo_switchover.py` | 446 | test_shared_repo_switchover.py | 4 | **LOW** |
| `translator_finish_gate.py` | 308 | test_translator_finish_gate.py | 5 | **low-medium** |
| `swarm_task_board.py` | 299 | test_core_swarm_task_board.py | 50 | **high** |
| `openclaw_workspace.py` | 299 | test_core_openclaw_workspace.py | 23 | **high** |
| `openclaw_runtime_signal_truth.py` | 294 | test_core_openclaw_runtime_signal_truth.py + test_openclaw_runtime_signal_truth.py | 31+32=63 | **high** |
| `cost_analytics.py` | 272 | test_cost_analytics.py + test_core_cost_analytics.py | 22+29=51 | **high** |
| `swarm_memory.py` | 261 | test_swarm_memory.py | 20 | **medium** |
| `cloud_key_probe.py` | 243 | test_cloud_key_probe.py | 3 | **LOW** |
| `provisioning_service.py` | 366 | test_core_provisioning_service.py | 49 | **high** |
| `swarm_verifier.py` | 206 | test_core_swarm_verifier.py | 32 | **high** |
| `runtime_policy.py` | 205 | test_core_runtime_policy.py | 29 | **high** |
| `ecosystem_health.py` | 370 | test_core_ecosystem_health.py | 28 | **high** |
| `local_health.py` | 194 | test_core_local_health.py | 29 | **high** |
| `cloud_gateway.py` | 358 | test_cloud_gateway.py + test_core_cloud_gateway.py | 39+42=81 | **high** |
| `mcp_registry.py` | 336 | test_mcp_registry.py + test_core_mcp_registry.py | 3+19=22 | **medium** |
| `chat_ban_cache.py` | 325 | test_chat_ban_cache.py | 19 | **medium** |
| `chat_capability_cache.py` | 377 | test_chat_capability_cache.py | 19 | **medium** |
| `shared_worktree_permissions.py` | 323 | test_core_shared_worktree_permissions.py | 23 | **medium** |
| `swarm_bus.py` | 329 | test_core_swarm_bus.py | 32 | **high** |
| `swarm_artifact_store.py` | 144 | test_core_swarm_artifact_store.py | 21 | **high** |
| `swarm_team_listener.py` | 167 | test_core_swarm_team_listener.py | 28 | **high** |
| `swarm_team_prompts.py` | 50 | — | 0 | **LOW** |
| `translator_engine.py` | 129 | test_core_translator_engine.py | 22 | **high** |
| `translator_live_trial_preflight.py` | 149 | — | 0 | **LOW** |
| `translator_mobile_onboarding.py` | 185 | — | 0 | **LOW** |
| `translator_runtime_profile.py` | 99 | — | 0 | **LOW** |
| `translator_session_state.py` | 102 | — | 0 | **LOW** |
| `language_detect.py` | 62 | test_core_language_detect.py | 22 | **high** |
| `telegram_rate_limiter.py` | 172 | test_telegram_rate_limiter.py | 8 | **medium** |
| `observability.py` | 162 | test_core_observability.py | 35 | **high** |
| `error_handler.py` | 113 | test_core_error_handler.py | 20 | **high** |
| `exceptions.py` | 158 | test_core_exceptions.py | 25 | **high** |
| `silence_mode.py` | 166 | test_silence_mode.py | 9 | **medium** |
| `spam_filter.py` | 55 | test_spam_filter.py | 7 | **high** |
| `subprocess_env.py` | 44 | test_core_subprocess_env.py | 10 | **high** |
| `lm_studio_auth.py` | 122 | test_lm_studio_auth.py | 5 | **low-medium** |
| `lm_studio_health.py` | 13 | test_lm_studio_health.py | 5 | **high** |
| `model_aliases.py` | 111 | test_model_aliases.py | 2 | **LOW** |
| `model_config.py` | 38 | test_core_model_config.py | 6 | **medium** |
| `model_router.py` | 96 | test_core_model_router.py | 18 | **high** |
| `model_types.py` | 46 | test_core_model_types.py | 10 | **high** |
| `operator_identity.py` | 94 | test_operator_identity.py | 3 | **LOW** |
| `openclaw_secrets_runtime.py` | 189 | test_openclaw_secrets_runtime.py | 4 | **LOW** |
| `openclaw_runtime_models.py` | 46 | — | 0 | **LOW** |
| `routing_errors.py` | 146 | test_routing_errors.py | 7 | **medium** |
| `logger.py` | 92 | test_core_logger.py | 4 | **LOW** |
| `voice_gateway_control_plane.py` | 112 | test_core_voice_gateway_control_plane.py | 31 | **high** |
| `handoff_auto_export.py` | 0 | test_handoff_auto_export.py | 5 | n/a (файл пустой) |

### src/handlers/*.py

| Модуль | Строк | Тест-файл | Тестов | Покрытие |
|--------|-------|-----------|--------|----------|
| `command_handlers.py` | 3717 | test_command_handlers_agent_swarm.py + test_command_handlers_status.py + test_inbox_commands.py | 5+1+8=14 | **LOW** |

### src/integrations/*.py

| Модуль | Строк | Тест-файл | Тестов | Покрытие |
|--------|-------|-----------|--------|----------|
| `browser_bridge.py` | 978 | test_integrations_clients.py (частично) | ~10 | **LOW** |
| `macos_automation.py` | 881 | — | 0 | **LOW** |
| `voice_gateway_client.py` | 579 | test_integrations_clients.py (частично) | ~5 | **LOW** |
| `browser_ai_provider.py` | 408 | — | 0 | **LOW** |
| `voice_gateway_subscriber.py` | 199 | — | 0 | **LOW** |
| `krab_ear_client.py` | 176 | test_krab_ear_watchdog.py | 2 | **LOW** |
| `hammerspoon_bridge.py` | 158 | — | 0 | **LOW** |
| `cli_runner.py` | 146 | — | 0 | **LOW** |
| `tor_bridge.py` | 129 | — | 0 | **LOW** |

### src/modules/*.py

| Модуль | Строк | Тест-файл | Тестов | Покрытие |
|--------|-------|-----------|--------|----------|
| `web_app.py` | 13571 | test_web_app_runtime_endpoints.py + test_web_app_assistant_query.py + test_web_api_endpoints.py | 133+4+24=161 | **medium** (161 тестов / 13571 строк = ~1.2%) |
| `web_router_compat.py` | 964 | test_web_router_compat.py | 9 | **LOW** |
| `web_app_swarm_dashboard.py` | 455 | — | 0 | **LOW** |
| `web_app_stats_dashboard.py` | 429 | — | 0 | **LOW** |
| `web_app_inbox_dashboard.py` | 427 | — | 0 | **LOW** |
| `web_app_costs_dashboard.py` | 304 | — | 0 | **LOW** |
| `web_app_landing_page.py` | 332 | — | 0 | **LOW** |
| `perceptor.py` | 120 | — | 0 | **LOW** |

### src/skills/*.py

| Модуль | Строк | Тест-файл | Тестов | Покрытие |
|--------|-------|-----------|--------|----------|
| `mercadona.py` | 464 | test_mercadona.py | 7 | **low-medium** |
| `web_search.py` | 47 | — | 0 | **LOW** |
| `imessage.py` | 45 | — | 0 | **LOW** |
| `crypto.py` | 21 | — | 0 | **LOW** |

---

## Топ-5 модулей с наихудшим покрытием (по: строки × отсутствие тестов)

| # | Модуль | Строк | Тестов | Приоритет |
|---|--------|-------|--------|-----------|
| 1 | `handlers/command_handlers.py` | **3717** | 14 (0.4%) | 🔴 КРИТИЧНО |
| 2 | `modules/web_app.py` | **13571** | 161 (~1.2%) | 🔴 КРИТИЧНО |
| 3 | `integrations/macos_automation.py` | **881** | 0 | 🔴 КРИТИЧНО |
| 4 | `integrations/browser_bridge.py` | **978** | ~10 | 🔴 КРИТИЧНО |
| 5 | `integrations/browser_ai_provider.py` | **408** | 0 | 🔴 HIGH |

### Следующие в очереди (large, 0 тестов):
- `modules/web_router_compat.py` — 964 строки, 9 тестов (LOW)
- `integrations/voice_gateway_client.py` — 579 строк, ~5 тестов (LOW)
- `core/capability_registry.py` — 826 строк, 9 тестов (LOW)
- `core/shared_repo_switchover.py` — 446 строк, 4 теста (LOW)

---

## Рекомендации — что тестировать в первую очередь

### 1. `handlers/command_handlers.py` (3717 строк, 14 тестов)
**Самый большой необокрытый файл.** Содержит все команды userbot: `!swarm`, `!search`, `!silence`, routing-логику. Критичен для регрессий.
- Тестировать: каждую команду-хендлер через mock Telegram сообщений, ACL-проверки, error paths

### 2. `modules/web_app.py` (13571 строк, 161 тест)
Несмотря на большое число тестов, покрытие ~1.2% кода. Огромный файл с сотнями endpoint'ов.
- Тестировать: все `/api/*` endpoints, auth middleware, error handling, rate-limiting
- Приоритет: новые endpoints из коммитов `feat: GET /api/endpoints`, `feat: GET /api/version`

### 3. `integrations/macos_automation.py` (881 строк, 0 тестов)
AppleScript/osascript автоматизация — хрупкая по природе, ошибки здесь молча ломают workflow.
- Тестировать: через mock subprocess, validation входных данных, error handling при failed osascript

### 4. `integrations/browser_bridge.py` (978 строк, ~10 тестов)
CDP-подключение к Chrome, Playwright scraping. Используется для browser AI provider.
- Тестировать: инициализация, reconnect logic, error paths при недоступном Chrome

### 5. `core/capability_registry.py` (826 строк, 9 тестов)
Реестр возможностей — центральный для routing и feature-detection.
- Тестировать: регистрация/lookup capabilities, per-chat overrides, cache invalidation

### Быстрые wins (translator-pipeline, нет тестов совсем):
- `core/translator_live_trial_preflight.py` — 149 строк
- `core/translator_mobile_onboarding.py` — 185 строк  
- `core/translator_session_state.py` — 102 строки
- `core/translator_runtime_profile.py` — 99 строк
- Все 4 файла = translator subsystem без тестов → один тест-файл `test_translator_subsystem.py` закроет gap

### integrations/ без тестов:
- `tor_bridge.py`, `hammerspoon_bridge.py`, `cli_runner.py`, `voice_gateway_subscriber.py` — все можно покрыть mock-тестами за 1-2 сессии

---

*Сгенерировано: 12.04.2026*
