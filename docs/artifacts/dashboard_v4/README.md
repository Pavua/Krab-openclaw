# Dashboard V4 Screenshots — Session 16 (2026-04-21)

Скриншоты 7 production страниц Dashboard V4 Краба.
Сделаны через Playwright Chromium (headless), viewport 1440x900, full_page=true.

## Страницы

| Файл | URL | Описание |
|------|-----|----------|
| `costs_2026-04-21.png` | http://127.0.0.1:8080/v4/costs | FinOps дашборд: расходы по провайдерам, runway бюджета, history |
| `inbox_2026-04-21.png` | http://127.0.0.1:8080/v4/inbox | Inbox дашборд: список items, stale-open/processing, статусы |
| `swarm_2026-04-21.png` | http://127.0.0.1:8080/v4/swarm | Swarm дашборд: команды (traders/coders/analysts/creative), task board |
| `ops_2026-04-21.png` | http://127.0.0.1:8080/v4/ops | Ops дашборд: метрики, алерты, timeline событий, SLA |
| `settings_2026-04-21.png` | http://127.0.0.1:8080/v4/settings | Settings: конфигурация модели, voice, silence, routing |
| `translator_2026-04-21.png` | http://127.0.0.1:8080/v4/translator | Translator дашборд: статус сессии, языки, delivery matrix |
| `commands_2026-04-21.png` | http://127.0.0.1:8080/v4/commands | Commands Registry: 154+ команд с фильтрами и метаданными |

## Размеры файлов

| Файл | Размер |
|------|--------|
| costs_2026-04-21.png | 443 KB |
| inbox_2026-04-21.png | 721 KB |
| swarm_2026-04-21.png | 531 KB |
| ops_2026-04-21.png | 469 KB |
| settings_2026-04-21.png | 538 KB |
| translator_2026-04-21.png | 445 KB |
| commands_2026-04-21.png | 827 KB |

## Метод съёмки

```
playwright chromium headless=true
wait_until='networkidle' (costs/inbox/swarm/ops)
wait_until='commit' + sleep(5-8s) (settings/translator/commands — SSE polling)
```
