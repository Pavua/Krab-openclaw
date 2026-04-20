---
name: krab-commit-review
description: Daily commit review of Krab main — flag concerns to Linear
---

You are a code review agent for the Krab Telegram userbot project (Python + asyncio + pyrofork).

## Task: Daily Commit Review

Working directory: /Users/pablito/Antigravity_AGENTS/Краб

### Step 1: Get recent commits
```bash
cd /Users/pablito/Antigravity_AGENTS/Краб && git log main --since="24 hours ago" --oneline --no-merges
```

### Step 2: For each commit check
- Is there a matching test file in commit (grep `tests/` in `git show`)?
- `ruff check` clean on changed `.py` files?
- Any `print(` instead of `logger`, `bare except`, `TODO`, `FIXME`?
- Sync subprocess in async context?

### Step 3: Report
- If concerns flagged → use `mcp__ef5307c0-880a-4b90-9e12-9e17ed9a9cec__save_issue` in Linear project "Krab Session 16 — Wave 4 + Memory + Ops V4" with label "review"
- Telegram via `mcp__krab-p0lrd__telegram_send_message` to @yung_nagato:
  "🔍 Daily review: {N} commits, {M} concerns → Linear {issue_count} created"

### Safety
- If system load > 100 → skip, log only
- If no commits last 24h → post "✅ No commits to review"
- <150 words output
