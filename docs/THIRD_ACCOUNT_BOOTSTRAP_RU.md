# Third Account Bootstrap RU

## Цель

Поднять безопасную рабочую среду на ещё одной macOS-учётке без смешивания
`pablito` runtime и чужого `HOME`.

Этот runbook одинаково применим к `USER2`, `USER3` и любой следующей
вспомогательной учётке.

## Порядок

1. Открыть `/Users/Shared/Antigravity_AGENTS/Краб`.
2. Выполнить `Check New Account Readiness.command`.
3. Убедиться, что docs и handoff bundle читаются.
4. Если нужен live runtime:
   - сделать reclaim/freeze с исходной учётки;
   - выполнить bootstrap текущего `~/.openclaw`;
   - пройти локальные login helper'ы;
   - только потом запускать runtime.

## Что обязательно проверить перед продолжением чужой сессии

1. Какая ветка была рабочей в последнем handoff.
2. Есть ли свежий `ATTACH_SUMMARY_RU.md` или `AUDIT_STATUS_*.md`.
3. Не осталось ли account-local launcher fix'ов только в `HOME` другой учётки.
4. Не держит ли `pablito` или другая учётка порты `:8080`, `:18789`, `:8090`.

## Что копировать можно

- сам репозиторий;
- документацию;
- acceptance artifacts;
- handoff bundle.

## Что копировать нельзя как truth

- OAuth state;
- browser profile;
- `.env` с чужими секретами;
- runtime PID/state;
- предположение, что `:8080` уже принадлежит текущей учётке.
