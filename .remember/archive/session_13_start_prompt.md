# Session 13 Start Prompt — Krab (18.04.2026)

**Copy-paste into chat to begin Session 13:**

```
Session 13 Krab — Wave 18 (architecture verification + ecosystem health fix)

Handoff from Session 12: 20+ commits merged, Chado modules fully wired into _process_message, Memory Phase 2 live (9131+ chunks), 10+ new commands, 12+ endpoints. Archive.db 43k+ messages. codex-cli primary, p0lrd ready for Telegram export.

Priority stack:

🔴 HIGH:
1. **Wave 18-A: Diagnose session_12 block empty bug** in `/api/ecosystem/health`. Why does it return {}? Trace: _build_session_12_block() → message_priority_dispatcher stats → chat_window_manager LRU → message_batcher backpressure. May indicate Chado modules not wired correctly into Wave 17-A integration.

2. **Wave 18-B: Chado Q4+Q5 interview** (follow up from Session 12). Spec: plugin architecture layers, distinguish "production" vs "experimental" skills. Budget 2-3h.

3. **Wave 18-C prep: p0lrd Telegram export** — when ready (>48h ETA), will bootstrap ~500k messages. MCP p0lrd@8012 ready.

4. **Dashboard V4 frontend** — delegate full spec to Gemini 3.1 Pro (spec exists from Session 11). Builder tools in place.

🟡 MEDIUM:
5. **Wave 17 Chado modules live verify** — do ChatWindow+Filter+Priority+Batcher+Reflector work end-to-end in real messages?

6. **Ruff cleanup** (src/ and tests/ ~580 errors outside src/)

7. **Memory Phase 3 prep** — query expansion, re-ranking improvements

8. **Disk hygiene** — archive.db 43MB, alerts at 500MB

Known issues:
- 99% disk (user cleaning)
- session_12 block empty (Wave 18-A)
- Some locked worktrees (cleanup ongoing)

Infra status (18.04):
- Krab PID 3515, archive.db 43k+, Dedicated Chrome :9222
- MCP yung-nagato@8011 (bootstrap done), p0lrd@8012 (ready), Hammerspoon@8013
- OpenClaw 18789 active
- All Wave 17 tests passing (Chado integration)

Start: Wave 18-A diagnostic → confirm Chado modules are live in production message flow.
```

---

## Context clues for diagnosing Wave 18-A

1. **_build_session_12_block()** in `web_app.py` calls:
   - `message_priority_dispatcher.get_stats()` → should return P0/P1/P2 counts
   - `chat_window_manager.get_stats()` → should return per-chat window count + LRU size
   - `message_batcher.get_backpressure_status()` → should return queue depth + overflow count

2. **If any dict is empty**, check:
   - Is module imported/initialized in `__init__.py`?
   - Are hooks wired into `_process_message()` (Wave 17-A)?
   - Is stats accumulation happening? (counters should increment with each message)

3. **Test verification:**
   ```bash
   pytest tests/core/test_chado_integration.py -q -v
   pytest tests/integration/test_wave_17_e2e.py -q -v
   ```

4. **Manual check:**
   ```
   !stats ecosystem
   # Should show non-empty session_12 block with chat_windows, batcher, filter stats
   ```

---

## Rules for Session 13

- Russian always
- Sonnet/Haiku default
- `parse_mode=markdown`
- НЕ SIGHUP openclaw
- Max parallel agents for Wave 18
- NO destructive MCP without `!confirm`

Ready to dive in!
