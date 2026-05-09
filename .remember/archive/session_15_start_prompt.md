# Стартовый промпт для Session 15

Скопируй в новый чат Claude Code после reboot:

---

Проект: **Krab** — персональный Telegram userbot на pyrofork + MTProto с OpenClaw Gateway, Dashboard :8080, Memory Layer (SQLite FTS5 + Model2Vec + sqlite-vec), мультиагентный swarm, MCP серверы.

Path: `/Users/pablito/Antigravity_AGENTS/Краб`
Ты работаешь на @yung_nagato (Krab); owner = @p0lrd (user_id 312322764).
MCP доступны: `krab-yung-nagato` (bot) + `krab-p0lrd` (owner) для e2e.

## Session 14 CLOSED (20.04.2026, reboot for memory pressure)

- **69 commits в main** (Wave 27 → 29-ZZ)
- **HEAD:** `26c1f1a` (docs: Google diagnostic finding)
- Reboot reason: memory 32/36 GB, Docker + LM Studio crashed, 20+ orphan `openclaw` procs
- После reboot — зомби-процессы мертвы, load avg должен быть норма

## Прочитай ПЕРВЫМ ДЕЛОМ

1. `.remember/session_14_reboot_checkpoint.md` — что было перед reboot, какие waves merged, что in-flight
2. `.remember/next_session.md` — Session 15 priorities
3. `CLAUDE.md` — project rules (204 endpoints, 154 handlers, Russian ответы)

## Session 14 достижения (highlights)

**Root-cause fixes (не симптомы):**
- 29-A/G/M: `_safe_reply` sweep 18 handlers (method never existed)
- 29-GG: 39 missing commands в `USERBOT_KNOWN_COMMANDS` frozenset
- 29-KK: unified `is_owner_user_id()` (ACL json + env)
- 29-XX: CLI provider photo redirect → Gemini vision fallback
- 29-Y: `OPENCLAW_HEALTH_WAIT_TIMEOUT_SEC=90` — устранил SIGTERM loops
- 29-TT+YY: chat_ban auto-expire + periodic sweep
- Paywall: 4 swarm team accounts добавлены в @p0lrd contacts

**Features:**
- `!health deep` (8 sections), `!memory rebuild`, `!archive stats/growth`, `!swarm status deep`
- `/api/health/deep` REST mirror
- LM Studio idle watcher (29-RR, **не wired**, S15)
- OpenClaw watchdog + owner alerts (29-WW)
- 14 Prometheus alerts

**Perf:** MMR 261ms → 5.3ms (49×) через token-set pre-compute + cap=5

**Diagnosed (verify/act):**
- how2ai: Telegram spam-ban для @yung_nagato, expires **04:11 UTC 20.04** (должно уже пройти)
- Google/Gemini: **FALSE ALARM "недоступно"** — API рабочий, probe hang в event loop
- LM Studio idle = root CPU starvation (load avg 73+)

## Session 15 priorities

### 🔴 High — root fixes
1. **Wire `lm_studio_idle_watcher`** в bootstrap (29-RR готов, не подключен)
2. **Metrics emission fix** — 3 missing (`krab_archive_chunks_embedded_total`, `krab_llm_route_latency_seconds`, `krab_auto_restart_attempts_total`)
3. **Dead alerts cleanup** — 6 alerts ссылаются на non-emitted metrics (`krab_memory_query_duration_seconds_bucket`, `krab_message_batcher_queue_depth`, `krab_llm_errors_total`, `krab_command_errors_total`, `krab_telegram_flood_wait_total`, `krab_archive_chunks_embedded_total`)
4. **how2ai cleanup** — `!chatban unban -1001587432709` или `jq 'del(."-1001587432709")' ~/.openclaw/krab_runtime_state/chat_ban_cache.json`
5. **probe_gemini_key timeout** — `asyncio.wait_for(..., 15)` wrapper + 60s TTL cache (fixes "Google ⚠️ недоступно" false alarm)

### 🟡 Medium
6. Complete 29-UU native cron (files в main, нужны tests + scheduler wire + handle_cron fallback)
7. Plist tweaks (optional): `ExitTimeout=120` Krab, `ThrottleInterval=5` OpenClaw
8. **Bootstrap p0lrd Memory Phase 2**: `venv/bin/python scripts/bootstrap_memory.py --export "/Users/pablito/Downloads/Telegram Desktop/DataExport_2026-04-XX/result.json"` (если export complete)
9. `!memory rebuild` — sqlite-vec FTS5/vec_chunks orphans cleanup

### 🟢 Low
10. OWNER_USER_IDS deprecation в пользу unified ACL (long-term)
11. Dashboard V4 frontend delegate Gemini 3.1 Pro
12. Pre-commit hook: ruff + mypy --strict на src/handlers/

## Launch check после reboot

```bash
# 1. Verify Krab живой
curl -s http://127.0.0.1:8080/api/uptime
curl -s http://127.0.0.1:18789/healthz

# 2. Activity Monitor: openclaw procs должно быть 1 (не 20+)
pgrep -fc openclaw

# 3. Git state
cd /Users/pablito/Antigravity_AGENTS/Краб
git log main --oneline -5  # HEAD должен быть 26c1f1a или новее
git status --short  # чисто

# 4. Tests smoke
PATH=/opt/homebrew/bin:$PATH venv/bin/python -m pytest tests/unit -q --no-header -p no:cacheprovider -x 2>&1 | tail -5

# 5. LM Studio — unload model если не используется
```

## Carry-forward правила

- Russian communication
- Sonnet/Haiku agents default, Opus high для архитектурных
- **fix root cause, not symptoms** (feedback_root_cause.md)
- Max parallel agents OK, НО следи за memory (лимит ~6-8 одновременно)
- Не SIGHUP openclaw — только `openclaw gateway` или `launchctl kickstart -k gui/501/ai.openclaw.gateway`
- `new start_krab.command` / `new Stop Krab.command`, wait full stop перед start

## Session-15 start command

Скажи что-то вроде: "продолжаем Session 15 после reboot. Прочитай .remember/session_14_reboot_checkpoint.md и session_15_start_prompt.md, verify Krab alive, дальше приоритеты — lm_studio_idle wire, metrics fixes, probe timeout."

Удачи! 🦀
