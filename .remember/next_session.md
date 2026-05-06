# Session 39 — Starter Handoff (Session 38 close, 2026-05-05)

## TL;DR

- **main HEAD**: `40c3e3d` (Wave 28-B macOS swap threshold)
- **Krab live**: running, session=ready, 436 peers (восстановлено из bak.1777575968)
- **Wave 22-A codex bypass VERIFIED in prod**: `codex exec --model X "PROMPT"` отвечает 14-16s
- **Wave 25-D !quota VERIFIED in prod**: multi-provider статус в одном TG-сообщении
- **Wave 26-F Russian Краб detection VERIFIED in prod**: "Краб, скажи привет" → ответ
- **Anthropic Vertex quota PENDING**: user отправил email с corp `pavelr7@rongfa.biz`, ждём approval от Cy/Elle (1-4 часа typically)

## Что сделано в Session 38 (2026-05-05) — 17+ waves, ~30 commits

### Bypass infrastructure (новые провайдеры обходящие broken OpenClaw transport)
- **Wave 23-A** `5ab4286` — Vertex AI direct SDK bypass для `google-vertex/*` через `google.genai(vertexai=True)`
- **Wave 23-B** `c261a34` — DEFAULT_LOCATION=`global` (gemini-3.x preview доступны только там)
- **Wave 23-C** `fa46fc3` — Anthropic Claude через Vertex (`anthropic-vertex/*`, region=us-east5) — bypass готов, ждёт квоты
- **Wave 25-E** `c370ff4` — Gemma fallback через AI Studio (`gemma-3-27b/12b/4b`, free 14400/day на 27b)
- **Wave 22-A-fix** `49d7be3` — codex CLI требует `exec` subcommand + positional prompt (`-p` зарезервирован под `--profile`)

### Каталог OpenClaw (модели в panel dropdown)
- **8 Gemini** в `google-vertex`: 2.5-pro/flash/lite, 2.5-flash-lite-preview-09-2025, 3-flash-preview, 3.1-pro-preview, 3.1-pro-preview-customtools, flash-latest
- **5 Gemini** в `google-gemini-cli` (free OAuth): 3.1-pro-preview, 3-pro-preview, 3-flash-preview, 2.5-pro, 2.5-flash
- **6 Claude** активированы в Cloud Console (Opus 4.7/4.6/4.5, Sonnet 4.6/4.5, Haiku 4.5) — quota=0 RPM, ждём approval
- **3 Gemma** в `google` (AI Studio): 3-27b-it, 3-12b-it, 3-4b-it

### Multi-account / rotation
- **Wave 24-A** `ae1c560`+`f8c8538` — codex rotation (~/.codex_accounts/{primary,account2,account3}/, LRU + quota tracking, GET `/api/codex/accounts`)
- `primary` symlink → `~/.codex` (existing ChatGPT Plus аккаунт)
- account2/account3 — пустые dirs ждут `./scripts/setup_codex_account.sh accountN` (ручной 2FA login)

### Stability / corruption recovery
- **Wave 24-B** `bb5dfe1` — peers threshold (≥50) + WAL/SHM stale cleanup в session preflight
- **Wave 24-C** (inline в `Stop Krab.command`) — post-doctor primary reapply (config supremacy против doctor wizard)
- **Wave 24-D** `bfbb3d6` — graceful shutdown 15s grace (было 0.5-0.8s) + WAL checkpoint pre-exit + Wave 16-F auto-clear OFF
- **Wave 27-A** `7c405d1` — network resilience: active TCP probe to Telegram DC + auto-reconnect + alert debounce 1800s, threshold 60→180s

### UX / observability
- **Wave 24-E** `d8cd118` — `/api/model/status` `reconciled_state` (configured/last_executed/policy_recommendation + active_display "X (last: Y ✓ Nm ago)")
- **Wave 25-A** `55b746c` — OAuth auto-resync daemon (gemini-cli `~/.gemini` → OpenClaw, 15min interval, LaunchAgent `ai.krab.oauth-resync`)
- **Wave 25-B** `5092f44` — Krab Ear coexistence monitor (combined RSS/swap/RAM thresholds → `/api/notify` Telegram alerts)
- **Wave 25-D** `ac0fea4` — `!quota` Telegram command (multi-provider state в одном сообщении)
- **Wave 25-D-fix** `e3f996c` — Pyrogram `parse_mode=ParseMode.MARKDOWN` enum (не строка lowercase)
- **Wave 28-B** `40c3e3d` — macOS-friendly swap threshold 16GB (compressed swap не = OOM)

### Quality / mention detection
- **Wave 25-F** `642b521` — sender_context учитывает русское "Краб" + 🦀 + dynamic @username
- **Wave 26-A** `8716592` — greeting target hint в LLM context: когда owner просит "Краб поздоровайся с X" + reply_to set → LLM получает inline `[Имя](tg://user?id=X)` directive
- **Wave 26-B** `062fe4e` — implicit question detection в trigger_detector — 10-min window после ответа Krab + эвристики "продолжай / а если / что думаешь / ?$"

### Documentation
- **Wave 28-A** `4ab1b03` — CLAUDE.md split (72KB → 21KB, -70%) — auto-tables вынесены в `docs/CLAUDE_AUTO_*.md`

## Pending для Session 39

### Blocked на user actions
1. **Anthropic Vertex quota approval** — Google ответ ожидается. После approval bypass auto-engage'нет, нужно добавить `anthropic-vertex/*` в panel fallback chain.
2. **account2 + account3 codex login** — `./scripts/setup_codex_account.sh account2` (2FA flow). Утроит codex квоту.

### Можем делать
3. **24h+ uptime verification** — проверить что Wave 24-B/D/27-A реально устранили recurring corruption после длинной uptime
4. **Mass production verify** — групповой чат, DM, voice, swarm, mention/reply сценарии

### Backlog
5. CLAUDE.md autotables refresh routine — `scripts/refresh_claude_md_autotables.py` weekly (теперь когда файлы маленькие)
6. `!quota` panel endpoint `/api/quota` — UI version вместо TG-only
7. Make gemma fallback selectable в panel
8. Krab Ear memory investigation (5 процессов, 2.16GB RSS)
9. Sentry observation после 24h running с Wave 24-D
10. anthropic-vertex/* в каталог OpenClaw (когда квота приедет)

## Operational quick reference

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб

# Krab control (Wave 24-D 15s grace)
"/Users/pablito/Antigravity_AGENTS/new Stop Krab.command"
"/Users/pablito/Antigravity_AGENTS/new start_krab.command"

# Health
curl -sS http://127.0.0.1:8080/api/health/lite | python3 -m json.tool
curl -sS http://127.0.0.1:18789/health  # gateway

# Multi-account codex
./scripts/setup_codex_account.sh account2  # ручной 2FA → ~/.codex_accounts/account2/
curl -sS http://127.0.0.1:8080/api/codex/accounts | python3 -m json.tool

# Quota check
# В Telegram: !quota --no-probe (быстро) | !quota (с probe ~30s)
gemini --model gemini-2.5-flash -p "ok"  # gemini-cli probe вручную

# Recovery (если session corrupt)
sqlite3 data/sessions/kraab.session "PRAGMA integrity_check; SELECT count(*) FROM peers"
cp data/sessions/kraab.session.bak.1777575968 data/sessions/kraab.session  # 436 peers backup

# Memory baseline (Wave 25-B)
tail -5 ~/.openclaw/krab_runtime_state/coexistence_monitor.log
```

## Critical operational notes

- **НИКОГДА** не использовать `launchctl kickstart -k` (causes session corruption)
- **Wave 24-D 15s grace** даёт SQLite checkpoint завершиться чисто
- **WAL/SHM cleanup при stale process** handled by Wave 24-B preflight
- **codex exec subcommand обязателен** для CLI bypass (-p = profile, не prompt)
- **Vertex location=global** для gemini-3.x preview, us-east5 для Anthropic Claude
- **Anthropic quota запрашивать с corp email** (10 RPM Sonnet/Haiku, 5 RPM Opus = sweet spot)
- **macOS swap 8-12GB ≠ OOM** — Wave 28-B threshold поднят до 16GB
- **Wave 25-A OAuth auto-resync** держит panel зелёным без ручного синка

## Session 38 stats

- **17+ waves**: 23-A/B/C, 24-A/B/C/D/E, 25-A/B/D/E/F, 26-A/B/F, 27-A, 28-A/B/C
- **30+ commits** (cd7ea91 → 40c3e3d)
- **3 LaunchAgents** добавлены: oauth-resync, coexistence-monitor, memory-baseline
- **6 моделей Claude** активированы (ждут квоту)
- **3 production verifications**: codex bypass, !quota, Russian "Краб" detection
- **CLAUDE.md** compressed -70% (72KB → 21KB)
- **0 critical regressions** в финальной сессии
