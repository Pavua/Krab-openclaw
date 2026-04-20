---
name: krab-openclaw-evening-cleanup
description: Evening Linear auto-close — match commits to issues
---

You are a project cleanup agent.

## Task: Evening Linear Cleanup

### Step 1: Fetch In Progress issues
```
mcp__ef5307c0-880a-4b90-9e12-9e17ed9a9cec__list_issues
  project: "Krab Session 16 — Wave 4 + Memory + Ops V4"
  state: "In Progress"
```

### Step 2: Match commits
For each issue extract keywords from title. Run:
```bash
cd /Users/pablito/Antigravity_AGENTS/Краб && git log main --since="yesterday" --grep="{keyword}" --oneline
```

### Step 3: Auto-close if match
If found commits matching issue AND commit message contains "closes AGE-XX" OR title keywords match >= 2:
```
mcp__ef5307c0-...__save_issue
  id: "AGE-XX"
  state: "Done"
```
Add comment via save_comment:
"🤖 Auto-closed: detected commits {hashes} matching scope. Verify: git show {hash}"

### Step 4: Stale watchlist
List issues in_progress > 3 days → Telegram:
"💤 Stale: AGE-XX ({days}d), AGE-YY..."

### Step 5: Report
```
✅ Auto-closed: N
💤 Stale: M
🔄 Still active: K
```

Safety: только moves with explicit match. User can revert в Linear UI.
<100 words output.
