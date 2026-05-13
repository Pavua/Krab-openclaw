# Session 49 — Starter Handoff (Session 48 in progress, 2026-05-13 ~02:30)

## TL;DR — Session 48 СУПЕРПРОДУКТИВНАЯ: **35+ commits**, **14 admin pages live**, **~300+ tests added**, 3 macOS reboot survived

**main HEAD**: `cf742ed` (Wave 186-fix-2 _ai_card schema fix)

## 🎯 Wave timeline (Session 48)

| Wave | Commit | Effect |
|---|---|---|
| 163 | `3fe3ef1` | `/api/network/probes` восстановлен (исчез после S47 refactor) — split_brain + dispatcher_tick + pyrogram disconnects |
| 164/165 | `ac1e38f` | `/admin/sentry` + `/admin/cron` pages |
| 166 | `8f5647b`+`d788b70`+`ee46f75`+... | Father Reminder + Risk Guard + .env.bak gitignore + Wave 138/141 followups |
| 167 | (Sentry resolve) | 6 issues resolved (Wave 142/143 fix verified, paid guard log spam reduced) |
| 168 | `9055905` | docs autotables refresh — 309 endpoints, 181 handlers |
| 169 | (bundled in 170) | `/admin/logs` page — structlog tail + level filter + grep + download |
| 170 | `fa56639` | PaidGeminiGuard logger.error → warning (Wave 41-O pattern) |
| 171 | (filesystem) | **8 GB freed** — old archive.db backups + workspace tarballs + dated dirs |
| 172 | `c695034` | backup retention sweep — auto-prune krab_memory/backups/workspace |
| 173 | `aedb14b` | typing indicator while LLM generates — async context manager |
| 174 | `eeafd4e`+`2555116`+`5168b84` | Sentry hygiene: 4 noise events downgraded |
| 176 | `f3ff396` | `/admin/db` — SQLite stats + integrity + WAL checkpoint |
| 177 | `d9ebba5` | Prometheus metrics for typing indicator + alert |
| 178 | (pending live retry) | Wave 90 retry batched prune — interrupted by reboots |
| 179 | `d1911b7` | `/admin/network` — MTProto session + ping + DNS diagnostics |
| 180 | `ea07549` | krab_ear IPC probe — backoff + not-installed + env gate. **KE now healthy** |
| 181 | `7c7574a` | TTS pipeline wrapped в recording_voice indicator |
| 182 | `db7eb23` | MLX Local KV4 provider в /admin/models picker |
| 183 | `1cd9b9d` | `/admin/voice` — TTS/STT/Gateway/Ear + restart actions |
| 184 | `4df00d6` | `/admin/memory` — RAG stats + search interface + retrieval metrics |
| 186 | `38d21c0` | `/admin/health` unified dashboard (scatter-gather 8 endpoints) |
| **186-fix** | `fe635bc` | **ASGITransport для in-process self-calls** — обход uvicorn single-worker deadlock |
| 186-fix-2 | `cf742ed` | `_ai_card` schema fix (chain.active_ai_channel) — все 7 cards GREEN |

## 🌐 14 admin pages LIVE на http://127.0.0.1:8080

| Page | Wave | Status |
|---|---|---|
| `/admin/models` | 144+182 | ✅ + MLX KV4 |
| `/admin/swarm` | 152 | ✅ |
| `/admin/costs` | 155 | ✅ |
| `/admin/ecosystem` | 156 | ✅ |
| `/admin/inbox` | 157 | ✅ |
| `/admin/routing` | 146+160 | ✅ |
| `/admin/cron` | 165 | ✅ 56 launchd agents |
| `/admin/sentry` | 164 | ✅ |
| `/admin/logs` | 169 | ✅ structlog tail |
| `/admin/db` | 176 | ✅ 12 DBs + integrity |
| `/admin/network` | 179 | ✅ ping + DNS |
| `/admin/voice` | 183 | ✅ TTS+STT+Gateway+Ear |
| `/admin/memory` | 184 | ✅ RAG search + stats |
| `/admin/health` | 186 | ✅ unified GREEN |

## 🔑 Архитектурные паттерны Session 48

### ASGITransport для server-internal calls (Wave 186-fix)
Когда FastAPI handler делает HTTP self-calls на свой сервер через httpx — это deadlock'ит uvicorn single-worker. Fix:
```python
transport = httpx.ASGITransport(app=ctx.app)
async with httpx.AsyncClient(transport=transport, base_url="http://owner-panel.local") as c:
    r = await c.get("/api/health")  # in-process, no socket
```
RouterContext теперь содержит `app: FastAPI` для этого паттерна.

### Wave 173 typing indicator (async context manager)
```python
async with recording_voice(bot.client, chat_id):
    await tts_pipeline(text)  # Telegram shows "Krab is recording..."
```
Loop re-sends ChatAction.RECORD_AUDIO каждые 4s, cancels on exit.

### Worktree-main divergence pattern
Subagents работают в worktree branch. Чтобы перенести их commits на main:
```bash
git format-patch -1 <sha> --stdout -- <files> > /tmp/patch
git apply --3way /tmp/patch
```
Это избегает 33-file cherry-pick conflicts из drift между branches.

## ⚠️ Pending для Session 49

### High priority
- **Wave 178 prune live retry** — 173 MB savings от 193K orphan messages. Запускать **в foreground**, monitor, без parallel sonnet agents во время prune.
- **typing indicator group chats verify** — Wave 173 wired для DM, проверить группы
- **autotables refresh** — endpoints > 309 после 14 admin pages

### Medium priority
- Wave 86 pressure-aware select — enable in production
- Wave 63-D dispatcher recovery — flip enabled after 1-2 weeks
- AGE-15 archive.db corruption monitor

### Low priority
- `/admin/help` index page — описание всех 14 admin pages
- Backup retention 4-я категория (`krab_memory/backups/`)
- Wave 173 typing indicator для image generation (UPLOAD_PHOTO)

## 💡 Lessons learned Session 48

1. **Memory pressure от parallel Sonnet agents** — 4+ одновременно вызывают macOS OOM. Limit: **2 max foreground**, background prune separate.
2. **Recovery from reboots clean** — launchd auto-restart + commit-after-each-wave persistence спасли 35 commits через 3 reboots.
3. **ASGITransport** — production pattern для FastAPI dashboards с self-calls. Никогда `httpx.get('http://127.0.0.1:port/...')` из handler того же сервера.
4. **Worktree-main divergence** требует `format-patch -- <files>` selective apply, не cherry-pick.

## Quick commands

```bash
# Health dashboard
open http://127.0.0.1:8080/admin/health

# All probes
curl -sS http://127.0.0.1:8080/api/network/probes | jq

# Krab restart
launchctl kickstart -k gui/$UID/ai.krab.core

# Wave 178 prune retry (FOREGROUND only!)
KRAB_MEMORY_PRUNE_APPLY=1 venv/bin/python scripts/krab_memory_prune_orphans.py --commit-each-chat 2>&1 | tee /tmp/prune.log

# Sentry quota check
venv/bin/python scripts/krab_sentry_quota_check.py
```

## Session 48 stats (updated post-Wave 192)
- Commits: **43+** (across 3 macOS reboots)
- Admin pages: 6 → **17** (added: cron/sentry/logs/db/network/voice/memory/health/help/env/commands)
- Tests added: **~400+**
- Endpoints: 309 → **349** (+40, 13% growth)
- Metrics: 52 → 53, Alerts: 42 → 43
- Sentry issues resolved: 10+
- Disk freed: 9.5 GB
- Major bug fixes: 2 — Wave 186 ASGITransport deadlock + Wave 180 KrabEar IPC probe
- Worktree-main reconciliations: 3 (Waves 163, 164/165, 169)

## Wave timeline addendum (190+)
| Wave | Commit | Effect |
|---|---|---|
| 187 | `06f47ba` | `/admin/help` index page — 15-я page |
| 188 | (background) | Wave 178 retry — live prune ~30min, ~173 MB savings expected |
| 189 | `286d672` | `/admin/env` — 39 vars, secret masking, 16-я page |
| 190 | `68ece90` | `/admin/commands` — 162 commands в 14 категориях, 17-я page |
| 191 | `a37ed6a` | backup retention 4-я категория — openclaw config backups (437 .bak files без retention) |
| 192 | `2e0fb43` | typing indicator group verify ✓ + NLU compound/anaphora patterns (`_KRAB_NAME_RE` DRY) |
| autotables | `258fad6` | docs/CLAUDE_AUTO_*.md refreshed — 349 endpoints, 181 handlers |
