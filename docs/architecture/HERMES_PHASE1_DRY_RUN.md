# Hermes Phase 1 — Dry-Run Findings

**Date**: 2026-05-02
**Wave**: 13-Phase-1
**Hermes version**: v0.12.0 (2026.4.30)
**Repo**: `/Users/pablito/Antigravity_AGENTS/hermes-agent-eval/` (NousResearch/hermes-agent)

## Install

- Method: `uv venv .venv` + `uv pip install -e .`
- Python: 3.14.4 (homebrew `python@3.14`)
- Core install only — skipped `[all]` extras (Modal, Daytona, voice, messaging)
- Install path: `/Users/pablito/Antigravity_AGENTS/hermes-agent-eval/.venv/bin/hermes`
- Status: **success** — `hermes --version` → `Hermes Agent v0.12.0`

## Migrate command syntax

```
hermes claw migrate [--source SOURCE] [--dry-run]
                    [--preset {user-data,full}] [--overwrite]
                    [--migrate-secrets] [--no-backup]
                    [--workspace-target PATH]
                    [--skill-conflict {skip,overwrite,rename}] [--yes]
```

Defaults: `--source ~/.openclaw`, target `~/.hermes`, preset `full`, secrets excluded, pre-migration zip backup to `~/.hermes/backups/`.

Dry-run command actually executed:
```
.venv/bin/hermes claw migrate --dry-run --yes
```
(env: `OPENAI_API_KEY=dummy ANTHROPIC_API_KEY=dummy` — not called during dry-run, only loaded by config layer.)

Full log: `/tmp/hermes_migrate_dry_run.log`.

## Dry-run summary — what would migrate (33 items)

**Memory & persona (3)**
- `memory` → `~/.hermes/memories/MEMORY.md`
- `user-profile` → `~/.hermes/memories/USER.md`
- `daily-memory` → `~/.hermes/memories/MEMORY.md` (merged)

**Messaging settings (5)** — Telegram / Discord / Slack / WhatsApp / Signal allowed-users + CWD merged into `~/.hermes/.env` (no tokens — secrets excluded by default).

**Config (3)**
- `model-config` → `~/.hermes/config.yaml`
- `command-allowlist` → `~/.hermes/config.yaml`
- `agent-config` → `config.yaml agent/compression/terminal`

**Custom providers (4)** → `config.yaml custom_providers[...]`:
- `google-antigravity`, `lmstudio`, `qwen-portal`, `codex`

**Skills (17)** → `~/.hermes/skills/openclaw-imports/`:
- actual-budget, apple-mail, bluebubbles, calendar, coding-agent-1gx, github, lm-studio, mercadona, openai-whisper, project-context-sync, project-manager, shopping-expert, skill-creator, todo, tubescribe, video-frames, weather
- + skill-category `DESCRIPTION.md`

**Conflict (1)**: `soul` (target `~/.hermes/SOUL.md` already exists — `--overwrite` would resolve)

## Skipped (15) — gaps

- `workspace-agents` — needs `--workspace-target PATH`
- `secret-settings` / `provider-keys` — require `--migrate-secrets`
- `tts-config`, `tts-assets` — none found in OpenClaw
- `shared-skills` — no shared dirs
- `raw-config-skip` x2 — extracted compatible values only, raw config & credentials NOT copied (intentional)
- `sensitive-skip` x4 — secrets / binary state / product-specific runtime data NOT migrated
- `mcp-servers` — **NONE migrated** (OpenClaw config has no MCP entries readable by tool)
- `memory-backend` — no memory backend config (likely Krab's archive.db / sqlite-vec layer not seen)
- `ui-identity` — no UI config

## Gaps for Krab specifically

**NOT covered by `hermes claw migrate`**:
1. **MCP servers** (yung-nagato, p0lrd, hammerspoon) — Hermes registry would need manual port from `~/.openclaw/configs/mcp/*.json` or LaunchAgents
2. **Memory Phase 2** (archive.db, vec_chunks FTS5+RRF+MMR pipeline) — `memory-backend` skipped, only flat `MEMORY.md` migrates
3. **Telegram userbot session** (`kraab.session`, peers DB) — Hermes uses `python-telegram-bot` (bot API), not Pyrogram MTProto userbot — fundamentally incompatible mode
4. **Owner panel** (`:8080`, 248 endpoints) — Hermes has its own gateway, no port
5. **Smart Routing policy store** (`chat_response_policy.py` JSON) — no equivalent in Hermes
6. **Swarm / Task Board / Forum Topics** — Hermes has subagent delegation but different model
7. **Voice Gateway / KrabEar** — separate from migration scope
8. **Cron jobs** — Hermes has its own cron; OpenClaw cron config not migrated automatically
9. **LaunchAgents** — runtime supervision not in scope
10. **Translator engine** — Krab-specific, no Hermes equivalent

## Phase 2 readiness assessment: **5/10**

**Strengths**: Tool exists, runs cleanly, identifies items correctly, has presets/conflict-handling/backup/secret-gating.

**Limitations**:
- Migrates only ~30% of Krab's customization surface (memory + skills + providers + basic ACL)
- Userbot model (Pyrogram MTProto) fundamentally different from Hermes (PTB bot API) — cannot run Krab as Hermes drop-in
- MCP server, memory backend, owner panel, swarm, voice all need manual bridges
- Custom providers migrated as config entries but functionality (e.g. lmstudio routing, codex CLI runner) needs Hermes-side equivalents

## Recommended next actions

**Phase 2 (NOT migrate to Hermes wholesale)**:
1. Keep Krab production as-is; treat Hermes as **eval sandbox** not successor
2. Cherry-pick Hermes design ideas: skills hub format, agentskills.io standard, FTS5 session search, dialectic user modeling (Honcho)
3. Map Krab's 17 skills onto Hermes skill format → useful one-way export (verified working via dry-run)
4. Run real (non-dry) migration into isolated `~/.hermes-eval/` with `--source ~/.openclaw` to compare side-by-side
5. Evaluate Hermes scheduled-cron + subagent-delegation against Krab swarm — pick architectural lessons, not code
6. Defer userbot/owner-panel/smart-routing/MCP — these are Krab-unique, no migration path

**Defer to Phase 3+** (or never): full Hermes adoption requires rewriting userbot layer (MTProto → bot API breaks all current capabilities) and re-implementing 248 endpoints. ROI low.
