<!--
Консервативный реестр кандидатов на перенос в _trash.
Ничего из этого списка не удаляется автоматически.
-->

# Cleanup Candidates

## Кандидаты (только после подтвержденного smoke)

1. `/Users/pablito/Antigravity_AGENTS/Krab Voice Gateway/start_gateway.command`
- Причина: дублирует запуск из `/scripts/start_gateway.command`, но использует другой venv (`.venv`).
- Риск: низкий после smoke `/scripts/start_gateway.command`.

2. `/Users/pablito/Antigravity_AGENTS/Краб/run_krab.sh`
- Причина: параллельная legacy-точка запуска, не является каноничным one-click.
- Риск: средний, переносить только после проверки `start_krab.command` + `Start_Full_Ecosystem.command`.

3. `/Users/pablito/Antigravity_AGENTS/Краб/run_krab.command`
- Причина: потенциальный дубль запускной логики.
- Риск: средний, требуются smoke и сравнение env.

4. `/Users/pablito/Antigravity_AGENTS/Краб/krab_ear.command`
- Причина: неканоничный путь управления Krab Ear из репозитория Krab.
- Риск: средний, переносить после подтверждения стабильности `/Users/pablito/Antigravity_AGENTS/Krab Ear/Start Krab Ear.command`.

## Правило переноса
- Перед переносом каждого кандидата обязателен smoke:
  - запуск штатного стартера;
  - health endpoints 200;
  - отсутствие критичных паттернов в логах.
