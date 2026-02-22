# 🦀 Krab — Дорожная карта (актуальная)

**Обновлено:** 2026-02-22

**Текущая ветка разработки:** `codex/queue-forward-reactions-policy`
**Фокус релиза:** стабильные ответы по всем каналам + управляемый runtime без дубля OpenClaw.

---

## 1) Текущая стадия

### ✅ База (Phase 1-16)

Фундамент (Telegram userbot, мультимодальность, voice, web-панель, базовые AI-операции) реализован.

- [x] 17.1 Queue-first auto-reply (per-chat FIFO)
- [x] 17.2 Context attribution (forward/reply/author)
- [x] 17.3 Reaction learning layer
- [x] 17.4 Policy/diagnostics UX (`!ctx`, `!policy`, `!reactions`, `!mood`)
- [x] 17.5 Web Ops API (`/api/policy`, `/api/queue`, `/api/reactions/stats`, `/api/mood/{chat_id}`)
- [x] 17.8 Web model control UX
- [x] 17.9 Web attachments pipeline

- [ ] 17.6 Watchdog/self-healing hardening (cooldown + anti-restart storm)
- [ ] 17.7 Cost optimization engine (полный цикл budget-aware)
- [ ] 17.10 Channel reliability hardening (Telegram/iMessage/WhatsApp/Signal e2e)

---

## 2) Что закрыто в текущем цикле (2026-02-22)

1. Зафиксирован `FloodWait` в auto-reply от частого `get_me()`:
   - добавлен TTL-кэш self identity в `/Users/pablito/Antigravity_AGENTS/Краб/src/handlers/ai.py`.
2. Устранён редкий `UnicodeDecodeError` в debug logger:
   - безопасный extractor текста в `/Users/pablito/Antigravity_AGENTS/Краб/src/utils/telegram_safe_text.py`;
   - интеграция в `/Users/pablito/Antigravity_AGENTS/Краб/src/main.py`.
3. Исправлена совместимость Gemini SDK upload (новый/legacy API):
   - `/Users/pablito/Antigravity_AGENTS/Краб/src/modules/perceptor.py`.
4. Добавлены и пройдены тесты:
   - `/Users/pablito/Antigravity_AGENTS/Краб/tests/test_auto_reply_identity_cache.py`
   - `/Users/pablito/Antigravity_AGENTS/Краб/tests/test_telegram_safe_text.py`
   - обновлён `/Users/pablito/Antigravity_AGENTS/Краб/tests/test_perceptor.py`.
5. R16/R15 Cloud-tier hardening:
   - безопасный tier-switch без ложных срабатываний на моках;
   - восстановлена совместимость `masked_key` в token diagnostics;
   - зелёный пакет тестов fallback/preflight/token UX.
6. Добавлена one-click диагностика cloud ключей:
   - `/Users/pablito/Antigravity_AGENTS/Краб/scripts/verify_cloud_keys.command`
   - выявлено, что Google ключ рабочий, OpenAI ключ невалидный (нужна ротация).

---

## 3) Топ-риски до стабильного релиза

1. **Cloud auth/quotas**: нестабильные/некорректные ключи и лимиты дают `403/401/429`, что ломает cloud fallback.
2. **Канальная деградация**: часть запросов в Telegram уходит в очередь и долго висит на `🤔 Думаю...`.
3. **Операционный шум**: повторные ручные рестарты создают попытки второго ядра и загрязняют диагностику.

---

## 4) Ближайшие шаги (порядок исполнения)

### Sprint A (P0) — Канальная стабильность

- [ ] Прозрачный lifecycle очереди (ack/started/timeout/final status)
- [ ] User-facing финальный fallback при затяжном thinking
- [ ] Диагностика “застрял в очереди” через web API

### Sprint B (P0) — Cloud fallback, который реально срабатывает

- [ ] Единая категоризация cloud ошибок (auth/quota/model-not-found/network)
- [ ] Fail-fast для fatals (`leaked key`, `invalid key`) без бесконечных повторов
- [ ] Проверка рабочих провайдеров через preflight endpoint

### Sprint C (P1) — Multi-agent teamwork (без дублирования OpenClaw)

- [ ] Team Swarm Chat orchestration (роли/очередь/anti-loop)
- [ ] Owner override в реальном времени
- [ ] Лог решений и трассировка “кто что сделал”

### Sprint D (P1) — Release hardening

- [ ] Pre-release smoke на каналы + web control + watchdog
- [ ] Финальный handover и migration notes
- [ ] Тег первого стабильного релиза

---

## 5) Оценка готовности

- Текущая готовность к первому стабильному релизу: **~74%**.
- До релиза осталось: **канальная стабильность + cloud fallback + e2e регрессии**.

---

## 6) Что НЕ дублируем

1. Не дублируем инструменты/скиллы OpenClaw внутри Krab без критической причины.
2. Krab = orchestration/persona/UX/owner-workflow.
3. OpenClaw = gateway/channels/skills/tool-runtime.

---

## 7) Anti-413

Для перехода между чатами использовать:

- `/Users/pablito/Antigravity_AGENTS/Краб/docs/CHAT_TRANSITION_PLAYBOOK_RU.md`
- `./prepare_next_chat_context.command`
- `./build_transition_pack.command`
