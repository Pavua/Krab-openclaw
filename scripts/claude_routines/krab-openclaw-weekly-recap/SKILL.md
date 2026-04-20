---
name: krab-openclaw-weekly-recap
description: Weekly recap + Canva infographic generation
---

You are a session recap agent for Krab.

## Task: Weekly Recap + Canva Infographic

### Step 1: Gather week stats
```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
git log main --since="7 days ago" --oneline | wc -l   # commit count
git log main --since="7 days ago" --pretty="%s" | awk '{print $1}' | sort | uniq -c | sort -rn   # categorize
git diff --shortstat main@{7.days.ago}..main   # lines
```

### Step 2: Categorize commits
Split by prefix: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.
Top 5 most impactful (feat + fix).

### Step 3: Canva generate
```
mcp__64aef4e6-49a2-4449-88f1-94c34a86a273__generate-design
  query: "Weekly Krab recap {date}. {N} commits, {M} lines. Top features: {list}. Dark navy theme, cyan accents, crab emoji. Infographic style."
  design_type: "infographic"
```
Pick first candidate → `create-design-from-candidate`.

### Step 4: Save + post
- Save summary to `/Users/pablito/Antigravity_AGENTS/Краб/.remember/recap-{YYYY-WW}.md`
- Telegram post:
```
📊 Weekly recap {date}
Commits: {N} | Lines: +{insert}/-{delete}
Features: {top_feat_count} | Fixes: {fix_count}
🎨 {canva_url}
```

<150 words output.
