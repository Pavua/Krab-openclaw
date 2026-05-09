# Session N+1 — Starter Handoff (Session 42 close, 2026-05-09 evening)

> **Project**: Krab (Telegram userbot). Этот handoff — ТОЛЬКО про Krab.
> Krab Ear имеет свой handoff в `/Users/pablito/Antigravity_AGENTS/Krab Ear/.remember/next_session.md`.
> См. также [PROJECT_SEPARATION_GUIDE.md](../docs/PROJECT_SEPARATION_GUIDE.md).

## TL;DR — Session 42 закрыта 2026-05-09 ~19:00, 3 коммита Wave 44-A/B/C

**FINAL STATE (закрытие 2026-05-09 ~19:00)**:

- **main HEAD**: `743b1d2` Wave 44-C (post-reconnect updates verify)
- **3 коммита**: 44-A (Sentry statsPeriod coerce) + 44-B (watchdog gateway restart) + 44-C (split-brain post-reconnect verify)
- **Krab live**: PID 27224, session=ready, `KRAB_PROACTIVE_ENABLED=1`
- **E2E verified**: pavua DM → Krab reply за 4s после Stop+Start canonical
- **Tests**: +24 (Wave 44-A: 18, 44-C: 6); 192 watchdog+sentry+mcp slice green; ruff clean

### Session 42 highlights — split-brain emergency triage + permanent fix

**Производственный инцидент Day**: Wave 39-D split-brain detection логировал
"split_brain_resolved_via_reconnect", но на самом деле _try_reconnect_pyrofork
восстанавливал TCP/MTProto handshake без оживления updates_subscriber.
Произошло 2 раза 09.05 (~06:58 и ~18:46). Оба раза:
- pavua писал DM → Krab silent
- Krab отправлял proactive alerts/digests (invoke API alive)
- log казал "resolved", но incoming messages не дошли → ~75 мин и часы downtime
- Recovery только через manual kickstart core

**Wave 44-A (95e1a3f)** — Sentry MCP statsPeriod coercion:
Sentry SaaS 2025+ принимает только {'', '24h', '14d'} на /issues/?statsPeriod.
Любое другое (1h/7d/30d) → HTTP 400 "Invalid stats_period". Утром Wave 40-S
правильно фиксил User-Agent (это была реальная проблема), но эта 400 — другой
root cause. _coerce_stats_period() с маппингом + INFO-логом + JSON-ответ
exposes statsPeriod (effective) + statsPeriod_requested (original).

**Wave 44-B (a471e86)** — `scripts/telegram_session_watchdog.py`:
Заменил stop+pkill+spawn anti-pattern на sanctioned `openclaw gateway restart`
(verified subcommand exists). Также добавил OPENCLAW_BIN env override + multiple
search paths для LaunchAgent context (минимальный PATH).

**Wave 44-C (743b1d2)** — KILLER fix:
После `_try_reconnect_pyrofork` returns True, перед logging
`split_brain_resolved_via_reconnect` — **повторный** `_probe_updates_flow_alive`
с verify_settle_sec=clamp(10..15, check_interval*0.3). Если update_id всё
ещё frozen → log `split_brain_reconnect_did_not_restore_updates` + alert
owner + `_launchd_exit_78()` (full process respawn).

Trade-off: при truly quiet окне (3am, 0 incoming) verify может дать
false-positive failure. Принимаемо: 10s respawn vs скрытый split-brain
на час+.

Tests: 6 new (3 source-AST + 2 behavioral + 1 helper marker).
Wave 39-D regression: 5/5 still pass.

**Sentry status (24h post-deploy)**: 11 issues (1 Krab core stale PYTHON-FASTAPI-7S,
10 KE отдельный проект). Krab core actionable: 0.

**Memory pressure observed**: Pavua получал alerts `combined_rss_high:13.5GB / 19.7GB`
17:58-18:13. Известный backlog item P2 — KE backend RSS reduction.

---

## Производственная активность для следующей сессии

### P0 — Verify Wave 44-C in production
Wait for next split-brain эпизод. Должно быть видно в логе:
- ✅ Success path: `split_brain_resolved_via_reconnect verified=True verify_settle_sec=N`
- ✅ Escalation: `split_brain_reconnect_did_not_restore_updates` → `_launchd_exit_78` →
  launchd auto-respawn ~10s.

Если за 24h split-brain эпизодов 0 — отлично, проблема возможно решена и
без 44-C (но 44-C страховка).

### P1 — Memory pressure mitigation
KE backend RSS regularly hits 19+GB combined. Watchdog уже emit'ит alerts.
Возможные направления:
- Whisper-MLX модель в отдельный subprocess (better isolation)
- Перевод на меньшую whisper-medium-mlx если quality OK для use case
- Memory limits через cgroups/launchd ProcessLimits

### P1 — Wave 44-D candidate: false-positive mitigation для quiet windows
Если 44-C в production даёт false-positive escalation в quiet окно
(3am-7am когда трафика нет) — рассмотреть active probe:
вместо ожидания passive update_id движения, делать `client.get_chat_history(self_id, 1)`
который ДОЛЖЕН триггернуть update event если subscriber alive.

### P2 — Memory alert digest squelch
Текущие alerts на каждое RSS-spike — шумно. Сделать опциональный squelch
"RSS-alert не более 1/hour" чтобы не флудить owner DMs.

---

## Session 41 ARCHIVE — Wave 37 → 43-Z + 39-B activated (предыдущая итерация)

**FINAL STATE (закрытие сессии 2026-05-09 ~05:30)**:

- **main HEAD**: `be940b0` Wave 43-Z (Sentry hygiene)
- **12 коммитов**: Wave 37, 38, merge, separation guide, 39, 40, 41, 41-O, CLAUDE.md, 42, 39-B, 43-Z
- **Krab live**: PID 95129, session=ready, **`KRAB_PROACTIVE_ENABLED=1` ACTIVE** (added в plist EnvironmentVariables + .env)
- **Tests**: 13186+ collected (~219 added в этой сессии)

**Sentry status (24h)**: 6 issues resolved сегодня:
- PYTHON-FASTAPI-60 (53e/day, openclaw 500) — Wave 41-O downgrade to warning
- PYTHON-FASTAPI-6B (5e/day, health_check_failed) — Wave 42 exc_class fix
- PYTHON-FASTAPI-7X (5e/day, client closed) — Wave 41 root cause fix
- PYTHON-FASTAPI-Z (390e/24h, generic Traceback) — Wave 43-Z CancelledError shutdown filter
- PYTHON-FASTAPI-4T/4S (8e total, concurrent stream reads) — Wave 43-Z benign marker

**Sentry remaining**: 10 unresolved (5 KE Backend + 5 KE Agent App Hangs) — все Krab Ear project, отдельный repo per separation guide. **Krab core: 0 actionable issues** post-deploy.

**Wave 39-B activation status**: ENV ON в plist + .env. Default per-chat:
joins=ON для NORMAL, media=OFF, ai=OFF. На следующий join event в любом
NORMAL chat → Krab автоматически welcome (1/chat/day quota). Owner может
расширить через `!proactive media on` per chat.

**Производственная активность для следующей session**:
- Observe Sentry 24h — confirm Wave 41/41-O/42/43-Z eliminated noise
- Observe `proactive_event_dispatched` логи — verify joins работают, false positive rate, dismiss reactions
- Если в YMB pavua reply'нет с anaphora — verify Wave 39-X output redirect
- KE App Hangs — отдельная Krab Ear session (project_separation_guide)

---

## Session 41 ARCHIVE (для context — детали 12 коммитов)

## TL;DR — Wave 39 DEPLOYED 2026-05-09 ~02:30 (поверх Wave 37+38 ~02:09)

- **Krab main HEAD**: `6bb5c41` Wave 39 ← `5805465` docs ← `0dfe561` merge ←
  Wave 38/37 ← `82d03c6` Wave 36
- **Krab live**: PID 67301, session=ready
- **Wave 39-C VERIFIED**: `inbox_janitor_swept_acked kinds=[..., owner_mention, ...]
  matched=5 swept=5` на startup — 5 stale_processing_owner_mentions закрылись
  автоматически.
- **Wave 39 fix list**:
  - X) Output-based reply target (regression fix). Парсит начало LLM ответа
    "🐶, ..." / "@user" / `[name](tg://user?id=N)` → reply_target = referenced
    если matches. Lечит regression case 09.05 02:14 в YMB FAMILY FOREVER когда
    Wave 37-B anaphora regex не сработал на "поправил, попробуй снова".
  - A) Repetition guard. Token Jaccard similarity (0.6 threshold, 600s window),
    per-chat FIFO. На повтор text заменяется на "🦀 Уже сказал близко по теме
    чуть выше." Не спамит вариациями.
  - C) Stale processing owner_mentions fix. _AUTO_SWEEP_KINDS теперь включает
    "owner_mention". 5 stuck items swept на restart.
  - D) Updates-flow tracker. `_last_seen_update_id` (через message.id в
    _process_message) + `_probe_updates_flow_alive` для true split-brain
    detection (invoke alive + updates dead → graceful reconnect first).

## Предыдущая итерация: Wave 37+38 deployed 2026-05-09 ~02:09

- **Krab main HEAD**: `5805465` (docs) ← `0dfe561` (merge) ← `b92ec45` Wave 38 ← `ab4430f` Wave 37 ← `82d03c6` (Wave 36)
- **Krab live**: PID 61692, session=ready, telegram_heartbeat_started, network_offline_monitor_started
- **Krab Ear**: codesign re-applied (Sealed Resources `version=2 rules=13 files=1`,
  было `none`). spctl reject — норма для adhoc. Перм SIGKILL должен пропасть.
- **Wave 37+38 LIVE**: heartbeat reliability + reply target redirect + anaphora hint
  + tech-metaphors restraint + inline mention link для users без @username
- **Sentry**: Krab clean (0 unresolved); Krab Ear имеет 2 unresolved (MLX whisper
  60s timeout, fallback сработал) — **не Krab issue**.

---

## Что сделано в этой сессии (Wave 37, 2026-05-09)

Все changes в worktree `Краб/.claude/worktrees/cranky-allen-a8855f` (branch `claude/cranky-allen-a8855f`).
Тесты проходят: 638 passed (broad userbot scope), 40 новых tests в 3 files.

### Wave 37-A — Pyrogram heartbeat reliability (P0-1)

**Файл**: [src/userbot/network_watchdog.py](../src/userbot/network_watchdog.py:430)

Два связанных bug'а в `_telegram_heartbeat_loop`:

1. **Heartbeat success обновлял `_last_telegram_event_ts`** → маскировал split-brain detection
   в `_network_offline_monitor_loop`. Pyrogram session мог быть split-brain (invoke API alive,
   updates_subscriber dead) — silence monitor не срабатывал, потому что heartbeat'ы успешны.
   Теперь heartbeat success обновляет ТОЛЬКО новое поле `_last_heartbeat_ok_ts` (для diagnostics);
   `_last_telegram_event_ts` обновляется ТОЛЬКО в `_process_message` (real user events).

2. **Threshold=3 too lenient**: на 1 heartbeat timeout приходилось ждать ~12 минут до escalation.
   Теперь на 1 fail сразу попытка `_try_reconnect_pyrofork()` (graceful). Если success → counter=0,
   recovery в seconds. Если fail → counter держится, threshold safety net остаётся.

**Tests**: [tests/unit/test_telegram_heartbeat_wave37.py](../tests/unit/test_telegram_heartbeat_wave37.py) — 6 tests.
**Bridge atom**: `_last_heartbeat_ok_ts: float = time.time()` в `__init__` ([userbot_bridge.py:407](../src/userbot_bridge.py:407)).

### Wave 37-B — Reply target redirect + anaphora hint (P1-3, P1-5)

**Файлы**:
- [src/userbot/delivery_helpers.py](../src/userbot/delivery_helpers.py) — `_query_has_anaphora`, `_resolve_reply_target`
- [src/userbot/reply_preprocessor.py](../src/userbot/reply_preprocessor.py:160) — anaphora hint в `build_segmented_prompt`

**Issue 1 fix (P1-3)**: когда user пишет *"Краб, спроси его..."* в reply на сообщение от X,
Krab теперь отправляет ответ с `reply_to_message_id = X.id` (не trigger user'а). Helper:

```python
def _resolve_reply_target(source_message, query):
    referenced = getattr(source_message, "reply_to_message", None)
    if referenced and _query_has_anaphora(query):
        return referenced
    return source_message
```

**Issue 3 fix (P1-5)**: в LLM prompt добавляется блок-подсказка:
```
[Контекст: местоимения 'его/ему/её/ей' в текущем сообщении относятся к
@{reply_to_author} (автору цитаты выше), не к отправителю]
```

**Tests**: [test_anaphora_detection_wave37.py](../tests/unit/test_anaphora_detection_wave37.py) — 31 test.

**Anaphora regex** (word-boundary, RU+EN): `его/ему/него/нему/её/ей/неё/ней/ним/нею/him/her/his/hers`.

### Wave 38 — Inline mention link для users без @username

**Файл**: [src/userbot/delivery_helpers.py](../src/userbot/delivery_helpers.py:90) — `_inject_user_mention_link`.

**Источник проблемы** (verified в YMB FAMILY FOREVER 2026-05-09 01:14-01:18, msg
[767211→767223](https://t.me/c/1804661353/767211)):

- 🐶 (user без @username, just emoji nickname) joined chat (msg 767199, empty text)
- pavua: *"Краб, спроси его почему он не здоровается"* (msg 767211, reply на 767199)
- Krab отвечал в text "🐶, теперь точно тебе" но **plain text, не clickable**
- В Telegram UI mention не было navigable → user видел только текст-обращение

Wave 37-B (P1-3) уже redirect'ит **reply target** на 🐶 (через anaphora "его").
**Wave 38 добавляет inline mention link** — `[🐶](tg://user?id=N)` markdown syntax,
который Pyrofork render'ит как clickable mention.

**Helper logic**:
- Replace ТОЛЬКО при name в начале text (избегаем false positives внутри)
- Word-boundary check: `Ан` в `Антон` НЕ срабатывает
- Idempotent: уже linked text не дублируется
- Priority: `@username` > `first_name`

**Integration**: применяется в `_deliver_response_parts` ТОЛЬКО когда
`reply_target is not source_message` (т.е. Wave 37-B redirect сработал).

**Tests**: [test_inline_mention_wave38.py](../tests/unit/test_inline_mention_wave38.py) — 10 tests, все pass.

### Wave 37-C — Tech-metaphors restraint (P1-4)

**Файл**: [src/userbot/access_control.py](../src/userbot/access_control.py:285) — `_append_runtime_constraints`.

Issue 2: Krab перегружал ответы IT-аналогиями (SSH-сеансы, OAuth, ports, kernel,
Telegram-матрица). Добавил guidance в system prompt — лёгкая ирония + жизненные сравнения OK,
обязательные tech-метафоры в каждом ответе — нет.

**Tests**: [test_tech_metaphors_restraint_wave37.py](../tests/unit/test_tech_metaphors_restraint_wave37.py) — 3 tests.

### P0-2 — Krab Ear codesign fix (manual user action)

**Status**: команды готовы, ждут пользователя. Документация в
[docs/KRAB_EAR_CODESIGN_FIX.md](../docs/KRAB_EAR_CODESIGN_FIX.md).

**Действие**:
```bash
xattr -cr "/Applications/Krab Ear.app"
codesign --force --deep --sign - "/Applications/Krab Ear.app"
spctl --assess -vv "/Applications/Krab Ear.app"
```

После этого `Sealed Resources` должно показать non-zero count, perm crashes пропадают.
Это пометка для Krab Ear проекта, но без её выполнения KE app будет crashить на launch.

---

## Pending для следующей сессии

### P0 — Smoke test Wave 37+38 в production

После merge worktree → main:
1. Restart Krab (с user approval): `bash "/Users/pablito/Antigravity_AGENTS/new Stop Krab.command" && sleep 3 && bash "/Users/pablito/Antigravity_AGENTS/new start_krab.command"`
2. Observe Sentry 24-48h на новые `telegram_heartbeat_*` events.
3. Verify в YMB FAMILY FOREVER (или другом групповом чате):
   - Reply на чужое сообщение + "Краб, спроси его..." → ответ Krab уходит на
     **исходное** сообщение (не на trigger).
   - Inline mention в text Krab'а — кликабельный (для users без @username тоже).

### P2 — Wave 39 candidate: proactive event detection

User feedback (2026-05-09): "Краб не мог отвечать не на мои сообщения к нему,
а отмечать нужного пользователя или событие".

Wave 37-B + 38 решают **address part** (правильный reply target + clickable mention).
**Event part** (proactive welcome на join, реакция на media-events без trigger) —
требует обсуждения:

- Anti-spam guardrails (cooldowns, capacity per chat).
- Event types: `MessageService` (join/leave), photo/video posts, link previews.
- Trigger threshold для proactive — currently `KRAB_IMPLICIT_TRIGGER_THRESHOLD=0.4`.

**Не делать без обсуждения** — proactive поведение может раздражать в чатах где
Krab не должен светиться.

### P1 — UnboundLocalError 'token' в LM Studio

mlx_lm 0.31.3 bundled bug. Latest LM Studio runtime (1.7.0) ещё содержит. Wait for next LM Studio
release. KE patches (tool_choice=none) защищают.

### P1 — Sentry MCP HTTP 400 (Cloudflare WAF)

`mcp__krab-p0lrd__krab_sentry_status` возвращает HTTP 400 после long-lived process. Fix:
- `mcp-servers/telegram/server.py:2972-2981` — add explicit `User-Agent: krab-mcp/1.0` header
- + exponential backoff retry on 4xx
- Quick mitigation: `launchctl kickstart -k gui/$UID/com.krab.mcp-p0lrd`

### P1 — Smart routing trigger improvements

`trigger_detector.py` regex coverage хорошее для русского "краб"/"Краб". Возможно расширить:
- Ironic mentions ("ну где же Краб?")
- Compound mentions ("@yung_nagato Краб...")

### P2 — Memory pressure mitigation

36 GB M4 Max regularly hits 35G/36G. Watchdog panic when swap >32 GB.
- Investigate way to reduce KE backend RSS
- Possible: separate process for Whisper-MLX (better isolation)

### P2 — _network_offline_monitor с invoke probe для split-brain

Wave 37-A разделил timestamps, но `_probe_telegram_session_alive` использует тот же
`GetUsers([InputUserSelf()])` что и heartbeat → если invoke жив но updates dead, probe success
не помогает. Доработать: track `update_id` stream, detect когда updates остановились но invoke
жив → trigger restart.

---

## Style guide для следующих сессий

- Russian language
- НЕ рестартать Krab/KE без explicit user approval (особенно если active KE transcribe — check
  `tail err.log | grep frames/s` перед `launchctl kickstart -k gui/$UID/ai.krab.ear.backend`)
- TodoWrite для multi-step tasks (3+ items)
- Verify findings перед attribution (don't blame stream client without timestamp correlation)
- Skills: superpowers:systematic-debugging для bugs, superpowers:test-driven-development
  для implementation. RED → GREEN → REFACTOR строго.

---

## Полезные пути (Krab)

### Logs
- `~/.openclaw/krab_runtime_state/krab_main.log` — Krab structlog (~100 MB)
- `/Users/pablito/Antigravity_AGENTS/Краб/logs/krab_launchd.out.log` — combined stdout
- `~/.openclaw/krab_runtime_state/coexistence_monitor.log` (JSONL, every 60s)

### Health endpoints
- `curl http://127.0.0.1:8080/api/health/lite`
- `curl http://127.0.0.1:8080/api/runtime/status`

### Restart commands (только с user approval!)
- Krab: `bash "/Users/pablito/Antigravity_AGENTS/new Stop Krab.command" && sleep 3 && bash "/Users/pablito/Antigravity_AGENTS/new start_krab.command"`
- Targeted core only: `launchctl kickstart -k gui/$UID/ai.krab.core` (если launchd-копия активна)
- OpenClaw gateway: `openclaw gateway` (НЕ SIGHUP!)
- MCP-p0lrd: `launchctl kickstart -k gui/$UID/com.krab.mcp-p0lrd`

### Тесты
```bash
pytest tests/ -q                                                # все
pytest tests/unit/test_telegram_heartbeat_wave37.py -v          # Wave 37-A
pytest tests/unit/test_anaphora_detection_wave37.py -v          # Wave 37-B
pytest tests/unit/test_tech_metaphors_restraint_wave37.py -v    # Wave 37-C
pytest tests/unit/test_inline_mention_wave38.py -v              # Wave 38
ruff check src/ && ruff format src/
```

---

## Cross-project (Krab Ear) — minimal touchpoints

Krab Ear — **отдельный проект** с **отдельным handoff**. В этой сессии для KE сделано
ТОЛЬКО документирование codesign fix (manual action). Подробности — в Krab Ear handoff.

Если будущая сессия касается KE — открой папку `/Users/pablito/Antigravity_AGENTS/Krab Ear/`
и читай `Krab Ear/.remember/next_session.md` отдельно.

См. [docs/PROJECT_SEPARATION_GUIDE.md](../docs/PROJECT_SEPARATION_GUIDE.md).

---

## State right now (close of Wave 37 session)

- **Krab core**: PID 50465 на момент close, не рестартован после Wave 37 commits — fix
  ещё в worktree, **не на main**.
- **Worktree**: `claude/cranky-allen-a8855f` готов к PR/merge.
  Branch base: `82d03c6` (main).
- **Sentry**: clean baseline после 12 resolved 8-9 May.
- **Tests**: 12702+ collected, 40 новых в Wave 37 файлах.

## Команда для merge в main (когда user готов)

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
git worktree list  # увидишь worktrees/cranky-allen-a8855f
# Создать PR (если хочешь):
cd .claude/worktrees/cranky-allen-a8855f
gh pr create --title "Wave 37: heartbeat reliability + reply target + anaphora hint + tech-metaphor restraint" --body "..."
# Или merge напрямую:
git checkout main && git merge --no-ff claude/cranky-allen-a8855f
```

После merge — **restart Krab** (с user approval) для apply нового behavior в production.

---

## ✅ DEPLOYMENT log (2026-05-09 02:05-02:09)

- 02:05 Krab Ear codesign: `xattr -cr` + `codesign --force --deep --sign -`
  → Sealed Resources `version=2 rules=13 files=1` (было `none`). spctl reject
  — норма для adhoc-signed.
- 02:06 Wave 37 commit `ab4430f` + Wave 38 commit `b92ec45` в worktree branch.
- 02:06 Merge to main → `0dfe561` (no-ff merge).
- 02:06 Docs commit `5805465` (handoff + separation guide).
- 02:07 Stop Krab.command (graceful 6s + KE backend stopped + KE Agent stopped).
- 02:08 start_krab.command → launchd kickoff.
- 02:09 Krab UP (PID 61692): `telegram_heartbeat_started fail_threshold=3 interval_sec=240`,
  `network_offline_monitor_started threshold_sec=60`, session=ready.
