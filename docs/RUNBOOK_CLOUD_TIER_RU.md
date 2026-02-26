# Runbook: Cloud Tier State — проверка вживую (R23)

## Цель

Убедиться что free→paid autoswitch и reset работают в production контуре без перезапуска бота.

---

## Требования

- Бот запущен: `./start_krab.command`
- `WEB_API_KEY` задан в `.env` (иначе auth-эндпоинты открыты)
- `GEMINI_API_KEY_FREE` и `GEMINI_API_KEY_PAID` заданы для autoswitch

---

## Шаг 1 — Проверить текущий Tier State

```bash
# Смотрим active_tier, метрики, switch_count
curl -s http://localhost:8080/api/openclaw/cloud/tier/state | python3 -m json.tool
```

**Ожидаем:**

```json
{
  "available": true,
  "tier_state": {
    "active_tier": "free",
    "switch_count": 0,
    "sticky_paid": false,
    "metrics": {
      "cloud_attempts_total": 0,
      "cloud_failures_total": 0,
      "tier_switch_total": 0,
      "force_cloud_failfast_total": 0
    }
  }
}
```

---

## Шаг 2 — Проверить Cloud Diagnostics

```bash
# Диагностика ключей провайдеров
curl -s "http://localhost:8080/api/openclaw/cloud" | python3 -m json.tool
```

Проверить что `providers.google.ok = true` и `error_code = ""`.

---

## Шаг 3 — Ручной Reset Tier

```bash
# Требует WEB_API_KEY
export WEB_API_KEY="ваш-ключ-из-.env"

curl -s -X POST http://localhost:8080/api/openclaw/cloud/tier/reset \
  -H "X-Krab-Web-Key: $WEB_API_KEY" | python3 -m json.tool
```

**Ожидаем:**

```json
{
  "ok": true,
  "result": {
    "previous_tier": "paid",
    "new_tier": "free",
    "reset_at": 1740000000.0
  }
}
```

---

## Шаг 4 — Проверить Autoswitch в логах

После quota-ошибки от Google API в логах должно появиться:

```
CloudTier autoswitch: free → paid | reason=quota_or_billing | switch_count=1 | sticky=True
```

Или при ручном reset:

```
CloudTier manual reset: paid → free | switch_count=2
```

---

## Шаг 5 — Регрессия: тесты

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
source .venv/bin/activate

pytest tests/test_cloud_tier_state.py \
       tests/test_cloud_autoswitch.py \
       tests/test_force_cloud_failfast.py \
       tests/test_cloud_tier_reset_endpoint.py \
       tests/test_r16_cloud_tier_fallback.py \
       tests/test_r17_cloud_diagnostics.py \
       -v --tb=short 2>&1 | tail -60
```

---

## Известные ограничения

- `sticky_paid` живёт в **runtime памяти** — после рестарта бота tier сбрасывается по `.env`.
- Autoswitch cooldown = 60с (настраивается через `CLOUD_TIER_AUTOSWITCH_COOLDOWN_SEC`).
- `force_cloud_failfast_total` считает только fail-fast в `force_cloud` режиме, не обычные cloud-ошибки.

---

## Env переменные (R23)

| Переменная | Значение по умолчанию | Описание |
|---|---|---|
| `CLOUD_TIER_AUTOSWITCH_COOLDOWN_SEC` | `60` | Минимум секунд между autoswitch |
| `CLOUD_TIER_STICKY_ON_PAID` | `1` | Paid остаётся после autoswitch до ручного reset |
| `GEMINI_API_KEY_FREE` | — | Ключ free tier Gemini |
| `GEMINI_API_KEY_PAID` | — | Ключ paid tier Gemini |
