# Checkpoint 18.03.2026 (финальный) — Krab/OpenClaw

**Общий % проекта: ~68%** (был 62%)

---

## Что сделано в этой сессии (18.03)

### 1. Qwen Portal OAuth ✅
- **Симптом:** `expires in 0m` → refresh token был мёртв
- **Решение:** Создан `Login Qwen Portal OAuth.command` → кнопка в панели 8080 запускает OAuth
- **Результат:** Авторизован, `expires in 6h`
- **Диагностика:** `expires in 0m` = отображение 0 для отрицательного TTL (протухший токен)

### 2. LM Studio chat модели ✅
- Добавлено 8 chat моделей в `~/.openclaw/openclaw.json → agents.defaults.models`
- Итого: 35 configured models (было 27), 12 LM Studio моделей
- **Важно:** регистрировать модели нужно в `openclaw.json → agents.defaults.models`, а НЕ в `models.json`
- Добавленные модели:
  ```
  lmstudio/qwen3.5-9b-mlx@8bit (thinking: off)
  lmstudio/qwen3.5-27b-mlx@8bit (thinking: off)
  lmstudio/qwen2.5-coder-7b-instruct-mlx
  lmstudio/mistralai/devstral-small-2-2512
  lmstudio/deepseek/deepseek-r1-0528-qwen3-8b (reasoning)
  lmstudio/microsoft/phi-4-reasoning-plus (reasoning)
  lmstudio/google/gemma-3n-e4b
  lmstudio/mistralai/mistral-small-3.2
  ```

### 3. auth_recovery_readiness.py восстановлен ✅
- Файл был удалён (остался только .pyc), восстановлен из git: `1d62b4b`
- Кнопки релогина в панели 8080 теперь полностью рабочие

### 4. OAuth re-login helpers созданы ✅
```
Login Qwen Portal OAuth.command     ← НОВЫЙ
Login OpenAI Codex OAuth.command    ← НОВЫЙ
Login Google Antigravity OAuth.command ← НОВЫЙ
Login Gemini CLI OAuth.command      ← был раньше
```
- Все доступны из панели 8080 → кнопка "Перелогинить ..."
- API endpoint: `POST /api/model/provider-action { provider, action: "repair_oauth" }`

### 5. E2E тест подтверждён ✅
- **Метод:** @p0lrd написал @yung_nagato "ну как ты?" с мобильного Telegram
- **Результат:** Краб ответил развёрнутым статусом через Gemini 3.1 Pro Preview
- **Браузерный e2e:** ограничение — Chrome-сессия залогинена как @yung_nagato (userbot)
  - Для автоматизации нужна сессия @p0lrd в Chrome
  - Функционально тест прошёл ✅

### 6. Git
- Ветка: `fix/routing-qwen-thinking`
- Коммит: `0a4cf09` — fix: restore auth_recovery_readiness and add OAuth re-login helpers
- Запушено в remote ✅

---

## Текущее состояние routing chain
```
google/gemini-3.1-pro-preview (primary, via GEMINI_API_KEY) ← РАБОТАЕТ
→ openai-codex/gpt-5.1-codex-mini  (#1, expires in 6d)     ← РАБОТАЕТ
→ qwen-portal/coder-model           (#2, expires in 6h)     ← ВОССТАНОВЛЕН
→ google-gemini-cli/gemini-3-flash-preview (#3, expires 0m) ← нужен refresh
→ openai-codex/gpt-5.3-codex       (#4)
→ claude-proxy/claude-sonnet-4-6   (#5, локальный)
```

---

## Что осталось до 100%

### Срочно / Следующий чат
1. **Браузерный e2e** (опционально) — залогинить Chrome отдельной вкладкой как @p0lrd
   - Модель: Sonnet 4.6, обычный режим
2. **google-gemini-cli refresh** — `expires in 0m`, может auto-refresh через gemini CLI sync
   - Запуск: `Login Gemini CLI OAuth.command` или `scripts/sync_gemini_cli_oauth.py`

### Среднесрочно (Этап 1-2 роадмапа)
3. **Translator finish gate** — ru→es retest на iPhone 14 Pro Max (ручной шаг, manual-only)
4. **Channel stability** — DM-policy правила, pairing-спам, session overrides
5. **Telegram session watchdog** — 5-10 перезапусков без ручного логина

### Более долгосрочно (Этап 3-5)
6. **Browser для Краба** — Chrome DevTools protocol (давать Крабу доступ к вкладкам)
   - Модель: Opus 4.6, **Plan Mode** (архитектурный)
7. **Voice Gateway + Krab Ear** — как обязательные сервисы экосистемы
8. **pablito owner panel** — воспроизвести runtime truth на main аккаунте
9. **Multi-agent setup** — разделить ответственности, отдельный Telegram аккаунт каждому агенту
   - Модель: Opus 4.6, **Plan Mode** (архитектурный)
10. **!model scan** с размером модели и корректным выбором
11. **Dashboard/Browser relay** — стабилизация Chrome Relay порта

### Архитектурные (Этап 6)
12. **Parallel mode** — Sequential 1/1 vs Parallel 4/8 (обсудить)
13. **Codex CLI** — добавить в coding-agent skill как опцию

---

## Рекомендации по модели/режиму

| Задача | Модель | Режим |
|--------|--------|-------|
| Читать/анализировать файлы | Haiku | обычный |
| Мелкий фикс, хелпер | Sonnet 4.6 | обычный |
| Сложная фича, рефактор | Sonnet 4.6 | **Plan Mode** |
| Параллельные независимые задачи | Sonnet 4.6 | **agent dispatching** |
| Архитектура (multi-agent, browser) | Opus 4.6 | **Plan Mode** |
| Когда /compact или новый чат | — | контекст > 80% заполнен |

---

## Ключевые пути

| Что | Путь |
|-----|------|
| Runtime конфиг | `~/.openclaw/openclaw.json` |
| Models catalog | `~/.openclaw/openclaw.json → agents.defaults.models` |
| Models spec | `~/.openclaw/agents/main/agent/models.json` |
| Auth profiles | `~/.openclaw/agents/main/agent/auth-profiles.json` |
| Claude proxy config | `~/.openclaw/claude_proxy_config.json` |
| Claude proxy скрипт | `scripts/claude_proxy_server.py` |
| Gateway лог | `/tmp/openclaw_gateway.log` |
| Web panel | `http://127.0.0.1:8080` |
| Claude Code alias | `alias claude="claude --dangerously-skip-permissions"` |

---

## Диагностика при проблемах

```bash
# Статус routing
openclaw models status | grep -E "Default|Fallback|expires"

# Qwen auth (если истёк снова)
# → Кнопка в панели 8080 → "Перелогинить Qwen Portal"
# или двойной клик: "Login Qwen Portal OAuth.command"

# Gemini CLI auth refresh
scripts/sync_gemini_cli_oauth.py

# Логи gateway
tail -30 /tmp/openclaw_gateway.log | grep -E "error|fallback|rate|expired"

# Claude proxy
curl -s http://localhost:17191/health

# Добавить новые LM Studio модели:
# ~/.openclaw/openclaw.json → agents.defaults.models → добавить запись
# "lmstudio/model-id": {"params": {"thinking": "off"}}
```

---

## Правила работы (для следующего агента)

- Ветки: создавать для каждого блока задач
- Коммиты: делать после каждого значимого шага + push
- Main: мержить только 100% готовый код
- Язык общения: **русский**
- Reporting: писать общий % проекта и % текущего блока
- Всегда рекомендовать модель/режим под задачу
- НЕ слать SIGHUP openclaw — использовать `openclaw gateway` для перезапуска
- `alias claude="claude --dangerously-skip-permissions"` уже настроен
