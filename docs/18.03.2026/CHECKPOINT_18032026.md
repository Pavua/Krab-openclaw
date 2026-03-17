# Checkpoint 18.03.2026 — Krab/OpenClaw

**Общий % проекта: ~62%**

---

## Что сделано в этой сессии

### Routing стабилизация
- ✅ Fallback цепочка перестроена:
  ```
  google/gemini-3.1-pro-preview (primary)
  → openai-codex/gpt-5.1-codex-mini  (#1)
  → qwen-portal/coder-model           (#2)
  → google-gemini-cli/gemini-3-flash-preview (#3)
  → openai-codex/gpt-5.3-codex       (#4)
  → claude-proxy/claude-sonnet-4-6   (#5, локальный)
  ```
- ✅ `thinkingDefault = adaptive` включён
- ✅ Claude-proxy работает на порту 17191

### CLI инструменты
- ✅ `codex` CLI установлен (v0.115.0)
- ✅ `~/.local/bin` добавлен в PATH (cursor agent)
- ✅ `alias claude="claude --dangerously-skip-permissions"` в ~/.zshrc
- ✅ Claude Code: v2.1.77 (актуальная версия)

### Git
- ✅ Ветка: `fix/routing-qwen-thinking` создана и запушена
- ✅ Commit: `cc24231` (docs cleanup)

---

## Что осталось (следующий чат)

### Срочно
1. **Qwen Portal OAuth** — токен expires in 0m постоянно, нужна диагностика
   - Симптом: `openclaw models status` всегда показывает `expires in 0m`
   - Возможная причина: timezone/clock drift в расчёте TTL

2. **LM Studio chat модели** — в провайдере только embedding модель
   - Нужно добавить 8 chat моделей в `~/.openclaw/agents/main/agent/models.json`
   - Модели на внешнем SSD, JIT loading (30-120 сек первый запрос)

3. **e2e тест** через `web.telegram.org` в Chrome
   - У пользователя открыта сессия userbot в Chrome
   - Проверить: отправить сообщение → Краб отвечает корректно

### Среднесрочно
4. **Browser для OpenClaw** — дать Крабу доступ к Chrome через DevTools protocol
5. **Translator finish gate** — ru→es retest на iPhone 14 Pro Max (manual step)
6. **pablito owner panel** — воспроизвести runtime truth на main аккаунте

### Архитектурные улучшения
7. **Multi-agent setup** — разделить ответственности между агентами, дать каждому свой Telegram аккаунт
8. **Parallel mode** для OpenClaw (обсудить: Sequential 1/1 vs Parallel 4/8)
9. **Codex CLI интеграция** — добавить в coding-agent skill как опцию

---

## Ключевые пути

| Что | Путь |
|-----|------|
| Runtime конфиг | `~/.openclaw/openclaw.json` |
| Agent конфиг | `~/.openclaw/agents/main/agent/agent.json` |
| Models конфиг | `~/.openclaw/agents/main/agent/models.json` |
| Auth profiles | `~/.openclaw/agents/main/agent/auth-profiles.json` |
| Claude proxy config | `~/.openclaw/claude_proxy_config.json` |
| Claude proxy скрипт | `scripts/claude_proxy_server.py` |
| Gateway лог | `/tmp/openclaw_gateway.log` |
| Web panel | `http://127.0.0.1:8080` |
| Claude Code alias | `alias claude="claude --dangerously-skip-permissions"` |

---

## Диагностика при проблемах

```bash
# Проверить routing
openclaw models fallbacks list
openclaw models status | grep -E "Default|Fallback|auth"

# Логи gateway
tail -30 /tmp/openclaw_gateway.log | grep -E "error|fallback|rate|expired"

# Проверить claude-proxy
curl -s http://localhost:17191/health

# Qwen auth (если истёк)
openclaw models auth login --provider qwen-portal
```

---

## Правила работы (для следующего агента)

- Ветки: создавать для каждого блока задач
- Коммиты: делать после каждого значимого шага + push
- Main: мержить только 100% готовый код
- Язык общения: **русский**
- Reporting: писать общий % проекта и % текущего блока
- Действия: `alias claude="claude --dangerously-skip-permissions"` уже настроен
