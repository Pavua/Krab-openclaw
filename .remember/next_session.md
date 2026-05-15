# Session 52 — Starter Handoff (Session 51 closed, 2026-05-16)

## TL;DR — Session 51 (~5 часов): 4 commits, 1 prod bug fix, mlx_lm bench complete, +31% throughput recommendation

**main HEAD**: `ad0d20b` (video_note reply fix). После этого: P0 diag + P3 cleanup + video_note.

Session 51 продолжила Session 50 fixes (P3.5+P0+P3) и закрыла P5 research цикл
с mlx_lm.server. **Главные находки**: (1) `gemma4_assistant` arch не
поддерживается ни в mlx_lm 0.31.3 ни в LM Studio mlx-engine — все HTTP-paths для
external draft DEAD END, (2) vanilla 4bit на mlx_lm.server = **77.1 tok/s**
vs current OptiQ-4bit production = 58.7 → **+31% throughput бесплатной заменой**,
(3) video_note reply bug найден + закрыт.

## 🎯 Что сделано (production)

| Commit | Fix | Tests |
|---|---|---|
| `1e5fb00` | **P0 diag** — `_resolve_recent_active_chats` теперь логирует early returns + filter breakdown counters (enumerated/no_chat_id/no_date/too_old). Раньше silent return на client=None маскировал root cause `recent_active_count=0`. | 19 P0 tests still green |
| `497f7bf` | **P3** — drop hardcoded `mlx-local-kv4` из `_CLOUD_PROVIDERS`, use `build_mlx_local_provider_group` (Wave 240) для dynamic discovery. `_get_all_providers()` helper joins cloud+local. Explicit `known_prefixes.add("mlx-local-kv4")` safety belt. | +3 tests (58/58 green) |
| `ad0d20b` | **video_note reply** — owner reported 2026-05-16: «Краб, кружок поглядеть?» → Krab ответил «медиафайл не передан в runtime». Reply media extraction обрабатывал photo/animation, video/video_note был gap. 1 elif branch + delegation в `_process_video_message`. | (ad-hoc — Wave 16-G pattern) |

## 🐍 P5 final verdict — все HTTP paths для external draft Gemma 4 BLOCKED

### Findings from 6 parallel research subagents (Session 51 start)

| Path | Status | Reason |
|---|---|---|
| **mlx_lm.server + --draft-model** (the killer finding) | ❌ BLOCKED | `ValueError: Model type gemma4_assistant not supported` — mlx_lm 0.31.3 не имеет gemma4_assistant arch для draft loading |
| **LM Studio Python SDK + draftModel** | ❌ BLOCKED | mlx-engine не распознаёт `gemma4_assistant` arch (user confirmed visually: LM Studio shows 0 compatible drafts) |
| **Rapid-MLX --enable-mtp** (Session 50) | ❌ BLOCKED | MTP regress 2× (37 vs 81 baseline); only embedded MTP head, no external draft API |
| **mlx-openai-server (cubist38)** | ⚠️ untested | Same `--draft-model-path` flag — likely same arch block |
| **vllm-mlx** | ❌ N/A | Embedded MTP only (Qwen3-Next), no external draft API |
| **mlx_vlm direct** (Session 49: 101 tok/s) | ✅ работает | НО не HTTP server, нет tool calling integration с OpenClaw |
| **Ollama PR #15980 Gemma 4 MTP** | ⚠️ alt | Merged, но `/api/chat` ≠ OpenAI-compat для Krab routing — нужен адаптер |

**Bench results (Session 51, mlx_lm.server vanilla 4bit, t=0 greedy, 800 max_tokens)**:
- baseline (no draft): **77.1 tok/s median** ✅
- + draft model attempt: **server crashed at first request** (`Model type gemma4_assistant not supported`)

**Comparison table (cross-session)**:

| Stack | tok/s |
|---|---|
| mlx_vlm direct + MTP (S49, не HTTP) | **101** |
| LM Studio HTTP vanilla 4bit (S49) | 79.7 |
| Rapid-MLX baseline (S50) | 81.7 |
| **mlx_lm.server vanilla 4bit (S51)** | **77.1** |
| mlx_lm.server :8088 HTTP OptiQ (production now) | 58.7 |

### 💡 P5 silver lining — free +31% throughput

**Recommendation**: обновить `~/Library/LaunchAgents/com.user.mlx-lm-server.plist`
заменив target:
```diff
- <string>/Volumes/4TB SSD/LMStudio_models/mlx-community/gemma-4-26B-A4B-it-OptiQ-4bit</string>
+ <string>/Volumes/4TB SSD/LMStudio_models/mlx-community/gemma-4-26b-a4b-it-4bit</string>
```

Без всякого spec decoding — **+31% throughput на :8088** (58.7 → 77.1 tok/s).
RotorQuant использует OptiQ для quality-related research, но для **production
Krab throughput vanilla 4bit лучше**. Можно либо:
- (A) Изменить production plist на vanilla 4bit (нужно user buy-in, RotorQuant impact)
- (B) Создать second instance :8089 с vanilla 4bit, route Krab туда через
  `MLX_LOCAL_KV4_URL=http://127.0.0.1:8089`
- (C) Defer — текущая Krab routing использует cloud (codex-cli) в hot path,
  local fallback редкий

Не applied autoматически — это user/RotorQuant decision.

### Альтернативы для true spec decoding (defer):

1. **Найти `gemma4` arch draft** (не `gemma4_assistant`) — нет такого в HF
   на сегодня (subagent search exhaustive). Только `guardiangate1775` суите.
2. **Wait for mlx-lm upstream PR** — поддержка `gemma4_assistant` arch как
   draft в mlx_lm. Можно открыть issue/PR в ml-explore/mlx-lm.
3. **Wrap mlx_vlm direct в FastAPI** — кастомный HTTP-сервер вокруг 101 tok/s
   path. Heavy lift, untested tool calling.
4. **Ollama Gemma 4 MTP** + adapter `/api/chat` → OpenAI-compat. Medium lift.

## 🐛 Открытые баги для Session 52

### 🟡 P0 — Verify P0 diag (S51 commit 1e5fb00) после следующего graceful_restart

После next `graceful_restart_triggering_catchup` event ищи в логе:
```bash
grep "graceful_catchup_recent_skipped" ~/.openclaw/krab_runtime_state/krab_main.log
grep "graceful_catchup_recent_dialogs_resolved" ~/.openclaw/krab_runtime_state/krab_main.log
```

**Expected diagnostic signals** (нужен **Krab restart** для применения patch):
- `graceful_catchup_recent_skipped reason=no_client` → подтверждает гипотезу
  что `self.client` None в момент graceful-restart hook (race condition с
  reconnect). Fix: задержка hook'а либо retry если client None.
- `graceful_catchup_recent_skipped reason=limit_zero` → user disabled через
  `KRAB_GRACEFUL_CATCHUP_RECENT_LIMIT=0`.
- `graceful_catchup_recent_dialogs_resolved` с `enumerated=0` → iter_dialogs
  возвращает empty (Pyrogram cache state issue).
- `... enumerated=N filtered_too_old=N` (одинаковые) → hours window (6h)
  слишком узкий, нужно увеличить.

### 🟡 P1 — Verify video_note reply fix (S51 commit ad0d20b) после restart

Owner может повторить scenario: в любом chat → reply text на video note (кружок).
Krab должен ответить по содержимому видео (не «не передан в runtime»).
Log marker: `module=media_processors` + `video_perceptor_*` events.

### 🟢 P2 — codex login для account2/account3 (carried over)
Unchanged. User Terminal action.

### 🟢 P3 — Production routing optimization (NEW)

`com.user.mlx-lm-server.plist` использует OptiQ-4bit (58.7 tok/s). vanilla
4bit даёт 77.1 tok/s (+31%). Recommendation: либо одна замена в plist, либо
parallel instance :8089 для Krab. **Не applied — нужен user decision**.

## ⚡ Quickstart следующей сессии

```bash
# 1. Health check
curl -sS http://127.0.0.1:8080/api/health | python3 -m json.tool
curl -sS http://127.0.0.1:8080/api/admin/routing-active | python3 -m json.tool

# 2. Если Krab лежит:
"/Users/pablito/Antigravity_AGENTS/new start_krab.command"

# 3. Verify S51 fixes after Krab restart (нужен restart!):
LOG=~/.openclaw/krab_runtime_state/krab_main.log
# P0 diag
grep -E "graceful_catchup_recent_(skipped|dialogs_resolved)" $LOG | tail -5
# P3 — admin panel render
curl -sS http://127.0.0.1:8080/admin/models | grep -c "mlx-local-kv4"
# video_note bug → попроси owner reply на кружок в любом chat

# 4. Bench scripts (для optional follow-up):
ls /Volumes/4TB\ SSD/bench_tmp/   # rapid_mlx_bench.py, mlx_lm_server_bench.py, lmstudio_bench.py

# 5. mlx_lm.server status:
launchctl list | grep mlx-lm
curl -sS http://127.0.0.1:8088/v1/models | python3 -m json.tool

# 6. Rapid-MLX dormant в isolated venv (для future):
~/venvs/rapid-mlx/bin/rapid-mlx models  # list aliases
```

## 📊 Текущее состояние (2026-05-16 ~22:30+)

- **Krab**: health ok, routing `codex-cli/gpt-5.5` (cloud), launchd-managed
- **:8088 mlx_lm.server**: PID 10314, OptiQ-4bit loading после launchctl restore
- **OpenClaw Gateway**: `:18789` running
- **LM Studio :1234**: 0 models loaded (was Gemma — unloaded в S50)
- **Voice Gateway**, **Krab Ear**: green
- **RAM**: ~6.9 GB free (mlx_lm.server warming up OptiQ-4bit ~15 GB)
- **Sentry**: clean (Session 50 fixes hold)
- **Tests collected**: 15960+ (S51 added 3 P3 tests, retained 19 P0)

## 🛑 Уроки сессии

| Memory file | Урок |
|---|---|
| `feedback_arch_mismatch_draft_block` (NEW?) | Спекулятивное декодирование с external draft model требует **same arch** target + draft. `gemma4` vs `gemma4_assistant` — separate archs, не interoperable в mlx_lm/mlx-engine (только mlx_vlm 0.5.0+ умеет MTP-через-assistant). Subagent's research about LM Studio `draftModel` parameter был optimistic — реальный mlx-engine также blocked. |
| `feedback_launchctl_supervised_processes` (NEW?) | Простой `kill PID` не помогает для launchd-managed процессов (auto-respawn). Нужен `launchctl unload <plist>` + verify через `pgrep`. После bench restore через `launchctl load`. |
| `feedback_gemma_thinking_mode_response_shape` (NEW?) | Gemma 4 thinking mode в mlx_lm.server возвращает `message.reasoning` field, не `message.content`. Bench scripts должны fallback: `text = msg.get("content") or msg.get("reasoning") or ""`. Также max_tokens нужен достаточный (800+) чтобы model успела закрыть thinking и начать content. |
| `feedback_reply_media_extraction_pattern` (existing) | Reply media extraction для photo/animation существует, но video/video_note был gap. Pattern: `if not has_direct_X and reply_msg has X → process_X(message=reply_msg, ...)`. Same as Wave 16-G (audio). |

## 🎯 P0 для Session 52 (приоритеты)

1. **Restart Krab** чтобы применить video_note fix (ad0d20b) + P0 diag (1e5fb00) + P3 (497f7bf).
   - `new Stop Krab.command` + wait + `new start_krab.command`
2. **Verify video_note fix** — попросить owner повторить scenario (reply на кружок)
3. **Wait for P0 diag signals** — natural graceful_restart event, прочитать новые
   log events чтобы понять root cause `recent_active_count=0`
4. **Production routing decision** — оставить OptiQ-4bit (RotorQuant research) либо
   migrate на vanilla 4bit (+31% throughput). User decides.
5. **(Optional) Open mlx-lm upstream issue** — request `gemma4_assistant` arch
   support для draft loading. Github issue в `ml-explore/mlx-lm` repo.

Удачной сессии 🦀
