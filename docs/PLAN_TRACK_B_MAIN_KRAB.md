# Track B — Main Krab (Telegram userbot ядро)

> **Project:** Краб (`/Users/pablito/Antigravity_AGENTS/Краб`)
> **Master plan:** `~/.claude/plans/parallel-enchanting-chipmunk.md` (Plan ID `parallel-enchanting-chipmunk`)
> **Created:** 2026-04-08
> **Scope:** Phase 1 hardening + Phase 5 Translator MVP routing
> **Self-contained:** да, можно работать без оглядки на Track C / Track D
>
> **HOT BACKLOG — CLOSED (session 5, 2026-04-11):**
> - ~~**P0 — string formatting bug**~~ — FIXED (commit `32ec2e7`): exc_info=True, safe markdown, wrapped reply
> - ~~**P1 — Stability C+B**~~ — FIXED (commit `6d2407d`): session watchdog auto-reconnect with 60s cooldown

---

## Что в этом файле

Только секции трека B (Main Krab) из master plan. Если тебе нужна информация про:

- **Backup, repo hygiene, integration contract, conflict-avoidance, observability** → master plan, разделы A.1–A.5
- **Voice Gateway (refactor, auto-summary, iOS)** → `Krab Voice Gateway/docs/PLAN_TRACK_C_VOICE_GATEWAY.md`
- **Krab Ear (diarization, integration, IPC commands)** → `Krab Ear/docs/PLAN_TRACK_D_KRAB_EAR.md`
- **Cross-cutting (USER2/USER3, .gitignore, memory discipline)** → master plan, раздел E

Master plan меняется при изменении shared foundation. Этот файл — при изменении трека B.

---

## Состояние трека на 11.04.2026 (session 5)

| Параметр | Значение |
|----------|----------|
| Phase | 1-3, 5, 8 (multi-phase progress) |
| Готовность | ~70% overall Master Plan |
| Открытые блокеры | #10 Mercadona (ждёт логи) |
| Тесты | ~1750+ passed (было 928 на старте session 5) |
| Session 5 | **87 commits**, +893 tests, translator MVP e2e, swarm execution, 25 API endpoints |
| Runtime | gemini-3-pro-preview, P0+P1 fixed, 50/50 stability PASS |
| Sync | origin/main ✅ |

### Session 5 закрыто (2026-04-10..11)
- **P0**: top-level exception handlers — full traceback logging + safe markdown
- **P1**: session watchdog auto-reconnect (60s cooldown)
- **B.2 Translator MVP**: language_detect + translator_engine + pipeline + per-chat + flash model + e2e
- **B.10 Coverage**: ALL src/core/ + ALL src/userbot/ mixins + employee_templates + mcp_client
- **Swarm listeners**: 4 team accounts respond in DMs (@p0lrdp_AI, @p0lrdp_worldwide, @hard2boof, @opiodimeo)
- **Swarm task board**: create/list/done/fail/assign + auto-track rounds
- **FinOps**: tool_calls, channel, fallback, context_tokens tracking
- **Policy Matrix**: runtime enforcement via check_capability()
- **Identity**: operator_id + account_id in every routing event
- **Channel parity**: silence/notify/voice/tasks via REST API + reserve bot
- **!notify on/off**: tool narration toggle
- **p0lrd co-owner**: ACL promoted
- **Heartbeat 60s**: optimized from 45s
- **Dashboard specs**: translator page + FinOps update for Gemini 3.1 Pro

---

## B.1. Phase 1 Stability Hardening (1-2 сессии)

**Что осталось из Stability Kernel** (всё остальное закрыто):

- **#10 Mercadona** — anti-bot stealth, ждёт **логи терминала от пользователя**. Без логов копать дальше нельзя.
  Action: спросить пользователя в начале сессии, скопировать вывод последней попытки `!shop`.

- ~~**#14 OpenClaw v2026.4.5 monitoring**~~ — **CLOSED 08.04.2026.** Audit: gateway up, route ok, userbot ready, ноль crash/restart за 24h. Только ожидаемый SIGTERM 07.04 20:20 (планово).

**Опциональные мелочи:**

- `.remember/remember.md` уже добавлен в `.gitignore` (см. master A.1)
- Удалить `.venv.OLD_DELETE_ME_SAFELY` (1.5GB) — таймер 24h истёк, runtime стабилен.

**Файлы для Mercadona:**
- `src/integrations/browser_bridge.py`
- `src/skills/mercadona.py`

**Verification:**
```bash
PATH=/opt/homebrew/bin:$PATH /Users/pablito/Antigravity_AGENTS/Краб/venv/bin/python -m pytest tests/unit/ -q  # 691+ passed
mcp__krab-yung-nagato__krab_status   # runtime up
gh pr list --repo Pavua/Krab-openclaw   # no stale PRs
```

---

## B.2. Phase 5 Translator MVP — main Krab routing (2-3 сессии)

**Цель:** main Krab распознаёт voice-сообщение в Telegram, определяет язык, маршрутизирует в Voice Gateway, возвращает переведённый текст + audio в чат.

**Приоритет языковых пар:**
1. RU↔EN (самая стабильная пара по edge_tts)
2. RU↔ES
3. Auto-detect
4. DE/FR/IT (опциональный задел)

### Что уже есть (НЕ дублировать!)

| Файл | Что внутри |
|------|-----------|
| `src/core/translator_runtime_profile.py` | Owner profile, **plain dict** (НЕ dataclass). Allowed pairs: `es-ru`, `es-en`, `en-ru`, `auto-detect`. Modes: `bilingual`, `auto_to_ru`, `auto_to_en`. Voice strategies: `voice-first`, `subtitles-first`. API: `load_translator_runtime_profile(path)`, `normalize_translator_runtime_profile(changes, base)`, `save_translator_runtime_profile(path, profile)` |
| `src/core/translator_session_state.py` | **Persistent dict-state**, НЕ state machine. Поля: `session_status` (init "idle"), `session_id`, `active_session_label`, `translation_muted`, `last_language_pair`, `last_translated_original`, `last_translated_translation`, `last_event`, `updated_at`, `timeline_*`. Управление через `apply_translator_session_update(changes, base)` (auto-stamps `updated_at`). НЕ трогать модуль, использовать API |
| `src/core/translator_finish_gate.py` | **iOS milestone gate** (pytest + iOS build + install + app launch + health). НЕ относится к B.2 — это для finalize iOS deploy. **Не дёргать здесь** |
| `src/integrations/voice_gateway_client.py` | Полный клиент к Voice Gateway (24.9KB) — **НЕ трогать**. Ключевые методы для B.2: `start_session(source, translation_mode, notify_mode, tts_mode, src_lang, tgt_lang, meta)`, `patch_session(...)`, `tune_runtime(session_id, **patch)` (смена языка на лету), `session_tts(session_id, text, voice, style)`, `stop_session(session_id)`. Env: `VOICE_GATEWAY_URL` (default :8090), auth: `KRAB_VOICE_API_KEY` |
| `src/integrations/voice_gateway_subscriber.py` | `VoiceGatewayEventSubscriber` класс. Хуки: `on_stt_final`, `on_translation_final`, `on_call_state`, `on_any_event`. Auto-reconnect: 2.0s delay, max 10 attempts. Lifecycle: `await start(session_id)` / `await stop()`. НЕ трогать |
| `src/handlers/command_handlers.py:1144` | `handle_translator()` команда — расширить новыми actions (`start`, `stop`, `pause`, `lang`) |
| `src/handlers/command_handlers.py:1072` | `handle_voice()` — текущий локальный TTS pipeline |
| `src/integrations/krab_ear_client.py` | Сейчас **только** `health_check()` + `health_report()`. Расширяется в Track D, D.3 (transcription methods) |

### Что нужно создать

**1. `src/core/language_detect.py`** (новый файл) — language detection
- Подход: `franc-min` для текста (быстро, оффлайн), Gemini-flash-lite для голоса (1 вызов на первую реплику)
- Public API: `async def detect_language(text: str | bytes) -> str` → ISO 639-1 ("ru", "en", "es", ...)

**2. `src/handlers/handle_translator_session.py`** (новый файл) — translator session router

Псевдо-flow:
```python
async def handle_translator_voice_message(message, voice_path):
    profile = load_translator_runtime_profile(...)
    if not profile.get("ordinary_calls_enabled"):
        return await fallback_local_voice(message, voice_path)

    src_lang, tgt_lang = resolve_pair(profile["language_pair"], voice_path)  # auto-detect или fixed

    # Создаём сессию в Voice Gateway
    session = await voice_gateway_client.start_session(
        source="telegram_voice",
        translation_mode=profile["translation_mode"],
        notify_mode="webhook",  # или 'silent'
        tts_mode=profile["voice_strategy"],
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        meta={"chat_id": message.chat.id, "message_id": message.id},
    )

    # Подписываемся на события
    subscriber = VoiceGatewayEventSubscriber(...)
    subscriber.on_translation_final = lambda evt: await reply_with_voice(message, evt)
    await subscriber.start(session["id"])

    # Сохраняем state
    apply_translator_session_update({"session_id": session["id"], "session_status": "active", ...}, ...)
```

- Реагирует на voice-сообщение в Telegram если `translator_runtime_profile["ordinary_calls_enabled"]` (обрати внимание — это поле в dict, не attr)
- Создаёт VG session через `voice_gateway_client.start_session(...)` с правильными `source`/`translation_mode`/`tts_mode`
- Подписывается на события через `VoiceGatewayEventSubscriber.on_translation_final` (auto-reconnect 2s, 10 attempts)
- Отвечает в Telegram голосом через `voice_gateway_client.session_tts(session_id, text, voice, style)`
- Обновляет `translator_session_state` через `apply_translator_session_update()`

**3. Wire в `src/userbot_bridge.py`** — добавить routing в message handler
- В районе `_get_voice_attachment()` (line ~3378): после распознавания voice → `if translator_session_active(): await handle_translator_session(...)` иначе обычный flow.

### Файлы

| Действие | Путь |
|----------|------|
| Создать | `src/core/language_detect.py` |
| Создать | `src/handlers/handle_translator_session.py` |
| Изменить | `src/userbot_bridge.py:3378` (вставить router check) |
| Изменить | `src/handlers/command_handlers.py:1144` (расширить handle_translator: actions `start`, `stop`, `pause`, `lang`) |
| Создать | `tests/unit/test_language_detect.py` |
| Создать | `tests/unit/test_translator_session.py` |

### Зависимости

- `franc-min` или `langdetect` в `pyproject.toml` (~50KB)

### Verification

- Unit tests: новые в `tests/unit/test_language_detect.py`, `tests/unit/test_translator_session.py`
- E2E: реальная отправка voice в Telegram → проверка ответа
- MCP test: через `mcp__krab-yung-nagato__telegram_send_message` записать голос и проверить

---

## B.3. Backlog cleanup (опционально, 1 сессия)

- Items #1 (Swarm v2 → R20+ backlog), #3 (HomePod mini, low priority), #11-13 (macOS уже deployed)
- Audit OpenClaw v2026.4.5 monitoring → если 5 дней стабильно → close #14

---

## B.4. Critical files map

```
src/
├── userbot_bridge.py            (5747 LOC) — message handler, line 3378 = voice routing point
├── openclaw_client.py           (3138 LOC) — tool execution loop, не трогать
├── voice_engine.py              (116 LOC)  — edge_tts wrapper, fixed PATH issue 07.04
├── core/
│   ├── translator_runtime_profile.py — есть, расширить allowed_pairs если auto-detect
│   ├── translator_session_state.py — есть, не трогать
│   ├── translator_finish_gate.py — есть, не трогать
│   ├── subprocess_env.py        — fixed 07.04 для homebrew PATH
│   └── language_detect.py       — НОВЫЙ
├── handlers/
│   ├── command_handlers.py:1144 — handle_translator (расширить)
│   └── handle_translator_session.py — НОВЫЙ
└── integrations/
    ├── voice_gateway_client.py — НЕ трогать (24.9KB)
    ├── voice_gateway_subscriber.py — НЕ трогать
    └── krab_ear_client.py      — health-only сейчас, расширить позже (Track D, D.3)
```

---

## B.5. Verification Track B

```bash
# Unit
PATH=/opt/homebrew/bin:$PATH /Users/pablito/Antigravity_AGENTS/Краб/venv/bin/python -m pytest tests/unit/ -q

# Lint
ruff check src/ && ruff format --check src/

# Runtime
mcp__krab-yung-nagato__krab_status   # gateway up
curl http://127.0.0.1:8080/api/health/lite

# E2E translator (через MCP)
mcp__krab-yung-nagato__telegram_send_message → "!translator start es-ru"
# затем отправить voice в Telegram, проверить ответ
```

---

## Зависимости от других треков

- **Track C (Voice Gateway)** должен быть запущен (`http://127.0.0.1:8090/health` → 200) и иметь endpoint `/v1/sessions` рабочий. Track C refactor (C.1) НЕ блокирует Track B — клиент `voice_gateway_client.py` использует API, не внутреннюю структуру.
- **Track D (Krab Ear)** должен быть запущен (`http://127.0.0.1:5005/health` → 200) для STT. Diarization из D.1 опциональна — без неё translator работает, просто без speaker labels. D.3 integration wiring улучшит fallback path для voice messages, но не блокирует translator MVP.

## Conflict-avoidance (memo)

- Не править `~/.openclaw/openclaw.json` параллельно с другими сессиями
- Restart Krab только из этой сессии когда работаешь в треке B (иначе pyrofork lock conflict)
- Свои git-ветки: `feat/main-krab-translator`, `feat/main-krab-mercadona`

См. master plan A.5 для полного списка правил.

---

## End of Track B
