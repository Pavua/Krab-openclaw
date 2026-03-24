# LM Studio MCP Setup

## Что включать в Server Settings

### `Require Authentication`
- Держать **включенным**.
- Использовать отдельный API token для локального API LM Studio.
- Это обязательное условие, если ты хочешь давать API-клиентам доступ к MCP.

### `Allow per-request MCPs`
- **Включать только если LM Studio должен отдавать MCP-инструменты внешним API-клиентам**, а не только внутреннему чату LM Studio.
- Для нашего стека `Krab/OpenClaw + LM Studio` это **полезно и оправдано**, потому что тогда REST/API запросы тоже смогут использовать curated MCP-набор.
- Но это рискованно, если одновременно доступны `shell` или широкая `filesystem-home`, поэтому:
  - auth должен быть включен;
  - токен не должен быть публичным;
  - список MCP-серверов должен быть curated, а не случайным.

### `Обслуживание по локальной сети`
- Включать только если реально нужны вызовы с других устройств в LAN.
- Если весь стек живёт на одной машине, лучше оставить локальный сценарий и не расширять поверхность атаки без причины.

### `CORS`
- Не включать без необходимости.
- Для обычного server-to-server сценария `Krab/OpenClaw -> LM Studio` CORS не нужен.

## Какой режим рекомендован для этого проекта

### Рекомендованный профиль
1. `Require Authentication` = ON
2. `Allow per-request MCPs` = ON
3. `CORS` = OFF
4. `Обслуживание по локальной сети` = по ситуации

Почему:
- проекту нужны "глаза", файлы, docs и browser tooling не только в UI LM Studio, но и в API-контуре;
- при этом auth остаётся обязательным барьером;
- curated `mcp.json` ограничивает дрейф и снижает шанс поломанного runtime.

## Что добавлено в curated MCP stack

### Базовые must-have
- `filesystem` — доступ к файлам проекта Krab/OpenClaw
- `memory` — MCP память
- `lmstudio` — bridge к локальному LM Studio API
- `openai-chat` — внешний OpenAI bridge
- `openclaw-browser` — DevTools поверх OpenClaw browser relay
- `chrome-profile` — DevTools поверх обычного Chrome-профиля

### Optional по ключам
- `context7`
- `github`
- `firecrawl`
- `brave-search`

### High-risk, но полезные
- `shell`
- `filesystem-home`

## Как синхронизировать конфиг

Используй:

```bash
python scripts/sync_lmstudio_mcp.py --write --backup
```

или просто двойной клик по:

`Sync LM Studio MCP.command`

## Как добавить токены без вставки их в чат

Для LM Studio:

`Set LM Studio Token.command`

Для Context7:

`Set Context7 Token.command`

Для Firecrawl:

`Set Firecrawl Token.command`

Для Brave Search:

`Set Brave Token.command`

Эти обёртки записывают секреты в локальный `.env` через скрытый prompt.

Каноничные переменные:
- `LM_STUDIO_API_KEY`
- `CONTEXT7_API_KEY`
- `FIRECRAWL_API_KEY`
- `BRAVE_SEARCH_API_KEY`

## Важный нюанс про DevTools

### `openclaw-browser`
- Работает через `http://127.0.0.1:18800`
- Это хороший "рабочий" browser tool для автоматизации внутри нашего стека
- Отдельный relay Chrome больше не поднимается автоматически при обычном старте Krab.
- Если нужен старый eager-start сценарий для acceptance/debug, выставь `OPENCLAW_BROWSER_AUTOSTART=1` в `.env`

### `chrome-profile`
- Используй helper `new Open Owner Chrome Remote Debugging.command` или эквивалентный relaunch обычного Chrome владельца с `--remote-debugging-port=9222`
- Простого открытия `chrome://inspect/#remote-debugging` недостаточно: сам DevTools MCP не поднимает порт за тебя
- Owner panel теперь проверяет этот путь не только по readiness, но и реальным action probe через CDP
- Если LM Studio / Codex уже были открыты до relaunch Chrome, после attach может понадобиться их перезапуск, чтобы MCP перечитал состояние
- Если ordinary Chrome сейчас запущен из другой macOS учётки, helper из USER2 не сможет его перезапустить: relaunch нужно делать из той же учётки, которой принадлежит процесс Chrome
- На Chrome `146.0.7680.154` подтверждён новый блокер: default profile может отклонять DevTools remote debugging с текстом `DevTools remote debugging requires a non-default data directory`
- Это означает, что ordinary attach к настоящему дефолтному профилю владельца может быть принципиально недоступен; truthful fallback в таком случае: использовать OpenClaw Debug browser или поднимать отдельный non-default Chrome data dir
