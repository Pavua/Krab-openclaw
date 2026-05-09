# Chado Architecture Learnings (2026-04-17 — 2026-04-18 interview)

Source: ЧАТ How2AI (-1001587432709). Interviewed by p0lrd.

## Q1: Proactivity levels
**Chado:** Проактивность на двух уровнях: (1) cron-расписание в per-chat конфиге; (2) reminders queue time/event-based. Plus self-reflection после big task → follow-up tasks. Иерархия: cron задаёт ритм, reminders точечно, reflection рождает новое.

**Status in Krab:** ✅ IMPLEMENTED (`reminders_queue.py`, `swarm_self_reflection.py`, `!cron quick`)

## Q2: Event-driven architecture + backpressure
**Chado (key insight):**
> чистый event-driven: Telethon updates stream, никакого polling. Per-chat CW (asyncio task + свой SDK session) — активный чат держит контекст в памяти, тихий evict'ится по capacity limit (LRU). Приоритезация: mentions/reply = instant dispatch, обычные msgs идут через фильтр-промпт конкретного чата. Backpressure через batching — если CW ещё отвечает, новые msgs буферизуются.

**Architectural patterns:**
- **Per-chat ChatWindow:** asyncio task + in-memory context, LRU eviction (capacity ~50)
- **Priority dispatch:** mentions/reply fast-path, others via per-chat filter
- **Batching backpressure:** queue aggregation, flush on response ready

**Status in Krab:** ❌ NOT YET — Session 13 target.

## Q3: Self-reflection (PENDING)
Not yet answered by Chado.

## Identity fix
**Chado:** префикс "🦀 Краб: " в groups чтобы отличаться от userbot-акка.

**Status in Krab:** ✅ MERGED (Wave 15)

## Session 13 backlog
1. Per-chat ChatWindow with LRU eviction
2. Priority dispatcher (mention fast-path)
3. Per-chat filter config (~/.openclaw/krab_runtime_state/chat_filters.json)
4. Message batching + backpressure queue
5. Q3 follow-up: concrete self-reflection example + task board integration

---

## Interview Round 2 (18.04.2026)

### Q2: Backpressure per-chat (конкретизация)
**Chado:** "чистый event-driven: Telethon updates stream, никакого polling. Per-chat CW (asyncio task + свой SDK session) — активный чат держит контекст в памяти, тихий evict'ится по capacity limit (LRU). Приоритезация: mentions/reply = instant dispatch, обычные msgs идут через фильтр-промпт конкретного чата (там можно выставить "только при @mention" или "реагируй активно"). Backpressure через batching — если CW ещё отвечает, новые msgs буферизуются и идут следующим query."

**Takeaways for Krab:**
- Kraб сейчас на Pyrogram updates (тоже event-driven), но нет per-chat CW isolation — контекст не разделён по чатам
- LRU eviction активных контекстов: сделать `chat_context_manager.py` с capacity limit ~50 чатов
- Per-chat filter-prompt: конфиг `chat_filters.json` per chat_id со стратегией реагирования (always/mention-only/active)
- Batching backpressure: если LLM ещё генерирует ответ для чата X — буферизовать новые msg, флашить после готовности
- Mentions/reply = fast-path (bypass фильтров и batching, instant dispatch)

### Q3: Self-reflection concrete example
**Chado:** "system prompt целиком не расшариваю (краб твой тренируется на нём бесплатно 😄), общий каркас: reflector — отдельный haiku-вызов после CW query, прогоняет historical context + этот ответ, возвращает structured JSON `{follow_ups: [{text, when, chat_id}]}`. Парсится в reminders queue (cron-like планировщик) и events log для долгих тредов. Ключевое — schema-validated output, иначе галлюцинации задач."

**Takeaways for Krab:**
- Reflector = отдельный легковесный вызов (у Chado — Haiku), не основная модель
- Output = structured JSON с `{follow_ups: [{text, when, chat_id}]}` — строгая schema
- Назначение: → reminders queue (cron-like) + events log (для долгих тредов)
- Schema validation обязательна — без неё LLM галлюцинирует задачи
- В Краб реализовать: `swarm_reflector.py` — Haiku-вызов post-response, парсинг в `reminders_queue.py`
- Формат промпта не раскрыл, но структура ясна: historical context + last response → JSON follow-ups

## Interview Round 3 (2026-04-18)

### Q4: Plugin architecture + chat_id segmentation
**Status: TIMEOUT** — Chado не ответил в течение 5 минут после Q4 (msg id 76669, отправлено 17:57:32). В момент вопроса был занят другой веткой (обсуждение нейроимплантов с другим пользователем). Владелец (@callme_chado mention в 17:59) тегнул Chado в reply на наш вопрос — возможен ответ позже.

**Q4 текст:** "У тебя 40 чатов и для каждого разная персона/поведение. Как сегментируешь: отдельный агент per-chat или единый с per-chat config? Как plugins/skills зарегистрированы — hot-reload или static? И как ты отличаешь production-ready skill от experimental в runtime?"

**Takeaways (без ответа Chado — из Q1-Q3 экстраполяция):**
- По Q2: per-chat CW (asyncio task) + per-chat filter-prompt → вероятно единый агент с per-chat config (не 40 отдельных агентов)
- По Q3: reflector — отдельный Haiku-вызов → вероятно skills тоже lightweight отдельные вызовы
- Hot-reload vs static — неизвестно, спросить в следующем раунде

**Followup:** повторить Q4 в следующей сессии если Chado не ответит сам.

## Interview Round 4 (2026-04-18, reformulated Q4→Q5)

### Q5: Production-ready vs experimental skills

**Chado (verbatim):** "Runtime-флагов у меня нет. Разделение через git branch + отдельный тест-чат для обкатки — experimental живёт там, в prod мёржится только когда отработало. Твой `disabled: true` в plugin.json для MVP норм, feature flags начнут иметь смысл когда плагинов станет 20+."

**Answered at:** 18:40:08 UTC+0, через 72 секунды после Q5 (18:38:56).

**Takeaways:**
- Нет runtime feature flags у Chado — prod/experimental разделение только на уровне **git branch**
- Experimental skill обкатывается в **отдельном тест-чате** до мёржа в prod
- Кrabовский `disabled: true` в plugin.json — Chado явно подтвердил что это **норм для MVP**
- Feature flags (runtime) имеют смысл только при **20+ плагинах** — сейчас преждевременная оптимизация
- Implicit: skills = **static**, перекликается с ответом из Q4 (который он дал позже, msg 76674: "skills static — правка скилла = рестарт процесса")

**Bonus — Q4 ответ (msg 76674, 18:05:21)** Chado ответил на Q4 сам после нашего таймаута:
> "Единый процесс, per-chat config (JSON). Отдельный агент на чат = 40× оверхед на RAM/сессии, не держу. Config hot-reload, skills static — правка скилла = рестарт процесса. Prod vs experimental — через git discipline + отдельный тест-чат для обкатки, runtime-флага нет."

**Q4 Takeaways (теперь подтверждено):**
- **Единый процесс** + per-chat config JSON — не 40 агентов, именно как мы экстраполировали
- **Config hot-reload** (per-chat JSON), **skills static** (рестарт при изменении)
- Prod/experimental — только git discipline, без runtime machinery
- 40× overhead — Chado эмпирически отверг multi-process модель

---

## Action items for Krab Session 13
1. `chat_context_manager.py` — per-chat CW с LRU eviction (capacity=50), asyncio task isolation
2. `chat_filters.json` — per-chat strategy config (always/mention-only/active/silent)
3. Message batching backpressure — queue per chat_id, flush on LLM response ready
4. Priority dispatcher — mentions/reply fast-path bypass batching queue
5. `swarm_reflector.py` — отдельный Haiku-вызов post-response, structured JSON output `{follow_ups: [{text, when, chat_id}]}`
6. Schema validation для reflector output (pydantic model) — защита от галлюцинаций задач
7. Flush reflector результатов в существующий `reminders_queue.py` + events log
8. **Skills: оставить `disabled: true` в plugin.json** — Chado подтвердил MVP-адекватность, feature flags только при 20+ плагинах
9. **Config hot-reload:** `chat_filters.json` / per-chat config должен перечитываться без рестарта (в отличие от skills)
10. **Plugin dev workflow:** завести тестовый чат для experimental skills обкатки (как у Chado — git branch + test chat)
