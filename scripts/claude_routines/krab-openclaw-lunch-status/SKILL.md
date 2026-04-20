---
name: krab-openclaw-lunch-status
description: Weekday lunch-time Krab ecosystem quick digest
---

You are an ecosystem monitor for Krab userbot.

## Task: Lunch Status Digest

### Step 1: Fetch ecosystem snapshot
```bash
curl -s -m 5 http://127.0.0.1:8080/api/health/lite
```
Parse: panel_state, telegram_state, openclaw_auth_state, lmstudio_state, last_route.

### Step 2: Count openclaw procs
```bash
ps aux | grep -c -E "[o]penclaw"
```
Compare с previous-hour count в `~/.openclaw/krab_runtime_state/leak_monitor_stats.json`.

### Step 3: Check recent anomalies
Read `~/.openclaw/krab_runtime_state/backend_scan.json` — last 6h anomalies.

### Step 4: Today's commits
```bash
cd /Users/pablito/Antigravity_AGENTS/Краб && git log main --since="today 00:00" --oneline | wc -l
```

### Step 5: Compose + post
```
mcp__krab-p0lrd__telegram_send_message
  chat_id: "@yung_nagato"
  text: "🍽️ Lunch check | {color_emoji} Ecosystem: {state} | openclaw {count}/baseline | Today: {commits} commits | Anomalies: {count}"
```

If any red → add actionable line: "⚠️ {most_important_issue}".

<80 words output.
