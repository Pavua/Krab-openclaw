# Owner Panel API — детальный справочник

Owner panel: `http://127.0.0.1:8080`. Обновлено: Session 38 (05.05.2026).
Self-documenting список: `GET /api/endpoints`

## Основные группы

### Health & Status
| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/health` | GET | Health check |
| `/api/health/deep` | GET | Расширенный health |
| `/api/health/lite` | GET | Лёгкий health |
| `/api/v1/health` | GET | Версионированный health (внешние мониторы) |
| `/api/uptime` | GET | Аптайм в секундах |
| `/api/version` | GET | Версия и данные сессии |
| `/api/stats` | GET | Статистика |
| `/api/sla` | GET | SLA метрики |

### Models & Routing
| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/model/switch` | POST | Сменить модель |
| `/api/model/status` | GET | Статус модели (включая reconciled_state) |
| `/api/model/recommend` | GET | Рекомендация модели |
| `/api/model/preflight` | POST | Preflight проверка модели |
| `/api/model/local/status` | GET | Статус LM Studio |
| `/api/model/local/load-default` | POST | Загрузить LM Studio модель |
| `/api/model/local/unload` | POST | Выгрузить LM Studio модель |
| `/api/model/explain` | GET | Объяснение выбора модели |
| `/api/model/catalog` | GET | Каталог моделей |
| `/api/model/apply` | POST | Применить конфигурацию модели |
| `/api/model/feedback` | GET/POST | Feedback по модели |
| `/api/model/provider-action` | POST | Действия с провайдером |
| `/api/openclaw/model-routing/status` | GET | Статус routing |
| `/api/openclaw/model-autoswitch/status` | GET | Авто-переключение |
| `/api/openclaw/model-autoswitch/apply` | POST | Применить авто-переключение |
| `/api/openclaw/routing/effective` | GET | Эффективный routing |
| `/api/openclaw/model-compat/probe` | GET | Probe совместимости модели |
| `/api/thinking/status` | GET | Статус режима thinking |
| `/api/thinking/set` | POST | Включить/выключить thinking |
| `/api/depth/status` | GET | Текущий уровень reasoning |

### Memory
| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/memory/search` | GET | Поиск по памяти |
| `/api/memory/stats` | GET | Статистика памяти |
| `/api/memory/phase2/status` | GET | Статус Memory Phase 2 |
| `/api/memory/doctor` | GET | Диагностика памяти |
| `/api/memory/doctor/fix` | POST | Авторемонт памяти |
| `/api/memory/heatmap` | GET | Heatmap памяти |
| `/api/memory/indexer` | GET | Статус индексера |
| `/api/memory/indexer/backfill` | POST | Backfill индекса |
| `/api/memory/indexer/flush` | POST | Flush индекса |
| `/api/memory/coverage-audit` | GET | Аудит покрытия памяти |

### Costs & FinOps
| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/costs/budget` | GET/POST | Просмотр и установка бюджета |
| `/api/costs/history` | GET | История расходов по провайдерам |
| `/api/costs/report` | GET | Cost report |
| `/api/costs/hourly` | GET | Почасовые расходы |
| `/api/costs/by_chat` | GET | Расходы по чатам |
| `/api/costs/by-tier` | GET | Расходы по тирам |
| `/api/costs/codex-quota` | GET | Квота Codex |
| `/api/ops/cost-report` | GET | Ops cost report |
| `/api/ops/runway` | GET | Runway бюджета |

### Inbox
| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/inbox/status` | GET | Статус inbox |
| `/api/inbox/items` | GET | Элементы inbox |
| `/api/inbox/update` | POST | Обновить элемент |
| `/api/inbox/create` | POST | Создать inbox item |
| `/api/inbox/events` | GET | События inbox |
| `/api/inbox/stale-processing` | GET | Зависшие в processing |
| `/api/inbox/stale-open` | GET | Зависшие open |
| `/api/inbox/stale-processing/remediate` | POST | Исправить processing |
| `/api/inbox/stale-open/remediate` | POST | Исправить open |
| `/api/inbox/bulk-ack-stale` | POST | Массово подтвердить stale |

### Swarm
| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/swarm/status` | GET | Статус свёрма |
| `/api/swarm/teams` | GET | Список команд |
| `/api/swarm/team/{team_name}` | GET | Детальная инфо о команде |
| `/api/swarm/stats` | GET | Статистика board+artifacts+listeners |
| `/api/swarm/reports` | GET | Markdown-отчёты |
| `/api/swarm/artifacts` | GET | Артефакты свёрма |
| `/api/swarm/artifacts/cleanup` | POST | Очистка старых артефактов |
| `/api/swarm/listeners` | GET | Статус слушателей команд |
| `/api/swarm/listeners/toggle` | POST | Управление слушателями |
| `/api/swarm/memory` | GET | Память свёрма |
| `/api/swarm/events` | GET | События свёрма |
| `/api/swarm/delegations/active` | GET | Активные делегирования |
| `/api/swarm/task-board` | GET | Kanban-доска задач |
| `/api/swarm/task-board/export` | GET | Export task board (csv/json) |
| `/api/swarm/tasks` | GET | Список задач |
| `/api/swarm/tasks/create` | POST | Создать задачу |
| `/api/swarm/task/{task_id}` | GET/DELETE | Детальная задача / удалить |
| `/api/swarm/task/{task_id}/update` | POST | Обновить статус |
| `/api/swarm/task/{task_id}/priority` | POST | Сменить приоритет |

### Translator
| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/translator/status` | GET | Статус переводчика |
| `/api/translator/auto` | POST | Авто-определение языка |
| `/api/translator/lang` | POST | Смена пары языков |
| `/api/translator/test` | GET | Быстрый тест перевода |
| `/api/translator/translate` | POST | Перевести текст |
| `/api/translator/languages` | GET | Поддерживаемые языки |
| `/api/translator/readiness` | GET | Готовность переводчика |
| `/api/translator/control-plane` | GET | Control plane |
| `/api/translator/session-inspector` | GET | Инспектор сессии |
| `/api/translator/mobile-readiness` | GET | Мобильная готовность |
| `/api/translator/delivery-matrix` | GET | Матрица доставки |
| `/api/translator/live-trial-preflight` | GET | Preflight live-trial |
| `/api/translator/mobile/onboarding` | GET | Онбординг мобильный |
| `/api/translator/bootstrap` | GET | Bootstrap данные |
| `/api/translator/history` | GET | История переводов |
| `/api/translator/session/start` | POST | Начать сессию перевода |
| `/api/translator/session/toggle` | POST | Пауза/возобновление |
| `/api/translator/session/action` | POST | Действие сессии |
| `/api/translator/session/summary` | POST | Сводка сессии |
| `/api/translator/session/escalate` | POST | Эскалация |
| `/api/translator/session/policy` | POST | Политика сессии |
| `/api/translator/session/quick-phrase` | POST | Быстрая фраза |
| `/api/translator/session/runtime-tune` | POST | Runtime настройка |

### Voice
| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/voice/toggle` | POST | Переключить голосовой режим |
| `/api/voice/profile` | GET | Голосовой профиль |
| `/api/voice/runtime` | GET | Runtime голосовых настроек |
| `/api/voice/runtime/update` | POST | Обновить runtime |

### OpenClaw
| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/openclaw/cron/status` | GET | Статус cron |
| `/api/openclaw/cron/jobs` | GET | Список cron jobs |
| `/api/openclaw/cron/jobs/create` | POST | Создать cron job |
| `/api/openclaw/cron/jobs/toggle` | POST | Вкл/выкл cron job |
| `/api/openclaw/cron/jobs/remove` | POST | Удалить cron job |
| `/api/openclaw/cron/jobs/run_now` | POST | Запустить сейчас |
| `/api/openclaw/channels/status` | GET | Статус каналов |
| `/api/openclaw/channels/runtime-repair` | POST | Починить каналы |
| `/api/openclaw/channels/signal-guard-run` | POST | Запустить signal guard |
| `/api/openclaw/runtime-config` | GET | Runtime конфигурация |
| `/api/openclaw/report` | GET | Отчёт OpenClaw |
| `/api/openclaw/deep-check` | GET | Глубокая проверка |
| `/api/openclaw/remediation-plan` | GET | План исправлений |
| `/api/openclaw/browser-smoke` | GET | Smoke-тест браузера |
| `/api/openclaw/browser/start` | POST | Запустить браузер |
| `/api/openclaw/browser/open-owner-chrome` | POST | Открыть Owner Chrome |
| `/api/openclaw/browser-mcp-readiness` | GET | Browser MCP готовность |
| `/api/openclaw/photo-smoke` | GET | Smoke-тест фото |
| `/api/openclaw/cloud` | GET | Cloud статус |
| `/api/openclaw/cloud/diagnostics` | GET | Cloud диагностика |
| `/api/openclaw/cloud/runtime-check` | GET | Cloud runtime проверка |
| `/api/openclaw/cloud/switch-tier` | POST | Сменить cloud tier |
| `/api/openclaw/cloud/tier/state` | GET | Состояние cloud tier |
| `/api/openclaw/cloud/tier/reset` | POST | Сброс cloud tier |
| `/api/openclaw/control-compat/status` | GET | Совместимость control |

### Runtime & Recovery
| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/runtime/recover` | POST | Восстановить runtime (exit 78 → HTTP 503 + recovery_loop_detected) |
| `/api/runtime/chat-session/clear` | POST | Очистить сессию чата |
| `/api/runtime/operator-profile` | GET | Профиль оператора |
| `/api/runtime/repair-active-shared-permissions` | POST | Починить permissions |
| `/api/runtime/handoff` | GET | Handoff данные |
| `/api/runtime/summary` | GET | Summary runtime |
| `/api/context/checkpoint` | POST | Сохранить checkpoint контекста |
| `/api/context/transition-pack` | POST | Transition pack контекста |
| `/api/context/latest` | GET | Последний контекст |

### Ecosystem & Ops
| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/ecosystem/health` | GET | Здоровье экосистемы |
| `/api/ecosystem/health/export` | GET | Экспорт health |
| `/api/ecosystem/health/debug` | GET | Debug health |
| `/api/ecosystem/capabilities` | GET | Возможности экосистемы |
| `/api/system/diagnostics` | GET | Диагностика системы |
| `/api/system/info` | GET | Системная информация хоста |
| `/api/ops/diagnostics` | GET | Ops диагностика |
| `/api/ops/metrics` | GET | Метрики |
| `/api/ops/timeline` | GET | Timeline событий |
| `/api/ops/runtime_snapshot` | GET | Runtime snapshot |
| `/api/ops/usage` | GET | Использование |
| `/api/ops/executive-summary` | GET | Executive summary |
| `/api/ops/report` | GET | Ops отчёт |
| `/api/ops/report/export` | GET | Экспорт отчёта |
| `/api/ops/bundle` | GET | Bundle данных |
| `/api/ops/bundle/export` | GET | Экспорт bundle |
| `/api/ops/alerts` | GET | Активные алерты |
| `/api/ops/history` | GET | История ops |
| `/api/ops/maintenance/prune` | POST | Очистка данных |
| `/api/ops/ack/{code}` | POST/DELETE | Подтвердить/снять alert |
| `/api/ops/openclaw-procs` | GET | OpenClaw процессы |

### Agent Engine (Session 35-36)
| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/agent-engine/status` | GET | Статус всех engine |
| `/api/agent-engine/runs` | GET | История запусков engine |
| `/api/agent-engine/comparison` | GET | Сравнение openclaw vs hermes |

### Smart Routing (Session 26)
| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/chat/policy/{chat_id}` | GET/POST | Политика чата |
| `/api/chat/policies` | GET | Все политики |

### Misc
| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/silence/status` | GET | Статус тишины |
| `/api/silence/toggle` | POST | Переключить режим тишины |
| `/api/notify/status` | GET | Статус уведомлений |
| `/api/notify/toggle` | POST | Переключить уведомления |
| `/api/notify` | POST | Отправить уведомление |
| `/api/assistant/query` | POST | Запрос к AI ассистенту |
| `/api/assistant/attachment` | POST | Прикрепить файл к запросу |
| `/api/assistant/capabilities` | GET | Возможности ассистента |
| `/api/diagnostics/smoke` | POST | Smoke-тест диагностики |
| `/api/provisioning/templates` | GET | Шаблоны provisioning |
| `/api/provisioning/drafts` | GET/POST | Черновики provisioning |
| `/api/provisioning/preview/{draft_id}` | GET | Preview черновика |
| `/api/provisioning/apply/{draft_id}` | POST | Применить черновик |
| `/api/capabilities/registry` | GET | Реестр возможностей |
| `/api/channels/capabilities` | GET | Возможности каналов |
| `/api/userbot/acl/status` | GET | Статус ACL |
| `/api/userbot/acl/update` | POST | Обновить ACL |
| `/api/policy` | GET | Политика |
| `/api/policy/matrix` | GET | Матрица политик |
| `/api/krab/restart_userbot` | POST | Перезапустить userbot |
| `/api/hooks/sentry` | POST | Sentry webhook |
| `/api/hooks/sentry/secret/rotate` | POST | Ротация Sentry secret |
| `/api/reactions/stats` | GET | Статистика реакций |
| `/api/reactions/incoming` | GET | Входящие реакции |
| `/api/mood/{chat_id}` | GET | Настроение чата |
| `/api/commands` | GET | Реестр команд |
| `/api/commands/{name}` | GET | Детальная инфо о команде |
| `/api/commands/usage` | GET | Использование команд |
| `/api/commands/usage/top` | GET | Топ команд |
| `/api/endpoints` | GET | Self-documenting список endpoints |
