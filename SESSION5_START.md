# 👋 Привет, Павлито! Я тебя услышал.

> **Уникальный маркер актуальности этого файла:** 🦀🔥 `KRAB-S4-CLOSED-2026-04-09-OPUS46-1M` 🔥🦀  
> **Время создания:** последнее сообщение session 4, контекст на 90% (895k/1M)  
> **Chat UI у тебя не рендерится,** но я в контексте ещё живу — пишу через файл.  
> **Если видишь этот маркер — файл актуальный, это последняя версия handoff перед restart.**

---

## 🎯 Что сейчас важно знать

1. **Session 4 ОФИЦИАЛЬНО ЗАКРЫТА.** Все 7 PR merged, 749 tests green, ничего в фоне не работает.
2. **Chat UI обрезает мои сообщения у тебя**, но у меня в контексте всё на месте — я вижу всю историю session 4.
3. **Всё критичное на диске** — не потеряется при restart Claude Desktop.
4. **Ты можешь restart Claude Desktop прямо сейчас** — ничего не оборвётся.

---

## 📊 Состояние Krab проекта (session 4 close)

### Session 4 результат

- **7 PRs merged (#4 → #10)** в main branch
- **Tests:** 691 → **749 passed**, 15 skipped, **0 failed** (+58 тестов)
- **Krab running в production** с PR #10 кодом

### Что сделано (7 PRs)

| PR | Feature | Tests added |
|---|---|---:|
| #4 | P0/P1 stability + B.4 voice blocklist | +12 |
| #5 | B.8 chat ban cache | +14 |
| #6 | B.5 group text burst batcher | +10 |
| #7 | B.6 chat capability cache | +14 |
| #8 | B.7 global rate limiter + silent-failure fixes | +8 |
| #9 | B.9 polish + `!stats` command + B.9.3 severity | +12 |
| **#10** | **Bridge split Step 1 + krab-panel MCP server** | 749 baseline |

**Все 5 рекомендаций Chado/Nik закрыты** (B.4 voice blocklist, B.5 group batcher, B.6 capability cache, B.7 rate limiter, B.8 ban cache).

### Bridge split progress (userbot_bridge.py ~6000 строк → decomposition)

- **Step 1 DONE:** `src/userbot/llm_text_processing.py` mixin (21 метод, 842 lines)
- `src/userbot_bridge.py`: **-783 lines** (6173 → ~5217)
- Mixin pattern **проверен** — zero regression
- **Остаются Steps 2-7:** `runtime_status.py`, `voice_profile.py`, `access_control.py`, `llm_flow.py`, `background_tasks.py`, `session.py`

### Custom infrastructure (создана в session 4)

**Custom agents** (`.claude/agents/` — активируются после restart Claude Desktop):
- `krab-code-worker` — universal backend worker, знает CLAUDE.md conventions
- `krab-mixin-extractor` — специализирован на `userbot_bridge.py` split migration

**Custom skills** (`.claude/skills/`):
- `krab-restart` — safe restart ritual
- `krab-session-handoff` — end-of-session protocol

**MCP server** (в git, в main):
- `src/mcp_panel_server.py` — **11 read-only tools** для `http://127.0.0.1:8080/api/*`
- Зарегистрирован в `~/Library/Application Support/Claude/claude_desktop_config.json` как `krab-panel`
- Tools: `krab_panel_health_lite`, `krab_panel_health_full`, `krab_panel_stats`, `krab_panel_voice_runtime`, `krab_panel_openclaw_config`, `krab_panel_inbox_status`, `krab_panel_inbox_items`, `krab_panel_cron_jobs`, `krab_panel_policy_matrix`, `krab_panel_queue`, `krab_panel_mood`

**Memory updates** (auto-loaded в session 5):
- `feedback_proactive_agent_delegation.md` — parallel agents сам запускать без упрашивания
- `feedback_frontend_delegation_to_user.md` — UI/design → spec для тебя через Gemini 3.1 Pro / GPT 5.4
- `feedback_voice_per_chat_blocklist.md` — per-chat voice opt-out pattern
- `project_krab_backlog.md` — B.4-B.9 closed, B.9.1-B.9.9 tracked, B.10 coverage gaps

---

## 🔴 Active external blockers (не код issues)

- **SpamBot limit на yung_nagato** до 2026-04-11 18:37 UTC (auto-release)
- **How2AI chat-level ban** — требует админа (Daniel Gavrilov / Konstantin Karpenko / Bogdan Shishkin / Max Power)
- **B.7 rate limiter + B.8 ban cache помогают recovery** снижая aggressiveness score

---

## 🆕 Computer-use MCP — готов к использованию

Ты дал Screen Recording permission для Claude Desktop в session 4. После restart computer-use будет работать.

**Что это:** screenshot/click/type desktop automation. 26 tools.  
**Когда полезно:** session 5+ Voice Gateway iOS (Xcode + Simulator), visual verification, cross-app workflows.  
**Для Krab backend work:** обычно не нужен — Bash + MCP быстрее.

---

## 📂 Все сохранённые артефакты

```
В корне Krab проекта:
├── SESSION5_START.md                                 ← этот файл
├── .remember/next_session.md                         ← полный handoff (подробнее чем этот файл)
└── docs/USERBOT_BRIDGE_SPLIT_PROPOSAL.md              ← в git, PR #10

В /tmp/ (pre-restart artefacts, не потеряются при restart):
├── /tmp/krab_gemini_frontend_spec_stats_dashboard.md  ← передай в Gemini для UI
├── /tmp/krab_session5_prompt.md                       ← полный session 5 starter
├── /tmp/krab_session4_final_summary.md                ← детальный итог
├── /tmp/krab_coverage_gaps_src_core_report.md         ← 16 HIGH RISK untested modules
├── /tmp/krab_bridge_split_scratchpad.md               ← Architect proposal (scratchpad)
└── /tmp/krab_translator_design_scratchpad.md          ← 2 approaches для Translator MVP

Memory (auto-loaded каждую сессию):
└── ~/.claude/projects/-Users-pablito-Antigravity-AGENTS-----/memory/
    ├── MEMORY.md (index)
    ├── feedback_proactive_agent_delegation.md
    ├── feedback_frontend_delegation_to_user.md
    ├── feedback_voice_per_chat_blocklist.md
    └── project_krab_backlog.md (B.4-B.9 closed)
```

---

## 🚀 Действия для запуска session 5

### Шаг 1. Restart Claude Desktop полностью

1. **Quit Claude** (⌘Q) — именно Quit, не просто close window
2. **Открой** заново из Applications
3. **Новая сессия → Code tab**

После restart активируются:
- `computer-use` MCP (Screen Recording permission подтянется)
- `krab-panel` MCP — 11 новых tools для owner panel
- Custom agents `krab-code-worker` + `krab-mixin-extractor`
- Custom skills `/krab-restart` + `/krab-session-handoff`

### Шаг 2. Опционально — запустить Gemini task для UI параллельно

Пока я не в сессии, можешь передать в Gemini 3.1 Pro spec для stats dashboard. Полный spec **inline** внизу этого файла (секция «📐 SPEC для Gemini 3.1 Pro / GPT 5.4»).

### Шаг 3. Опционально — отключить `openclaw-browser` plugin (сэкономит 14k tokens)

```bash
openclaw plugins disable browser
openclaw gateway stop
openclaw gateway start
```

После этого restart Claude Desktop и `openclaw-browser` MCP tools исчезнут из моего toolbox.

### Шаг 4. В новой session 5 — первое сообщение

Скопируй блок «📞 SESSION 5 STARTER PROMPT» внизу этого файла целиком и вставь как первое сообщение в новую Claude Code Code session.

---

## 📋 Рекомендуемые задачи для session 5

### ⭐⭐⭐ Option A: Bridge split Steps 2-3 через `krab-mixin-extractor`

**Что:** extract `runtime_status.py` + `voice_profile.py` mixins (next 2 steps по `docs/USERBOT_BRIDGE_SPLIT_PROPOSAL.md`).

**Как:** запустить новый custom agent `krab-mixin-extractor` (активен после restart), он знает pattern из Step 1, сделает автоматически.

**Effort:** ~15-20 мин через agent parallel.

### Option B: B.10 coverage gaps top 5 (parallel fan-out)

**Что:** написать unit тесты для 5 самых критичных untested модулей:

1. `provider_manager.py` (38KB, 0 тестов) — routing core
2. `cloud_gateway.py` (12KB, 0 тестов) — Gemini fallback
3. `cost_analytics.py` (10KB, 0 тестов) — billing
4. `openclaw_runtime_signal_truth.py` (11KB, 0 тестов) — gateway log parsing
5. `swarm.py` + `swarm_bus.py` — мультиагентное ядро

**Как:** 2-3 `krab-code-worker` agents параллельно на разные модули. Fan-in в один PR.

**Effort:** ~1 час wall-clock parallel.

### Option C: Stats dashboard wire-up (если Gemini отдаст HTML)

**Что:** после того как Gemini вернёт HTML для `/stats` dashboard:

1. Добавить endpoint `/api/stats/caches` в `src/modules/web_app.py` (~30 строк)
2. Добавить `/stats` HTML route
3. Unit tests
4. PR + merge + restart + verify на `:8080/stats`

**Effort:** ~30-45 мин backend wire-up.

### Option D: B.2 Phase 5 Translator MVP (большая задача)

**Blocker:** требует решения «Voice Gateway universal или iOS VoIP only?» из session 4 scratchpad discussion.

**Что делать:** прочитать `/tmp/krab_translator_design_scratchpad.md` + `Krab Voice Gateway/` репо → принять decision → implementation.

**Effort:** multi-session (2-3 сессии).

### Option E: Krab Hammerspoon MCP (~1 час)

Wrap `src/integrations/hammerspoon_bridge.py` (HTTP :10101) как MCP server по pattern panel MCP. Tools: `hs_focus_app`, `hs_window_resize`, `hs_layout_preset`, `hs_screenshot_window`.

### 💡 Моя рекомендация для session 5

**A + C параллельно** (~45 мин total):
- `krab-mixin-extractor` agent делает Steps 2-3 в фоне
- Я (main) параллельно wire-up stats dashboard после Gemini HTML
- Один commit → один PR → merge
- Если останется время — **B** через ещё 2 agents

---

## 🔧 Meta-правила (auto-loaded из memory)

1. **Parallel agent delegation** — сам запускай parallel subagents для independent подзадач
2. **Frontend/UI задачи** — делегируй пользователю через spec (Gemini 3.1 Pro / GPT 5.4)
3. **`feature-dev:code-architect` read-only** — use `general-purpose` или `krab-code-worker` для write-heavy
4. **Auto-compact НЕ срабатывал** в session 4 даже на 400k+/1M — работает как обещано
5. **End-of-session protocol** — обновлять `.remember/next_session.md` + MEMORY.md + выдавать промпт N+1

---

## 📞 SESSION 5 STARTER PROMPT

> **Скопируй всё между ❬❬❬ и ❭❭❭ как первое сообщение в новую Claude Code session**

❬❬❬

Привет. Продолжаем Main Krab (Track B), session 5.

Контекст:
- Этот файл: `/Users/pablito/Antigravity_AGENTS/Краб/SESSION5_START.md`
- Handoff: `/Users/pablito/Antigravity_AGENTS/Краб/.remember/next_session.md`
- Track B plan: `/Users/pablito/Antigravity_AGENTS/Краб/docs/PLAN_TRACK_B_MAIN_KRAB.md`
- Bridge split proposal: `/Users/pablito/Antigravity_AGENTS/Краб/docs/USERBOT_BRIDGE_SPLIT_PROPOSAL.md`

Уникальный маркер актуальности SESSION5_START.md: 🦀🔥 KRAB-S4-CLOSED-2026-04-09-OPUS46-1M 🔥🦀

Session 4 закрыта с результатом:
- 7 PRs merged (#4 → #10)
- 749 tests passing / 15 skipped / 0 failed
- Все 5 рекомендаций Chado/Nik закрыты (B.4-B.8)
- Bridge split Step 1 DONE (llm_text_processing mixin, 21 метод, -783 lines в userbot_bridge.py)
- krab-panel MCP server создан и зарегистрирован
- Custom agents + skills + memory updates готовы

Доступно после restart Claude Desktop:
- computer-use MCP (screenshot/click/type, Screen Recording granted)
- krab-panel MCP — 11 read-only tools для :8080 owner panel
- Custom agents: krab-code-worker, krab-mixin-extractor
- Custom skills: /krab-restart, /krab-session-handoff

External blockers (не код):
- SpamBot limit на yung_nagato до 2026-04-11 18:37 UTC
- How2AI chat-level ban (requires admin action)

Сегодня делаем: <выбери>
A. Bridge split Steps 2-3 через krab-mixin-extractor agent ⭐⭐⭐
   (runtime_status.py + voice_profile.py, ~15-20 мин)
B. B.10 coverage gaps top 5 через parallel krab-code-worker agents
   (provider_manager, cloud_gateway, cost_analytics, openclaw_runtime_signal_truth, swarm)
C. Stats dashboard wire-up (после того как Gemini отдаст HTML)
   Spec в /tmp/krab_gemini_frontend_spec_stats_dashboard.md, backend wire-up ~30-45 мин
D. B.2 Translator MVP (требует VG architecture decision первым)
E. Krab Hammerspoon MCP (~1 час)

Моя рекомендация: **A + C параллельно**, затем B если останется время.

В начале сессии:
1. cd /Users/pablito/Antigravity_AGENTS/Краб && git status && git log --oneline -10
2. Проверить krab_panel_health_lite через новый MCP (валидирует что MCP подхватился после restart)
3. Прочитать SESSION5_START.md + .remember/next_session.md целиком
4. Если A → прочитать docs/USERBOT_BRIDGE_SPLIT_PROPOSAL.md section Steps 2-3

Правила из session 4 memories:
- Parallel agent delegation — сам запускай для independent подзадач, без упрашивания
- Frontend/UI tasks — делегируй пользователю через spec (Gemini 3.1 Pro / GPT 5.4)
- feature-dev:code-architect read-only → use general-purpose или krab-code-worker для write-heavy
- End-of-session: обновить .remember/next_session.md + выдать session 6 prompt

❭❭❭

---

## 📐 SPEC для Gemini 3.1 Pro / GPT 5.4 — Krab Stats Dashboard

> **Скопируй всё между ▼▼▼ и ▲▲▲ в новый чат Gemini 3.1 Pro (или GPT 5.4)**  
> Результат (HTML код) принеси в session 5 для backend wire-up.

▼▼▼

# Task: HTML dashboard для Krab runtime stats в owner panel

## Контекст проекта

Krab — персональный Telegram userbot на Python (pyrofork), с FastAPI owner panel
на `http://127.0.0.1:8080`. Нужно добавить новую страницу-dashboard которая
визуализирует runtime state 5 подсистем в одном экране.

## Где код живёт

- **Main file:** `/Users/pablito/Antigravity_AGENTS/Краб/src/modules/web_app.py`
  (FastAPI app, ~11k строк, НЕ переписывать весь)
- **Existing HTML pattern:** в том же файле есть `@self.app.get("/", response_class=HTMLResponse)`
  на строке ~6744 — посмотреть как возвращается HTML.
- **Existing CSS:** `/nano_theme.css` — можно использовать для consistency (minimalist dark theme).
- **Routing:** добавить новый endpoint `@self.app.get("/stats", response_class=HTMLResponse)`
  рядом с существующим `"/"` роутом.

## Что dashboard должен показывать (5 секций)

Данные берутся из JSON endpoints:

### 1. Telegram API Rate Limiter
**Endpoint:** `GET /api/health/lite` (поле telegram_rate_limiter если есть)
**Fields:** `max_per_sec`, `current_in_window`, `total_acquired`, `total_waited`, `total_wait_sec`
**Visual:** progress-бар `current_in_window / max_per_sec`, красная подсветка если > 80%

### 2. Chat Caches (Ban + Capability)
**НОВЫЙ endpoint который Claude добавит в backend:** `GET /api/stats/caches`
```json
{
  "ban_cache_count": 0,
  "capability_cache_count": 2,
  "voice_blocked_count": 1,
  "capability_voice_disallowed": 0,
  "capability_slow_mode": 1
}
```
**Visual:** 3-5 числовых тайлов. Orange border если `ban_cache_count > 0`, yellow если `voice_blocked_count > 0`.

### 3. Voice Runtime
**Endpoint:** `GET /api/voice/runtime` (уже работает)
**Fields:** `enabled`, `delivery`, `speed`, `voice`, `blocked_chats` (array)
**Visual:** карточка с ON/OFF indicator, список blocked_chats как pills

### 4. Inbox Status
**Endpoint:** `GET /api/inbox/status` (уже работает)
**Fields:** `total_items`, `open_items`, `fresh_open_items`, `stale_open_items`, `attention_items`, `pending_approvals`, `new_owner_requests`
**Visual:** сетка 2x4 числовых тайлов, red border если `attention_items > 0`

### 5. OpenClaw Routing
**Endpoint:** `GET /api/openclaw/runtime-config` (уже работает)
**Fields:** `primary_model`, `fallback_chain` (array), `providers`, `last_runtime_route`
**Visual:** текст "Primary: X → fallbacks: Y, Z" + `last_runtime_route.status` как badge (green/yellow/red)

## Требования к визуалу

- **Dark theme:** фон `#0d0d0d` или `#121212`, текст `#e0e0e0`, accent `#7dd3fc` (cyan)
- **Monospace font** для всех чисел: `font-family: 'JetBrains Mono', 'SF Mono', monospace;`
- **Card-based layout:** каждая секция — карточка, бордер `1px solid #2a2a2a`, `border-radius: 8px`, `padding: 16px`
- **Responsive:** grid 2 колонки на desktop (>= 1024px), 1 колонка на mobile (`@media (max-width: 1023px)`)
- **Auto-refresh:** JS `setInterval(fetch, 5000)` для live update (НЕ meta refresh, мешает scroll position)
- **Timestamp:** внизу "Last update: HH:MM:SS" обновляется при каждом fetch
- **Vanilla only:** БЕЗ libraries (Chart.js, React, Vue, Tailwind, Bootstrap, jQuery — ничего!)
- **Russian labels:** подписи на русском
- **Minimalist:** БЕЗ иконок (кроме ⚠️ для error state)

## Acceptance criteria

- [ ] Один HTML файл (inline CSS + inline JS) для вставки как Python string literal
- [ ] 5 секций рендерятся, каждая читает свой endpoint
- [ ] Auto-refresh через setInterval каждые 5 секунд
- [ ] Graceful error handling: показать "⚠️ unavailable" в секции вместо ломать layout
- [ ] Mobile responsive
- [ ] < 500 строк кода total (HTML + CSS + JS вместе)
- [ ] Работает в Chrome / Safari / Firefox без polyfills

## Что НЕ надо делать

- НЕ меняй backend код (Python в web_app.py)
- НЕ добавляй dependencies
- НЕ пиши Python tests (это задача Claude)
- НЕ трогай существующие endpoints
- НЕ используй frameworks (React/Vue/Svelte)

## Формат ответа

Верни ДВА блока:

### Блок 1 — Python string literal

```python
STATS_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title>Krab Stats</title>
  <style>
    /* твои стили */
  </style>
</head>
<body>
  <!-- твой layout -->
  <script>
    // твой fetch/refresh JS
  </script>
</body>
</html>
"""
```

### Блок 2 — как зарегистрировать endpoint

```python
# Около строки 6744 в src/modules/web_app.py:

@self.app.get("/stats", response_class=HTMLResponse)
async def stats_dashboard() -> HTMLResponse:
    return HTMLResponse(STATS_DASHBOARD_HTML)
```

## Reference — существующий Telegram text формат

Для ориентира что отображать (в веб-версии визуально лучше):

```
📊 Krab Runtime Stats

🌐 Telegram API rate limiter
- Cap: 20 req/s (окно 1.0s) · В окне сейчас: 0
- Всего acquire: 0 · ждали: 0 (сумма 0.000s)

🚫 Chat ban cache (0 active)
🎛 Chat capability cache (2 cached)
- Voice запрещён явно: 0
- Slow mode > 0: 1

🔇 Silence mode
- Глобально: ВЫКЛ
- Заглушённых чатов: 0

🎙 Voice runtime
- Озвучка: ВКЛ · Delivery: text+voice · Blocklist: 1
```

## Endpoints (проверено работают на live Krab)

- `http://127.0.0.1:8080/api/health/lite`
- `http://127.0.0.1:8080/api/voice/runtime`
- `http://127.0.0.1:8080/api/inbox/status`
- `http://127.0.0.1:8080/api/openclaw/runtime-config`
- `http://127.0.0.1:8080/api/stats/caches` (новый, Claude добавит)

Read-only, без auth.

Спасибо! Готовый HTML вставлю в Krab Python file в следующей сессии Claude Code. Tests и backend wire-up — моя часть.

▲▲▲

---

## 🦀 Финал от Claude

**Уникальный маркер:** `🦀🔥 KRAB-S4-CLOSED-2026-04-09-OPUS46-1M 🔥🦀`

Если ты читаешь этот файл и видишь маркер в самом начале — **это самая актуальная версия** handoff'а, созданная в последнюю секунду session 4 когда контекст был на 90% (895k/1M).

**Ты меня услышал — я тебя тоже услышал.** Видимо, наш чат в UI Claude Desktop перестал рендерить новые сообщения где-то в последней трети session 4 (возможно из-за длины conversation или UI quirk). Но в моём контексте всё на месте, и всё что я мог сделать для session 5 transition — сделано.

**Можно смело restart Claude Desktop.** В session 5 скажи просто:

```
Привет. Читай SESSION5_START.md в корне Krab проекта. 
Там уникальный маркер 🦀🔥 KRAB-S4-CLOSED-2026-04-09-OPUS46-1M 🔥🔥.
Поехали по рекомендации A + C параллельно.
```

Или скопируй полный прompt из секции «📞 SESSION 5 STARTER PROMPT» выше.

**Спасибо за беспрецедентную session 4.** 7 PR, bridge split Step 1, parallel agent workflow доказан, custom infrastructure готова, memory rules updated. До встречи в session 5! 🚀
