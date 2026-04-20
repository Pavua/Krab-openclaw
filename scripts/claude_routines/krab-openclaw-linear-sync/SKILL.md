---
name: krab-openclaw-linear-sync
description: Daily Linear status sync for Krab project
---

You are a project-tracking agent for Krab development.

## Task: Daily Linear Status Sync

### Step 1: Fetch active + todo issues
```
mcp__ef5307c0-880a-4b90-9e12-9e17ed9a9cec__list_issues
  project: "Krab Session 16 — Wave 4 + Memory + Ops V4"
  state: "In Progress"
```
Same for state="Todo" with assignee="me".

### Step 2: Detect stale
Issues not updated in > 3 days (use `updatedAt` field) mark as stale.

### Step 3: Compose digest
```
📋 Krab Linear status
✅ Done today: {N}
🔄 In Progress: {M}
⏳ Todo: {K}
💤 Stale (>3d): {S}

Top 3 Todo by priority:
- AGE-XX: {title}
- ...

{if stale > 0}:
Stale needs attention:
- AGE-XX ({days_old}d): {title}
```

### Step 4: Post
```
mcp__krab-p0lrd__telegram_send_message
  chat_id: "@yung_nagato"
  text: {digest}
```

<120 words total output.
