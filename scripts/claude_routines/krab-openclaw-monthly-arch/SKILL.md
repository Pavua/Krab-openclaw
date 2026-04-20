---
name: krab-openclaw-monthly-arch
description: Monthly architecture diagram refresh via Canva
---

You are an architecture documenter for Krab.

## Task: Monthly Architecture Refresh

### Step 1: Collect metrics
```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
git log main --oneline | wc -l       # total commits
curl -s http://127.0.0.1:8080/api/endpoints | grep -oE '"path":' | wc -l  # endpoint count
ls src/handlers/*.py | wc -l         # handler count
du -sh ~/.openclaw/krab_memory/archive.db   # archive DB size
sqlite3 ~/.openclaw/krab_memory/archive.db "SELECT COUNT(*) FROM messages;"  # msg count
```

### Step 2: Update existing Canva
Use `mcp__64aef4e6-...__perform-editing-operations` on existing design:
- https://www.canva.com/d/cGi0rgCQvENmKkc (Session 15 architecture infographic)

Update text fields:
- Total commits: {N}
- API endpoints: {M}
- Archive DB: {size_MB} MB, {msg_count} msgs
- Last updated: {YYYY-MM}

### Step 3: Export + commit
```
mcp__64aef4e6-...__export-design
  design_id: DAHHbEIpLp0
  format: png
```
Save to `docs/artifacts/architecture_{YYYY-MM}.png`.

Git commit:
```bash
git add docs/artifacts/architecture_{YYYY-MM}.png
git commit -m "docs(architecture): monthly refresh {month} — {N} commits, {M} endpoints"
```

### Step 4: Telegram
```
🗓️ Monthly arch snapshot {month}
Commits: {N} | Endpoints: {M}
Archive: {size} ({msgs} msgs)
🎨 {canva_url}
```

<120 words output.
