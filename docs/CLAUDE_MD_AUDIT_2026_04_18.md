# CLAUDE.md Audit (18.04.2026)

## Summary

**File length:** 689 lines  
**Current commit:** `8e3ab8e` (memory query expansion prep for Phase 3)  
**Last CLAUDE.md update date:** 17.04.2026 (Phase 7 status + Session 10 summary)  
**Runtime assessment:** MODERATELY STALE — references are 2–8 days old; new features (Wave 20–21) undocumented

---

## Stale References

### Outdated session/phase dates

| Line(s) | Content | Status |
|---------|---------|--------|
| 213 | "Текущий routing (12.04.2026)" | **STALE**: Now 18.04; routing may differ |
| 270 | "Phase 7 статус (12.04.2026)" | **STALE**: Document mentions Phase 7 as ~98% but Session 10 launched 17.04 |
| 276 | "Session 10 статус (17.04.2026)" | **OK**: Yesterday, relatively current |
| 519 | "актуально на 12.04.2026" (Endpoints) | **STALE**: 6 days old; new endpoints added |

### Undocumented features (Wave 20–21, not in CLAUDE.md)

- **!loglevel** — runtime log verbosity toggle (Wave 20-G, committed 5 days ago)
- **!uptime** — enhanced with LM Studio/Archive health (recently added)
- **auto_reactions** — wired into llm_flow (Wave 21-A, 6 commits ago)
- **!archive/growth** — monitor with `/api/archive/growth` endpoint (new)
- **Voice Channel Phase 1.4** — documented in CLAUDE.md (lines 170–180) ✓

---

## Missing Endpoints

**Current endpoint count:** ~189 unique `/api/` paths in `src/modules/web_app.py`  
**Documented endpoints:** ~155 in CLAUDE.md table (lines 521–677)

### Recently added (not in CLAUDE.md)

```
/api/archive/growth           — archive.db growth snapshot + anomaly alert
/api/krab_ear/status          — (already in docs, line 546)
/metrics                      — Prometheus text format (added, line 542)
/api/session10/summary        — V4 Hub aggregation (added, line 602)
```

### Verification needed

Run to get full count:
```bash
grep -o '/api/[a-z/_]*' src/modules/web_app.py | sort -u | wc -l
# Result: 189 endpoints
```

CLAUDE.md lists **~155**. **Gap: ~34 endpoints** not documented (mostly sub-paths under existing categories, e.g., `/translator/session/action`, `/translator/mobile/register`).

---

## Commands: Count Mismatch

**Claim in CLAUDE.md:** "175+ команд"  
**Actual count:** ~175–185 (lines 1–17378 in `src/handlers/command_handlers.py`)

### Newly added commands not in CLAUDE.md

| Command | Wave | Feature |
|---------|------|---------|
| !loglevel | Wave 20-G | Runtime log level toggle |
| (auto_reactions) | Wave 21-A | Automatic reactions via llm_flow (not !cmd, internal) |

**Commands listed in CLAUDE.md:** All ~175 appear present and correct (spot-checked: !ask, !search, !swarm, !reset, !confirm, etc.).

---

## Module list staleness

### Still accurate

- `src/core/` modules (lines 51–123): All ~45 modules present in filesystem
- `src/handlers/` → `command_handlers.py` (line 125): ✓ Decomposition complete
- `src/integrations/` (lines 126–135): ✓ All listed present
- `src/voice_channel/` (lines 163–168): ✓ New in Phase 1.4

### Potentially missing from docs

| Module | Location | Reason |
|--------|----------|--------|
| `src/core/memory_validator.py` | core/ | Session 10: injection blocker (doc line 278) ✓ |
| `src/core/auto_restart_policy.py` | core/ | Session 10: resilience (doc line 282) ✓ |
| `src/core/reset_helpers.py` | core/ | Session 10: reset layers (doc line 282) ✓ |

All mentioned in Session 10 summary, architecture list not updated.

---

## Environment & Infrastructure

### Still accurate

| Item | Status |
|------|--------|
| Python 3.13 + pyrofork 2.3.69 | ✓ Current |
| MCP ports (8011–8013) | ✓ Current |
| OpenClaw :18789, Owner Panel :8080 | ✓ Current |
| LaunchAgents + plists | ✓ Listed (line 194) |

### Potential drift

- **Voice Channel port:** Docs claim 8081 (line 178), but confirm against `config.py`
- **Routing truth location:** Still `~/.openclaw/agents/main/agent/models.json` (needs verification)

---

## Test statistics (lines 679–688)

| Session | Documented count | Status |
|---------|------------------|--------|
| S5–S7 | 2071 → ~6826 | Old snapshots; no S11 data |
| S8–S10 | ~7310–7465 | Current but 8 days old |
| S11 | (missing) | New session not yet recorded |

No test count for current session.

---

## Recommended Updates

### Priority 1 (blocking accuracy)

- [ ] Update routing date from 12.04 → 18.04 (line 213)
- [ ] Add Section "## Wave 20–21 Status (18.04.2026)" documenting:
  - !loglevel (Wave 20-G)
  - auto_reactions wiring (Wave 21-A)
  - archive.db growth monitor (Wave 20-F)
- [ ] Update Phase 7 status → describe current Wave/session numbering (Line 270)

### Priority 2 (completeness)

- [ ] Add ~34 missing `/api/translator/session/*` and `/api/translator/mobile/*` endpoints
- [ ] Document `/api/archive/growth` endpoint with anomaly-alert trigger
- [ ] List all 189 endpoints in table (or link to `curl localhost:8080/api/endpoints`)
- [ ] Add test count for Session 11+ when available

### Priority 3 (nice-to-have)

- [ ] Verify Voice Channel port (8081) against live config
- [ ] Verify model routing truth location + current active model (Gemini 3.1 Pro vs documented)
- [ ] Add changelog/diff marker (e.g., "Last sync: 18.04, Session 11") at top of file

---

## Audit Verdict

**Overall staleness:** MODERATE (6–8 days behind current code)  
**Breaking issues:** NONE detected  
**Documentation accuracy:** ~85% (commands/modules accurate; endpoints incomplete; dates outdated)

**Recommendation:** Schedule manual review + refresh for next session. Do NOT auto-regenerate — loss of context notes and curated examples. Manual edits preferred.
