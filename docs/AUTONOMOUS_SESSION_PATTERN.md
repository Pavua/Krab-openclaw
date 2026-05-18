# Autonomous Session Pattern

Encoding learnings from S55–S65 marathon (single day, ~3.5h, 48 sonnet dispatches, 39 commits).

## 1. What is an autonomous session

An **autonomous session** is a multi-hour chain of parallel sonnet dispatches triggered by
the owner (Opus) on behalf of a user directive like «не останавливайся пока не закончишь»
("don't stop until you finish"). The user grants extended autonomy; Opus orchestrates
without checkpointing each wave for approval.

Key properties:
- Owner runs continuously, dispatching 6–10 sonnets per wave batch
- No user intervention between waves — Opus decides next steps from sonnet reports
- Wave batches are 3-deep on average (W1 → W2 follow-ups → W3 cleanup)
- All commits target `main` directly; worktrees only for true isolation needs

## 2. The 3-wave × 6-10 sonnets pattern

### Wave 1 — Main work + initial parallel sonnets
Dispatch 6–10 sonnets covering **disjoint file scopes**. Each sonnet:
- Writes code OR audits (never both)
- Commits locally, does NOT push
- Reports back ≤250 words

### Wave 2 — Depends on W1 outcomes
W2 sonnets react to W1 reports. Examples:
- W1 found root cause → W2 implements fix
- W1 added feature → W2 adds tests + observability
- W1 audit listed gaps → W2 fills the gaps

### Wave 3 — Follow-ups + cleanup
Verification, docs, coverage polish, encode learnings.

**Why disjoint file scopes are critical**: today we hit soft staging collisions
when sonnets touched same files (W1 + W2 + W4 absorbed by W6 via `git add -A`).
Explicit `git add <paths>` (not `-A`) is mandatory in briefs.

## 3. Read-only audit pattern (high-leverage)

S64 W1 + W2 found the silent-death root cause through **parallel disjoint angles**:
two audit sonnets read different files, neither modified anything, and their
reports together pinpointed the bug.

**Audit → implementation flow**:
1. W1 sonnets: read-only audits diagnose gaps from multiple angles
2. W2 sonnets: write-mode fixes implement the prescribed changes
3. W3 sonnets: tests + verification confirm the fix

**Coverage audits (S64 W7) → coverage improvements (S65 W4/W5/W6)** —
audits surface low-coverage hotspots; next-batch sonnets attack them.

Audit cost is low (~5 min/sonnet), leverage is high (saves hours of wrong-fix).

## 4. Sonnet brief template

See `docs/SONNET_BRIEF_TEMPLATE.md` for the canonical structure.

Briefs must include:
- Project path + `cd` directive
- `git add <paths>` NOT `-A` warning
- Numbered task steps with file paths
- Constraints (don't push, LOC limit, no regressions)
- Report format with word cap
- Commit message format

## 5. Anti-patterns observed today

### 5.1 `git add -A` staging collision
S64 W1/W2/W4 each ran `git add -A` near-simultaneously. W6 (last to commit)
absorbed pending hunks from W1/W2/W4. Result: misleading commit author, lost
provenance. **Fix**: always `git add <explicit paths>` in briefs.

### 5.2 Worktree branch trap
Sonnets dispatched into `claude/<branch>` worktrees committed to that branch,
not `main`. Owner had to cherry-pick. **Fix**: dispatch sonnets from `main`
unless isolation is genuinely needed; cherry-pick policy upfront.

### 5.3 Mid-restart sonnet kill
Krab restart killed in-flight sonnets. Partial work survived on disk
(uncommitted). **Fix**: avoid scheduling restarts during active waves;
if unavoidable, commit-and-push before restart.

## 6. Production verification integration

After fix lands:
1. Restart Krab (`new Stop Krab.command` → wait → `new start_krab.command`)
2. Tail logs for fix markers (e.g., `dispatcher_recovery_success`)
3. Live test via MCP (`mcp__krab-*__telegram_send_message`) or admin endpoints
4. Wakeup-based async verify — schedule a follow-up wave to confirm 10+ min later

S65 captured silent-death twice post-fix and verified `_recreate_client()`
behaved correctly — that closes the loop.

## 7. Pattern KPIs from today

| Metric | Value |
|---|---|
| Sonnets dispatched | 48 |
| Wave batches | 11 (S55–S65) |
| Commits to main | 39 |
| Sonnets/hour | ~14 |
| Commits/hour | ~11 |
| Tests added/code-write sonnet | 3–5 |
| Audit waves | ~30% |
| Implementation waves | ~70% |
| Regressions | 0 |
| Test count growth | 15665 → ~16000+ |

## 8. When NOT to use this pattern

- **Single-file simple change** — sonnet dispatch overhead exceeds task cost
- **Linear dependency chains** — each step needs previous step's exact output
  (sonnets can't share live state)
- **Architectural decisions** — need human review of trade-offs before implementation
- **Schema/migration changes** — concurrency risk too high
- **Production incident response** — owner needs full context, not delegate

## 9. Owner responsibilities during autonomous session

- Track wave state mentally (no external scratchpad needed for ≤3 waves)
- Aggregate sonnet reports → decide next wave composition
- Resolve merge conflicts (sonnets push to local branches, owner integrates)
- Push to `origin/main` at wave boundaries (not after each sonnet)
- Surface blockers to user proactively (security, billing, irreversible ops)

## 10. Closing the session

End-of-session checklist:
- All sonnet commits pushed to `origin/main`
- No dangling worktree branches uncommitted
- Memory updated (`MEMORY.md`, `next_session.md`)
- This document referenced from session notes if pattern was used

## References

- `docs/SONNET_BRIEF_TEMPLATE.md` — canonical brief structure
- `CLAUDE.md` — session highlights (S55–S65 encoded inline)
- `.remember/next_session.md` — handoff for following session
