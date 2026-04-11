# Dashboard: /costs Page Update Spec — Gemini 3.1 Pro

> Обновление существующей /costs page с новыми FinOps полями

## Новые поля в GET /api/costs/report

Добавлены в session 5:
```json
{
  "total_tool_calls": 15,        // NEW: общее число tool_use вызовов
  "total_fallbacks": 2,          // NEW: сколько раз был fallback
  "total_context_tokens": 45000, // NEW: суммарные context tokens
  "avg_context_tokens": 3000,    // NEW: средний context per request
  "by_channel": {                // NEW: breakdown по каналам
    "telegram": 12,
    "translator_mvp": 3
  }
}
```

## Что добавить в UI

### Секция: FinOps Breakdown (новая)
- Tool calls: 15 total
- Fallbacks: 2 (если > 0, показать warning badge)
- Avg context: 3000 tokens
- By channel: pie chart или badges

### Секция: Cost Efficiency
- Cost per request: total_cost / total_calls
- Tokens per dollar: total_tokens / total_cost

## Gemini Prompt

```
Обнови существующую HTML страницу /costs для Krab dashboard.

Добавь секцию "FinOps Breakdown" после основных метрик:
- Tool calls total
- Fallback count (с warning badge если > 0)
- Average context tokens per request
- Channel breakdown (telegram, translator, etc.)
- Cost efficiency: cost per request, tokens per dollar

JSON response format дополнился полями: total_tool_calls, total_fallbacks,
total_context_tokens, avg_context_tokens, by_channel.

Стиль: тёмная тема (#1a1a2e), карточки, как остальные страницы.
```
