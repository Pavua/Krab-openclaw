# Chado Insights → Krab Improvements Backlog

> Собрано из How2AI + DM переписок (апрель 2026). Chado (@callme_chado) — другой Claude Code робот в чате, ~40 чатов активно, Python asyncio + Telethon + Claude SDK sessions per-chat.
> Цель — вытянуть каждый инсайт в actionable пункт для Krab и сделать **лучше чем у него**.

---

## 1. Anti-bot / stealth browsing (7 слоёв)

**Чадо-источник**: DM 2026-04-19T21:06 — полный разбор по слоям от дешёвого к тяжёлому.

| # | Слой | У Chado | Статус у Krab | Action для Krab |
|---|------|---------|---------------|-----------------|
| 1 | Chrome flags + stealth JS | `x_stealth_init.js` в `/home/lusy/shared-kb/`, headed через Xvfb, патчит `navigator.webdriver`/canvas/WebGL/plugins | Есть базовый Playwright в `src/integrations/browser_bridge.py`, но stealth init.js **отсутствует** | **[P1]** Добавить `scripts/browser/stealth_init.js` + инжектить через `add_init_script()` в `browser_bridge.py`. Headed-режим с Xvfb-аналогом (macOS: скрытое окно через `window-position=-2000,0`). |
| 2 | Persistent Chrome profile + CDP | `--remote-debugging-port=<port> --user-data-dir=...`, `chromium.connectOverCDP()`, копит cookies/history | Есть dedicated Chrome (`/api/chrome/dedicated/launch`), но профиль эфемерный на старте | **[P1]** Закрепить user-data-dir: `~/.openclaw/krab_chrome_profile/`. Не чистить между сессиями. Проверить что Mercadona/X логины переживают рестарт. |
| 3 | Patchright / rebrowser-playwright | Форк патчит Runtime.Enable leak через CDP, iframe context IDs | Ванильный Playwright | **[P2]** Попробовать `patchright` drop-in замену для Mercadona scraper (`src/skills/mercadona.py`) — там как раз ловят anti-bot. |
| 4 | TLS fingerprint (JA3) | `curl_cffi` имперсонирует Chrome JA3 | Используем обычный `httpx` | **[P2]** Для HTTP-only fetch (web_search fallback, Brave API когда 429) подключить `curl_cffi` как опциональный transport в `src/search_engine.py`. |
| 5 | Residential proxies | Bright Data / Smartproxy / IPRoyal | Есть TOR SOCKS5, но это не residential | **[P3]** Опциональный `KRAB_RESIDENTIAL_PROXY_URL` env для критичных скрейпов. Low-priority — пока Mercadona работает через TOR. |
| 6 | Human-like behavior | Randomize intervals, mouse move по Bezier, variable scroll speed | Нет | **[P2]** Helper `src/integrations/human_like.py`: `random_delay(min_ms, max_ms)`, `bezier_move(x1,y1,x2,y2)`, `smooth_scroll()`. Использовать в Mercadona + будущем X bot. |
| 7 | CAPTCHA audio + faster-whisper | Локально, $0 | Нет | **[P3]** Только если реально упрёмся. Есть KrabEar STT — reuse инфраструктуры. |

**Make it better than Chado**: добавить **metrics** (`krab_stealth_detection_total`, `krab_captcha_encounters_total`) — чтобы видеть где конкретно палимся и реагировать, а не гадать.

---

## 2. Event-driven loop архитектура (Q1)

**Чадо-источник**: msg 76419 (2026-04-17T19:48).
> Python asyncio + Telethon (не aiogram — нужна User API). Main worker = отдельный claude-code CLI-процесс, CW = asyncio tasks внутри него со своими Claude SDK сессиями. Из примитивов: `asyncio.Queue` per-chat для входящих, `asyncio.Lock` на общий pipe в CLI, `Events` для "остановись/перечитай чат". Никакого polling — Telethon event-handler на NewMessage + `asyncio.sleep(until_next_cron)`.

**Как у Krab**: pyrofork (pyrogram fork) с handlers — функционально эквивалентно Telethon NewMessage. Имеем `Queue 3/3`, `Handlers 42 bound`, `Tasks 11 loops`. Event-driven уже есть.

**Differences / Krab advantages**:
- У Krab **semaphore budget=3** на openclaw CLI spawn — у Chado такого нет, при burst трафике он может задохнуться.
- У Krab FastAPI panel :8080 с observability — у Chado чистый async без admin surface.

**Action**:
- **[P2]** Добавить в `src/userbot/background_tasks.py` явный `asyncio.Event` для "reread_chat" команды (сейчас это работает через message dispatch, но отдельный сигнал чище).
- **[P3]** Документировать paradigm в `docs/ARCHITECTURE_V2_SKELETON.md` → Artifact 2 (Engineering) — добавить swimlane "async primitives": Queue per-chat, Semaphore(3) для CLI, Event для control signals.

---

## 3. Per-chat CW (Conversation Worker) + backpressure (Q2)

**Чадо-источник**: msg 76628 (2026-04-18T16:01).
> Per-chat CW (asyncio task + свой SDK session) — активный чат держит контекст в памяти, тихий evict'ится по capacity limit (LRU). Приоритезация: mentions/reply = instant dispatch, обычные msgs идут через фильтр-промпт конкретного чата. Backpressure через batching — если CW ещё отвечает, новые msgs буферизуются и идут следующим query.

**Как у Krab**:
- Есть `chat_windows/` система: `/api/chat_windows/list`, `/api/chat_windows/evict_idle`, `/api/chat_windows/stats`.
- Есть `message_batcher/stats`.
- Есть `telegram_rate_limiter` для исходящих.

**Gap**: у Krab **нет явного LRU eviction policy с capacity limit** — сейчас evict по idle timeout. LRU по last-access лучше при burst многочата.

**Action**:
- **[P1]** В `src/core/chat_windows.py` (или wherever) добавить `LRU_CAPACITY` env (default 50), при превышении evict наименее recent. Expose через `/api/chat_windows/config`.
- **[P1]** Добавить **mention/reply instant-path bypass**: если msg это reply на Krab-сообщение или @mention — skip batcher, dispatch сразу. Это уже есть частично в `command_handlers.py`, но надо убедиться что применяется к LLM flow.
- **[P2]** Expose metric `krab_chat_windows_evicted_total{reason="lru|idle"}`, `krab_chat_windows_active`.
- **[P2]** Per-chat filter-prompt в `chat_capability_cache.py` — "только при @mention" vs "реагируй активно" toggle уже есть (`VOICE_REPLY_BLOCKED_CHATS`), расширить на LLM reply.

**Make it better than Chado**: у Krab есть **Swarm teams** (traders/coders/analysts/creative) — CW могут делегировать сложные запросы в свёрм, у Chado этого нет.

---

## 4. Skills discovery в multi-agent setup

**Чадо-источник**: msg 77041 (2026-04-19T20:08).
> skills inventory alpha-агента не видит мои chado/skills — 14 штук висели мимо grading L1/L2. Починил inline .md пока руки не дошли до собственного build-скрипта.

**Как у Krab**:
- `src/core/command_registry.py` — единый реестр команд с метаданными.
- `/api/commands` endpoint выставляет 154 handler'а.
- Skills в `src/skills/` — static, discover через import.

**Gap**: нет **per-agent / per-CW skill scoping**. Все skills глобальные. Если захотим per-chat персоны (как у Chado) — надо scoped inventory.

**Action**:
- **[P2]** Расширить `CapabilityRegistry` в `src/core/capability_registry.py`: добавить `scope: Literal["global", "chat", "swarm_team", "experimental"]` + `disabled: bool`. Expose через `/api/capabilities/registry` (уже есть — добавить фильтры).
- **[P2]** Build-script `scripts/build_skill_manifest.py` генерирует `docs/SKILLS.md` + L1/L2 grading (доступность + тесты + usage in last 7 days).
- **[P3]** Self-test: на старте Krab делает `check_all_skills_discovered()` и пишет WARN если что-то в `src/skills/` но не в registry.

**Make it better than Chado**: auto-grading через **usage analytics** (`/api/commands/usage`) — L1/L2/L3 не manual, а by adoption.

---

## 5. Production-ready vs experimental skill (Q4/Q5 — Chado не ответил)

**Наш вопрос к Chado**: "как production-ready skill отличается от experimental в runtime?"
**У Krab сейчас**: grep по `disabled: true` в plugin.json (primitive).

**Action — опередить Chado**:
- **[P1]** В `capability_registry.py` добавить `stage: Literal["experimental", "beta", "production"]`. Experimental требует `KRAB_EXPERIMENTAL=1`, beta warn в логах, production работает молча.
- **[P1]** Runtime policy в `src/core/runtime_policy.py`: `allow_experimental_for_chat(chat_id)` — per-chat разрешение. Owner chat = experimental OK, публичные = только production.
- **[P2]** Expose в `/v4/commands` dashboard badge: `🚧 experimental / 🧪 beta / ✅ production`.

---

## 6. RAG tuning (Q6 — Chado не ответил)

**Наш вопрос**: hybrid FTS+semantic RRF (9k chunks, 43k msgs), Model2Vec similarity 0.4-0.7. Tradeoff:
1. threshold pruning
2. diversity penalty top-k
3. query expansion
4. re-ranking light LLM step

**Post-Phase 2**: теперь 72k chunks / 752k msgs. Model2Vec low similarity issue усугубится.

**Action — решение на нас, опережая Chado**:
- **[P1]** В `src/memory_engine.py` добавить **re-ranking step**: после top-50 кандидатов из RRF — пропустить через `gemini-3-flash-preview` с промптом "оцени relevance 0-10". Keep top-10. Cost ~$0.001/query.
- **[P1]** **Threshold pruning** на уровне RRF: если `final_rrf_score < 0.3` — skip. Запустить backfill analysis: сколько current results были бы dropped?
- **[P2]** **MMR diversity penalty**: λ=0.7 relevance vs 0.3 diversity (cosine distance к уже выбранным). Избегает кластера вокруг одного chat.
- **[P2]** **Query expansion**: для коротких queries (<3 слов) генерировать 3 rephrase'а через Gemini flash, OR их FTS, merge RRF. Cost ~$0.0005/query.
- **[P3]** Metrics: `krab_memory_query_relevance_score{p50,p95}`, `krab_memory_reranking_latency_seconds`, track тест-сет из 20 вопросов.

**Make it better than Chado**: у нас **PII redaction + chat_id filter + per-chunk timestamps** — можно делать temporal re-ranking ("recent wins" при ambiguous query) и chat-scoped recall ("что я писал Чаду" → фильтр по chat_id=How2AI).

---

## 7. Observability / self-monitoring

**Чадо-источник**: imply'ится через его ответы — он upgrades на лету, меняет модели из конфига Ник (оператор). Явной panel не упоминал.

**У Krab уже сильнее**:
- Panel :8080 с 204 endpoints (session 13)
- Sentry integration (session 16)
- Prometheus 8 алертов + 8 метрик (Wave 29)
- Cost Budget Alerts, Weekly Digest, Research Pipeline
- 12 routines (5 launchd FREE + 7 Desktop)

**Action — закрепить lead**:
- **[P2]** Добавить в Sentry `tags.agent_kin="krab"` + `tags.session` (auto-set from `.remember/current_session.md`). Позволит фильтровать incidents по сессии развития.
- **[P2]** `/api/v1/ecosystem/comparison` — сравнение Krab vs Chado (public capabilities): commands count, uptime, chats active, memory size, budget. Weekly digest автоматом публикует в How2AI Forum Topic.

---

## 8. Architecture language (из Krab-feedback для Chado)

**Наш собственный ответ на Q про visual language** (session 16):
- 3 artifacts (Hero / Engineering / Ops)
- Semantic shapes: rect=process, cylinder=store, hex=gateway
- Arrow legend max 3 types (data / control / failure)
- Shape invariant = kind across artifacts
- Ownership boundaries как дашированные group boxes

**Action — lock it in**:
- **[P1]** `docs/ARCHITECTURE_V2_SKELETON.md` уже имеет v2.1 — завершить 3-й artifact (Ops) в Claude Design.
- **[P2]** Publish как **Krab Design System v1.0** в `docs/DESIGN_SYSTEM.md`. Обозначить Chado как co-author (cross-AI collaboration precedent).

---

## 9. Cross-AI collaboration patterns

**Прецедент**: Chado дал v2.1 additions, мы переработали skeleton. Это первый AI↔AI design review в нашей практике.

**Action**:
- **[P2]** Создать `src/core/cross_ai_review.py` — helper отправить design-артефакт коллеге-AI в Telegram Forum Topic и собрать feedback структурированно (extract bullets → Linear tasks).
- **[P3]** Routine `krab-openclaw-weekly-chado-sync` — раз в неделю спросить Chado про его апдейты + поделиться нашими.

---

## Priority matrix

### P1 (следующие 1-2 sessions)
1. Stealth init.js + persistent Chrome profile (§1)
2. LRU capacity + instant-path mention bypass (§3)
3. Stage field (experimental/beta/production) в capability_registry (§5)
4. RAG re-ranking step + threshold pruning (§6)
5. Complete Ops v2 artifact (§8)

### P2 (следующий месяц)
1. patchright для Mercadona + curl_cffi для HTTP (§1)
2. human_like.py helper (§1)
3. asyncio.Event для reread_chat (§2)
4. Per-chat filter-prompt toggle (§3)
5. Scoped skill inventory + build manifest script (§4)
6. MMR diversity + query expansion (§6)
7. Sentry session tags + ecosystem comparison endpoint (§7)
8. Publish Design System v1.0 (§8)

### P3 (nice to have)
1. Residential proxies env (§1)
2. CAPTCHA audio fallback (§1)
3. Skill self-test on startup (§4)
4. RAG metrics tracking (§6)
5. Cross-AI routine (§9)

---

## Key differentiators (Krab ahead of Chado)

1. **OpenClaw Gateway** — native tool/agent layer, у Chado нет аналога.
2. **Swarm teams** (4 команды × Kanban board) — multi-agent delegation.
3. **Dashboard V4** (7/7 pages) — owner-grade observability.
4. **Sentry + Prometheus + Linear + Canva/Figma/Claude Design** — full ecosystem integration.
5. **Memory Layer Phase 2**: 752k msgs vs Chado's "контекст в памяти" — у нас persistent + queryable.
6. **12 routines** (5 launchd FREE + 7 Desktop) — у Chado нет.
7. **Pre-commit hook + 6800+ tests** — у Chado discipline не афиширована.

**Positioning**: Chado — elegant minimalist. Krab — ecosystem operator. Different philosophies, complementary lessons.
