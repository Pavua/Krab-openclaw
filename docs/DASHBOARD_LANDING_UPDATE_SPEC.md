# Dashboard: Landing Page (/) Update Spec — Gemini 3.1 Pro

> Обновление главной страницы с полным runtime summary

## API Endpoint

```
GET /api/runtime/summary → полное состояние Краба одним запросом:
{
  health: {...},          // telegram, gateway, scheduler
  route: {...},           // model, provider, channel
  costs: {...},           // total_cost, calls, by_model, by_channel
  translator: {profile, session},
  swarm: {task_board, listeners_enabled},
  silence: {...},
  notify_enabled: bool
}
```

## UI Layout

### Quick Stats Row (top)
- 🟢 Krab UP / 🔴 DOWN
- Model: gemini-3-flash-preview
- Tests: 1791 passed
- Costs: $0.XX today

### Cards Grid (2x3)
1. **Telegram** — connected, username, session state
2. **Model Route** — provider, model, channel, last route
3. **Translator** — language pair, session status, last translation
4. **Swarm** — task board summary, listeners ON/OFF
5. **Costs** — total, by model, tool calls, fallbacks
6. **Silence** — global/per-chat status

### Navigation
- Stats | Inbox | Costs | Swarm | Translator

## Gemini Prompt

```
Обнови главную HTML страницу (/) для Krab dashboard.

Данные из одного API: GET /api/runtime/summary

Layout:
1. Quick stats row: status, model, costs
2. 2x3 cards grid: Telegram, Route, Translator, Swarm, Costs, Silence
3. Навбар: Stats | Inbox | Costs | Swarm | Translator
4. Auto-refresh 10s

Стиль: тёмная тема (#1a1a2e), карточки с заголовками и иконками.
```
