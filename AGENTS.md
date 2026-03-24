# AGENTS.md

Этот файл описывает агентные роли и правила разработки именно для репозитория `/Users/pablito/Antigravity_AGENTS/Краб`.
Боевые инструкции Краба для OpenClaw runtime не живут здесь. Канонический runtime-source-of-truth находится в `~/.openclaw/workspace-main-messaging`.

## Что является истиной

- Runtime OpenClaw: `~/.openclaw/openclaw.json`
- Runtime моделей агента `main`: `~/.openclaw/agents/main/agent/models.json`
- Runtime auth-профилей: `~/.openclaw/agents/main/agent/auth-profiles.json`
- Боевая persona и память Краба: `~/.openclaw/workspace-main-messaging/*`

## Что хранится в этом репозитории

- Код мостов, glue-логики и web-панели Краба
- Тесты, smoke-скрипты и `.command`-точки запуска
- Документация по архитектуре, плану работ и проверкам
- Developer-facing инструкции для агентной разработки

## Агентные роли для разработки

- `Architect`: держит целевую архитектуру, source-of-truth и правила интеграции с OpenClaw
- `Runtime Engineer`: отвечает за gateway, routing, userbot bridge, auth и каналы
- `UI Engineer`: отвечает за owner-панель `:8080`, не дублируя native dashboard `:18789`
- `QA / Release`: держит smoke, e2e, merge-gate и release discipline

## Правила работы

- Не дублировать нативный функционал OpenClaw, если он уже есть в runtime или CLI
- Канонический execution plan живёт в `docs/MASTER_PLAN_VNEXT_RU.md`
- Проценты и baseline брать только из `docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md`
- Для multi-account использовать shared repo `/Users/Shared/Antigravity_AGENTS/Краб`, но не шарить runtime/auth/browser state
- Пока legacy shared repo не reconciled, fastest safe path для другой учётки — `/Users/Shared/Antigravity_AGENTS/Краб-active`
- Протокол владения, прав и switchover для людей и агентов живёт в `docs/MULTI_ACCOUNT_SWITCHOVER_RU.md`
- Стратегия работы с drift между `pablito` и shared repo живёт в `docs/SHARED_REPO_STRATEGY_RU.md`
- Перед передачей работы на другую учётку предпочитать `Prepare Next Account Session.command`
- Все изменения делать через отдельные ветки `codex/...`
- Merge в `main` разрешён только после тестов и smoke-проверки
- Для каждого крупного этапа обновлять roadmap-документ со статусом и проверками
- Repo-level docs не должны притворяться runtime persona-файлами Краба
