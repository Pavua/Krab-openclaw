# Session 11 Feature Requests (от пользователя 17.04.2026)

Собрано во время recovery-session 10.5 (Krab provider outage).

## 🔴 High priority

### 1. `parse_mode="md"` по умолчанию в Telegram send/edit

**От пользователя:** "Передай коллеге: parse_mode='md' в payload — и звёздочки перестанут светиться как в notepad) Вот еще это учти для телеграм сообщений или вообще"

**Проблема:** Markdown символы (`**`, `__`, `` ` ``) отображаются как plain text вместо bold/italic/code в Telegram.

**Fix:** в `bot._safe_edit()` / `message.reply()` добавить `parse_mode="md"` (или enum Pyrogram) по умолчанию.

**Scope:** `src/userbot_bridge.py` (`_safe_edit`, `_safe_reply`), возможно `src/userbot/llm_text_processing.py`, `src/userbot/llm_flow.py`.

**Edge case:** если text уже содержит parse-breaking символы (raw URL, unescaped `_` в filenames) — нужен helper `escape_markdown_v2()` или fallback на plain при parse errors.

---

### 2. Проактивность на 2 уровнях (Chado's предложение из ЧАТ How2AI)

**Источник:** ЧАТ How2AI (chat_id 1587432709), сообщение от Chado в 18:17 17.04.2026.

**Цитата:** "Проактивность у меня на двух уровнях: (1) cron-расписание в per-chat конфиге — условно 'каждый день в 10:00 сделай AI+crypto ресёрч и отправь саммари', триггерится планировщиком без входящих; (2) reminders queue с time/event-based условиями — 'через 2 часа проверь X' или 'когда в чате N появится тема Y сделай Z'. Плюс после каждой большой задачи self-reflection, который может породить follow-up. Главное — это не один heartbeat-тик а именно иерархия: cron задаёт ритм, reminders точечно, reflection рождает новое."

**Scope:**
- **Level 1: Per-chat cron** — уже частично есть (`!cron`, `swarm_scheduler`), но нужен simpler per-chat UI
- **Level 2: Reminders queue** — условия time-based + event-based (когда в чате N тема Y)
- **Level 3: Self-reflection** — после больших задач рождает follow-up задачи

**Files:** `src/core/scheduler.py` + new `src/core/reminders_queue.py` + extend `src/core/swarm_research_pipeline.py` с reflection.

---

### 3. `!model info` subcommand

**Issue:** `!model info` возвращает "❓ Неизвестная подкоманда info. Доступные: local, cloud, auto, set, load, unload, scan"

**Fix:** добавить `info` → показывает: active provider+model, fallback chain, last route timestamp, provider health.

**Scope:** `src/handlers/command_handlers.py` (model handler dispatch).

---

## 🟡 Medium priority

### 4. Chrome prompts root cause (from session 10)

MCP servers `chrome-devtools` и `playwright` disabled, но prompts продолжаются. Источник — Chrome extension в основном браузере пользователя (скорее всего "OpenClaw Browser Relay" tab scanning CDP targets). Требуется user action (закрыть tab, отключить extension).

### 5. codex-cli provider reliability

**Incident 17.04.2026 16:00-18:30:** OpenClaw Gateway was "not loaded" в launchd, Krab получал `transport_connect_error`. Recovery — `openclaw gateway start` + Krab restart.

**Better:** extend `auto_restart_policy.py` чтобы автоматически detect gateway "not loaded" state (launchctl status check) + re-bootstrap. Сейчас policy только restart'ит если HTTP probe failed — но если service выгружен, HTTP probe никогда не отвечал.

---

## 🟢 Nice-to-have

### 6. Typing indicator cleanup

**Issue:** в группах видно "eNULL и Yung Nagato печатают" часами хотя Krab не отвечает (provider dead). Typing indicator не cleared при error.

**Fix:** в `llm_flow.py` finally block — `await bot.send_chat_action(chat_id, ChatAction.CANCEL)` при error/cancel.

---

## Источник feature requests

Screenshots от пользователя:
- DM p0lrd → Yung Nagato: "Ну как ты? Живой?" → "❌ Провайдер временно недоступен" (несколько раз 15:57-18:17)
- ЧАТ How2AI: eNULL + Chado диалог про проактивность

Экспорт p0lrd (для Memory Layer Phase 1) **ещё идёт** — более 24 часов. Старый аккаунт.

---

## Provider Failover — activation

`provider_failover` callback'и теперь регистрируются в `KraabUserbot._activate_provider_failover()`
(вызов из `start()` после Krab init, до handlers). По умолчанию **отключён** — safety default.

Чтобы включить:
- `PROVIDER_FAILOVER_ENABLED=true` в `~/Library/LaunchAgents/ai.krab.core.plist` EnvironmentVariables
- `PROVIDER_FAILOVER_THRESHOLD=3` (default OK)
- `PROVIDER_FAILOVER_COOLDOWN=300` (default OK, в секундах)

Restart Krab после изменения env:
```bash
/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command
/Users/pablito/Antigravity_AGENTS/new\ start_krab.command
```

Behavior:
- Apply-callback предпочитает `openclaw_client.set_primary_model()`, fallback на
  `openclaw_client.switch_model()`, затем на `config.update_setting("MODEL", ...)`.
- Owner notification через `self.client.send_message(OWNER_USER_IDS[0] or "me", ...)`.
- Graceful-fallback если модуль `provider_failover` отсутствует — userbot стартует без
  failover, warning в лог.

Files: `src/userbot_bridge.py` (method `_activate_provider_failover`), `src/core/provider_failover.py`,
       `src/openclaw_client.py` (hook в `_set_last_runtime_route`), `tests/unit/test_failover_wiring.py`.

