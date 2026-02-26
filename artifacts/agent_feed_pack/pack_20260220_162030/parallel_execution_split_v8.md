# Параллельная Разработка: Codex + Antigravity (v8)

## Цель
Разделить разработку 50/50 без конфликтов по файлам и ответственности, чтобы оба потока могли идти одновременно.

## Принцип split
1. Domain ownership: каждый поток владеет своим функциональным доменом.
2. File ownership: изменения вне своей зоны запрещены, кроме заранее согласованных интеграций.
3. Contract-first: пересечение только через API/контракты, а не через хаотичные правки одних и тех же файлов.

## Распределение 50/50

### Поток A (Codex)
1. OpenClaw-first слой и web-оркестрация.
2. Ops/observability/cost guardrails.
3. Web panel + web-native assistant API.
4. Интеграционные и regression тесты этих зон.

Файлы зоны A:
- `config/workstreams/codex_paths.txt`

### Поток B (Antigravity)
1. Telegram max control (summaryx, moderation, provisioning flows).
2. Voice-command слой и bridge к Voice Gateway.
3. Интеграция обработчиков и сценарии управления в Telegram.
4. Тесты этих зон.

Файлы зоны B:
- `config/workstreams/antigravity_paths.txt`

## Протокол синхронизации
1. Каждый поток работает в своей ветке `codex/*`.
2. Перед merge/rebase запускается:
   - `scripts/check_workstream_overlap.command`
3. Если скрипт показывает overlap в измененных файлах:
   - merge блокируется,
   - назначается owner по домену,
   - лишние правки выносятся в отдельный PR.

## Контрактные точки (разрешенные интеграции)
1. `src/main.py` — только через короткие integration commits.
2. `src/handlers/commands.py` — общие команды через явные секции и минимальный diff.
3. `task.md`/`HANDOVER.md` — обновляются обоими потоками после каждого крупного спринта.

## Рекомендованный next split (прямо сейчас)

### Codex (следующие спринты)
1. Завершить Phase B до Done: OAuth/browser provider flows + auto-remediation.
2. Расширить web-first operator режим (ops history, alert acknowledgement).
3. Усилить security web API (idempotency tokens, tighter audit trail).

### Antigravity (следующие спринты)
1. Telegram moderation v2 e2e и rule templates.
2. Telegram provisioning UX (preview/apply confirmations).
3. Voice command flows в Telegram + более детальные статусы звонков.

