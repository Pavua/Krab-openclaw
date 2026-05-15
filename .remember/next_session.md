# Session 51 — Starter Handoff (Session 50 closed, 2026-05-16)

## TL;DR — Session 50 (2026-05-16, ~6 часов): 3 commits, 2 production fixes, 1 P5 verdict (no integration)

**main HEAD**: `011e281` (P3.5 + P0 + docs).

Session 50 закрыла **Pyrogram chat-drop bug** (P0, Wave 257-C — graceful-restart
catchup union с recent active dialogs) и **LM Studio prefix gap** (P3.5). P5
Rapid-MLX bench выполнен полностью, verdict: **NO integration** (baseline ≈
LM Studio, MTP regressed 2×).

После Session 49 → 50 transition был user reboot. После reboot всё работает
штатно: Krab health green, routing на `codex-cli/gpt-5.5` сохранилось, 3 commits
intact на `origin/main`.

## 🎯 Что сделано (production)

| Commit | Fix | Tests |
|---|---|---|
| `692bc37` | **P3.5** — `lmstudio/` (без дефиса) добавлен в `known_prefixes` admin router. POST switch + POST probe. | +1 |
| `4486ea4` | **P0** — `_run_graceful_restart_catchup_safe`: union whitelist + top-N recent active dialogs (env-tuned). Закрывает 13ч silence YMB. | +19 |
| `011e281` | **docs** — IMPROVEMENTS.md Session 50 entry + whitelist-gap pattern (3 в одной сессии → Wave-candidate audit) | — |

**Deployed**: Stop+Start Krab вечером 2026-05-15, верифицировано через health endpoint, swarm clients warmed up, startup catchup отработал штатно.

## 🐍 P5 Rapid-MLX — research complete, verdict NO

Установлен в `~/venvs/rapid-mlx/` (v0.6.50, изолированный venv с mlx-vlm 0.5.0).
Серьёзный bench cycle с Gemma 4 26B-A4B-it 4bit (path
`/Volumes/4TB SSD/LMStudio_models/mlx-community/gemma-4-26b-a4b-it-4bit`):

| Variant (t=0 greedy, max_tokens=300, RAM 18 GB free) | tok/s median |
|---|---|
| **Rapid-MLX baseline (no spec)** | **81.7** ✓ |
| Rapid-MLX MTP draft=2 | 37.3 ⚠️ regression 2.2× |
| Rapid-MLX MTP draft=4 + `--mtp-optimistic` | 38.9 ⚠️ regression 2.1× |

**Сравнение с Session 49 baselines**:
- mlx_vlm direct + MTP b=2 t=0 (Session 49 best): **101 tok/s** (внешний draft `guardiangate1775/gemma-4-26B-A4B-it-assistant-4bit`)
- LM Studio HTTP vanilla 4bit t=0 (greedy): **79.7 tok/s** (Session 49)
- Rapid-MLX baseline через HTTP: **81.7 tok/s** (Session 50) — лишь +2.5% vs LM Studio
- mlx_lm.server :8088 HTTP OptiQ (Krab production): 58.7 tok/s (Session 49)

**Verdict**: NO integration. Reasons:
1. **Baseline ≈ LM Studio** (+2.5%, noise-level). Нет смысла менять stable HTTP backend.
2. **MTP сломан** для Gemma 4 26B-A4B-it 4bit в Rapid-MLX v0.6.50. Empirically даёт 2× regression. Hypothesis: MTP head не embedded в weights, а Rapid-MLX MTP path не имеет external draft model API (тот что в mlx_vlm direct).
3. **DFlash ineligible** для Gemma 4: precision <8bit (4-bit), no declared drafter, no declared support, и даже после `pip install rapid-mlx[vision]` (mlx-vlm 0.5.0 теперь present) три других условия фейлят.
4. **Tools работают** через `--tool-call-parser gemma4` (subagent's research была неточной — sub claimed DFlash не работает с tool_calls, но реально в Rapid-MLX оба orthogonal).

**Что записать в архитектурный backlog** (если хочешь):
- Если когда-нибудь mlx-community зальёт **MTP-embedded** Gemma 4 weights (есть MTP head в bin) — пересмотреть. Тогда `--enable-mtp` будет работать как mlx_vlm direct.
- Либо upstream issue в rapid-mlx repo: feature request «external draft model для MTP» (текущий design предполагает только embedded head).
- Rapid-MLX **остаётся installed** в `~/venvs/rapid-mlx/` — может быть полезен для других моделей (Qwen3.5, hermes, etc) где MTP/DFlash declared support.

**Артефакты bench**: `/Volumes/4TB SSD/bench_tmp/`:
- `rapid_mlx_bench.py` — параметризованный bench script
- `rapid_mlx_baseline_t0.out` — 81.7 tok/s
- `rapid_mlx_mtp_d2_t0.out` — 37.3 tok/s
- `rapid_mlx_mtp_d4_opt_t0.out` — 38.9 tok/s

## 🔬 Architectural pattern сессии — "whitelist gap audit"

3 root causes в одной сессии оказались **whitelist gaps**:
- **P0**: `_resolve_catchup_target_chats` whitelist → YMB chat silence
- **P3** (defer): `_CLOUD_PROVIDERS` hardcoded vs runtime aliases
- **P3.5**: `known_prefixes` set без `lmstudio` (без дефиса)

Записано в IMPROVEMENTS.md как Wave-candidate: единый audit/codegen для prefix
sets (`assert all_prefixes_in_use ⊆ known_prefixes` в test suite либо derive
из cloud_inventory automatically).

Также session применила **"outcomes-not-heartbeats"** pattern (Session 45) к
graceful_restart_triggering_catchup: теперь не предполагает что whitelist ==
"all critical chats" — реально проверяет recent activity через iter_dialogs.

## 🐛 Открытые баги для Session 51

### 🟡 P1 — Verify Wave 257-B heartbeat (carried over from S50)

Wave 257-B heartbeat patch (Session 49) deployed, unit tests passed, но
**natural slow codex request (≥60s) не наблюдался** в production за 24h.
Codex traffic упал в 7-10× после Wave 62-G (codex weekly quota preempt в hot
path) → 0 slow requests / 24h. Patch остаётся untested в prod.

**Verify path**: либо подождать естественного long-running codex (может month'ы),
либо искусственно через `!swarm coders research <heavy topic>` (route через
codex-cli напрямую). Log-marker: `module=llm_flow + "🦀 Codex думает"` при
`route_model=codex-cli/* + elapsed_sec>=60`.

### 🟡 P1 — Verify P0 catchup union в prod

Wave 257-C deployed (Session 50). При первом `graceful_restart_triggering_catchup`
event должны увидеть в логе:
- `graceful_catchup_recent_dialogs_resolved count=N hours=6 limit=30`
- `graceful_catchup_targets_merged whitelist_count=2 recent_active_count=N total_unique=M`

Тестовый сценарий не triggered automatically — нужен либо natural network flap,
либо artificial: `pkill -SIGSTOP -f userbot_bridge && sleep 60 && pkill -SIGCONT
... && подождать reconnect`. Можно также через `iptables drop` Telegram DCs на
1-2 минуты.

### 🟢 P2 — codex login для account2/account3 (carried over from S49)

В `~/.codex_accounts/`:
- `primary/auth.json` ✅
- `account2/`, `account3/` — папки есть, `auth.json` отсутствует

**Fix** (user-action, Terminal): `CODEX_HOME=~/.codex_accounts/account2 codex login`
+ повторить для account3. После этого multi-account rotation в
`src/integrations/cli_subprocess_bypass.py:341-565` оживёт.

### 🟢 P3 — Hardcoded vs dynamic registry rationalize (IMPROVEMENTS.md noted)

`models_admin_router._CLOUD_PROVIDERS[4].models` (mlx-local-kv4 section)
содержит hardcoded entries, параллельно `_build_lm_studio_local_dynamic_section`
отдаёт full ids из autodiscovery. Dual source of truth — пользователь видит
оба варианта в UI. Workaround alias-слой работает, **runtime errors уже закрыты
Wave 257-A**, но UX cleanup.

Direction:
- (A) sync hardcoded entries с runtime ids + dedup в picker
- (B) удалить hardcoded MLX-секцию, оставить dynamic discovery (требует
  расширения `_build_lm_studio_local_dynamic_section` на mlx-local-kv4)

## ⚡ Quickstart следующей сессии

```bash
# 1. Health check
curl -sS http://127.0.0.1:8080/api/health | python3 -m json.tool
curl -sS http://127.0.0.1:8080/api/admin/routing-active | python3 -m json.tool

# 2. Если Krab лежит:
"/Users/pablito/Antigravity_AGENTS/new start_krab.command"

# 3. Sentry status 24h:
# mcp__krab-yung-nagato__krab_sentry_status statsPeriod=24h

# 4. Verify P0 catchup union сработал хоть раз (если в логе есть):
grep "graceful_catchup_targets_merged\|graceful_catchup_recent_dialogs_resolved" \
  ~/.openclaw/krab_runtime_state/krab_main.log | tail -5

# 5. Bench scripts (если ещё захочешь возиться с MLX):
ls /Volumes/4TB\ SSD/bench_tmp/

# 6. Rapid-MLX готов к запуску (dormant venv):
~/venvs/rapid-mlx/bin/rapid-mlx models  # list aliases
```

## 📊 Текущее состояние (2026-05-16 ~после reboot)

- **Krab**: health=ok (4/4 checks green), routing=codex-cli/gpt-5.5, started через launchd
- **OpenClaw Gateway**: `:18789` running (LaunchAgent)
- **`:8088` mlx_lm.server**: running (RotorQuant venv, OptiQ-4bit configured, 0 loaded)
- **LM Studio :1234**: running, 0 models loaded
- **Voice Gateway**, **Krab Ear**: green
- **Sentry 24h**: 0 events (Wave 256/257 fixes hold)
- **Memory**: 3.4 GB free, swap 16.7 GB used (fresh reboot, Chrome already heavy)
- **Tests collected**: 15960+ (15960 = 15940 baseline + 19 P0 tests + 1 P3.5 test)

## 🛑 Уроки сессии

| Memory file | Урок |
|---|---|
| `feedback_subagent_research_accuracy` (NEW?) | Subagent's research через WebFetch может быть **неточной по features**. Session 50: subagent сказал «DFlash не работает с tool_calls» (incorrect — они orthogonal в rapid-mlx). Урок: всегда verify subagent claims через прямое чтение CLI/docs/code. Subagents хороши для discovery, но specific feature claims нужно double-check. |
| `feedback_event_delays` (NEW?) | Background task notifications приходят с задержкой к user → мои status updates часто stale. Урок: меньше chatter про статус, batch work, poll state directly when about to take action, action на состояние а не на event timestamps. |
| `feedback_ram_pressure_36gb` (existing) | Подтверждено снова: Rapid-MLX MLLM на 4bit Gemma 4 26B = 22GB estimate working set, при 36GB system это 96% utilization → kernel panic risk. `--gpu-memory-utilization 0.75 --max-num-seqs 1` помогает но всё равно ест 18+ GB. |

## 🎯 P0 для Session 51 (приоритеты)

1. **Verify P0 catchup union sticked** — найти в логе хотя бы 1 событие `graceful_catchup_targets_merged`. Если нет за 24h → artificial trigger через network simulation.
2. **Verify Wave 257-B heartbeat** — natural slow codex либо artificial heavy prompt через codex bypass.
3. **(Возможно) P3 cleanup** — Option B (drop hardcoded MLX из `_CLOUD_PROVIDERS`, extend dynamic discovery). Low-risk refactor.
4. **(Optional) Sentry triage** — проверить за 24h нет ли регрессий от deployment Session 50.
5. **(Optional) Whitelist-gap audit Wave** — test assertion `all_prefixes_in_use ⊆ known_prefixes`, начало codegen unified prefix registry.

Удачной сессии 🦀
