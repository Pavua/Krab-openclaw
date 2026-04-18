# Experimental Skills Test Chat Workflow

> Based on Chado (How2AI bot) architecture — production-proven approach at 20+ plugins scale.

## Philosophy

Runtime feature flags **НЕ НУЖНЫ** пока plugins < 20 (current Krab ~10-15). Вместо runtime machinery:

1. **git branch** — experimental skill живёт в отдельной ветке
2. **Test chat** — один dedicated Telegram chat где Krab reacts на experimental
3. **Per-chat filter config** — `chat_filters.json` + `!listen` command
4. **Merge only when stable** — после обкатки в тест-чате, PR → main

## Setup

### 1. Create experimental test chat в Telegram

**Recommended:** private супергруппа `🧪 Krab Lab` с только owner (p0lrd) + Krab userbot (yung_nagato).

```bash
# Note chat_id после создания (через telegram_get_dialogs MCP tool):
mcp__krab-p0lrd__telegram_get_dialogs()
# Find "Krab Lab" → copy chat_id
```

### 2. Set chat mode = `active`

В тест-чате:
```
!mode active
```

Krab будет реагировать на **все** сообщения (не только mentions) — удобно для debugging.

### 3. Set production chats to `mention-only`

Во всех public groups:
```
!mode mention-only
```

Experimental behavior не leak'ит в production chats.

## Workflow

### A. Develop skill в git branch

```bash
git checkout -b experiment/new-skill
# Develop, test locally
pytest tests/unit/test_new_skill.py -v
```

### B. Deploy в lab

Restart Krab с experimental branch:
```bash
# Either push → rebase Krab's branch
# OR direct swap в main worktree для quick test
```

### C. Observe в 🧪 Krab Lab

Send test messages, observe responses. Iterate.

### D. Promote или reject

**Production-ready?** → PR to main, merge.  
**Needs more work?** → iterate в branch.  
**Reject?** → mark `disabled: true` в plugin.json или delete branch.

## Gating mechanisms

| Mechanism | Use when |
|-----------|----------|
| `disabled: true` в plugin.json | Archival / deprecated skill |
| Git branch | Active development |
| `!mode muted` per-chat | Per-chat skill rollout |
| Env flag (rare) | Infrastructure-level toggle (e.g. `MEMORY_AUTO_CONTEXT_ENABLED`) |

## Monitoring experimental impact

- **`/api/ecosystem/health`** — overall effect on response times, memory growth
- **`/api/chat_windows/stats`** — per-chat activity
- **`!stats ecosystem`** — quick health check в Telegram

## Graduation checklist для merge → main

- [ ] 24h+ active use в test chat без errors
- [ ] 5+ diverse conversation scenarios tested
- [ ] Unit tests для edge cases
- [ ] Integration test чтобы не ломать existing flow
- [ ] Docs обновлены (CLAUDE.md, CHANGELOG.md)
- [ ] Memory validator passes (no injection via new command)
- [ ] Disk impact acceptable (archive.db growth, logs)

## Roll-back plan

Если experimental skill ломает production после merge:
1. `git revert <commit>` — мгновенный откат
2. `!reset --layer=openclaw` — очистить in-memory session state
3. Restart Krab via `new start_krab.command`

## Когда добавить runtime feature flags

При **20+ активных plugins** или когда:
- Многие skills имеют overlap (один mute не достаточен)
- A/B testing compare metrics needed
- Multiple prod environments (dev/staging/prod)

До того — git discipline + test chat = enough.

---

**Reference:** Chado (How2AI) architecture interview Q5, 18.04.2026 — `.remember/chado_architecture_learnings.md`.
