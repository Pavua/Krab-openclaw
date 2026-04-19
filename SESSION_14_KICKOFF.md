# Session 14 Kickoff (20-21.04.2026)

**File:** Session 14 starter prompt created
**Status:** Ready for next Claude Code session
**HEAD:** 7361c2d (fix: MESSAGE_CAP_PER_WINDOW default 20→50)

## Quick Facts

- Session 13 CLOSED 19.04.2026 22:30 UTC
- 46 commits merged into main (Wave 27-29 batch)
- Major: !health deep, !memory rebuild, /api/health/deep, MMR 49× speedup, 14 Prometheus alerts
- Krab alive, OpenClaw flaky (recovers), LM Studio: 401 (normal)
- Paywall bypass: 4 team accounts added to p0lrd contacts
- how2ai @yung_nagato spam-ban expires 04:11 UTC 20.04.2026
- Integration tests: 17/19 Chado fixed, 2 remain (classify_priority signature, CAPACITY import)

## Session 14 Priorities

**See `.remember/next_session.md` or `.remember/session_14_start_prompt.md` for full details:**

### 🔴 High (do first)
1. Memory bootstrap (aged account export, ~500k+ messages)
2. how2ai spam-ban recovery (expires 04:11 UTC 20.04)
3. OpenClaw auto_restart_policy review (ExitTimeout, ThrottleInterval fixes)

### 🟡 Medium
4. Wave 29 in-progress cleanup (classify_priority, ruff pop, CAPACITY, DM reactions, FTS5 orphans)
5. LM Studio load avg optimization (73+ chronic, disable when idle)
6. Integration test flakes (2 remaining)

### 🟢 Low
7. Live benchmark 29-KK (is_owner unification)
8. Dashboard V4 frontend (delegate to Gemini 3.1 Pro)
9. !memory rebuild e2e test

## Launch

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command && sleep 4 && /Users/pablito/Antigravity_AGENTS/new\ start_krab.command
```

---

Created by Claude Code session 13 handoff.
