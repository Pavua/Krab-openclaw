# Session N+1 — Starter Handoff (Wave 37 close, 2026-05-09)

> **Project**: Krab (Telegram userbot). Этот handoff — ТОЛЬКО про Krab.
> Krab Ear имеет свой handoff в `/Users/pablito/Antigravity_AGENTS/Krab Ear/.remember/next_session.md`.
> См. также [PROJECT_SEPARATION_GUIDE.md](../docs/PROJECT_SEPARATION_GUIDE.md) — как разделять
> работу над Krab vs Krab Ear в одном Claude Code чате.

## TL;DR

- **Krab main HEAD**: `82d03c6` (Wave 36) + Wave 37 + **Wave 38** в worktree
  `claude/cranky-allen-a8855f` (готов к merge, см. ниже)
- **Krab live**: PID 50465 (на момент close), session=ready, **fix НЕ deployed**
- **Wave 37 + 38 готовы** (worktree, не запушены): heartbeat reliability + reply target
  redirect + anaphora hint + tech-metaphors restraint + **inline mention link для
  users без @username**
- **Sentry**: Krab clean (0 unresolved); Krab Ear имеет 2 unresolved
  ([KRAB-EAR-BACKEND-F](https://po-zm.sentry.io/issues/KRAB-EAR-BACKEND-F), -E)
  — MLX whisper timeout 60s, fallback сработал. **НЕ Krab issue**.

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
