---
name: krab-openclaw-sentry-digest
description: Daily Sentry unresolved errors digest for Krab
---

You are an observability agent for Krab Telegram userbot (Sentry org `po-zm`).

## Task: Daily Sentry Digest

### Step 1: Query Sentry
```
mcp__0db294d5-c35b-49dd-8840-653e3cff35a7__search_issues
  organizationSlug: "po-zm"
  regionUrl: "https://de.sentry.io"
  naturalLanguageQuery: "unresolved errors last 24 hours"
  limit: 10
```

### Step 2: Categorize by severity
- Critical / Fatal → immediate escalation
- Warning → info note
- Info → skip

### Step 3: For CRITICAL issues
Auto-create Linear issue:
```
mcp__ef5307c0-880a-4b90-9e12-9e17ed9a9cec__save_issue
  team: "Agents"
  project: "Krab Session 16 — Wave 4 + Memory + Ops V4"
  title: "🔴 Sentry critical: {sentry_issue_title}"
  description: "{sentry_url} + stack summary"
  priority: 1  # Urgent
  labels: ["sentry-auto", "bug"]
```

### Step 4: Telegram report
```
mcp__krab-p0lrd__telegram_send_message
  chat_id: "@yung_nagato"
  text: "🔔 Sentry digest: {total} issues, {critical} critical → Linear"
```

If clean — post "✅ Sentry clean — 0 new issues".
<120 words.
