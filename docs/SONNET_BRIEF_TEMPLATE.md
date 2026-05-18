# Sonnet Brief Template

Encoding lessons from S55–S63 (24+ sonnet dispatches, ~2.5h, 26 commits).

## Context

Proven pattern through S55–S63: short, structured briefs to Sonnet workers achieve
high success rate when scope is narrow, paths are explicit, and constraints are
clear. Owner (Opus) aggregates; workers don't push.

## Standard Brief Structure

```
## Title (concise, identifies wave + task)

Project: /Users/pablito/Antigravity_AGENTS/Краб (main repo).

**IMPORTANT**: `cd /Users/pablito/Antigravity_AGENTS/Краб` first!
Use `git add <specific paths>` NOT `-A`.

## Context
[Why this matters, references prior waves]

## Task
[Numbered steps, specific files, exact line ranges if known]

## Constraints
[Don't push, don't break X, keep diff ≤N LOC, etc.]

## Tests
[Specific test files, count target, targeted not full-suite]

## Commit
Exact commit message format: `feat(<scope>): <description> (Sn Wn)`. DO NOT push.

Report ≤N words: [structured asks].
```

## Critical Rules

- **Working directory**: `cd /Users/pablito/Antigravity_AGENTS/Краб` — main repo,
  NOT a worktree. Workers reset cwd between bash calls; always use absolute paths.
- **Staging**: `git add <specific paths>`, never `-A` (sweeps unrelated work from
  concurrent sonnets — observed collision in S58).
- **Verify commit**: `git log --oneline -1` after commit. If commit landed on wrong
  branch, cherry-pick to main (don't rebase).
- **No push**: owner aggregates and pushes batches. Workers commit local only.
- **Concurrency**: if two sonnets touch same paths, serialize them or split scope.

## Anti-Patterns

- `git add -A` — sweeps unrelated diffs (e.g. `.gitignore`, `venv` deletion).
- Pushing from worker session — breaks aggregation, may force-push race.
- Overlapping scope with another sonnet in same wave (file collision).
- Running full 15k pytest suite — slow (~3min). Use targeted module/file subset.
- Vague instructions ("clean up X") — workers need exact files + line ranges.
- Asking worker to decide architecture — owner decides, worker implements.

## Scope Mapping (walltime targets)

| Scope        | Files touched         | Tests          | Walltime |
|--------------|----------------------|----------------|----------|
| Code change  | 1 src/ + 1 tests/    | targeted ≤30   | ≤5 min   |
| Audit (R/O)  | read-only            | none           | ≤3 min   |
| Docs         | 1 .md                | none           | ≤2 min   |
| Refactor     | 1 module + tests     | module subset  | ≤8 min   |

## Common Patterns

- **Mirror**: copy structure from existing similar feature (e.g. new admin page
  mirrors existing one — same factory shape, same test layout).
- **Audit + fix**: investigate read-only first, then a second sonnet applies the
  fix once owner reviews findings. Don't combine in one brief.
- **Defense-in-depth**: add safety net (extra check, benign marker, fallback)
  WITHOUT breaking existing flow. Frame as "additive, no behavior change unless X".

## When to NOT use a sonnet

- Architecture decisions (owner uses Opus).
- Cross-cutting refactors >2 modules (split into sequential waves).
- Tasks requiring conversation context the worker doesn't have.
- Anything requiring production debugging with live logs.
