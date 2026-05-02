# Hermes ACP Bridge — Phase 2 Design Spec

**Date**: 2026-05-02 (Session 33)
**Status**: design only — NO code changes
**Predecessor**: `HERMES_EVAL_REPORT.md` (Wave 12-A research, Wave 13 PoC)
**Goal**: hybrid coexistence of OpenClaw + Hermes via Agent Client Protocol (ACP),
gated A/B routing per swarm-room / per-chat / per-engine env, with safe
fallback to OpenClaw on any Hermes failure.

## TL;DR

Hermes ships a fully-implemented ACP server (`hermes-agent-eval/acp_adapter/server.py`,
`HermesACPAgent(acp.Agent)`). Krab adds a thin `KrabHermesACPBridge` that
connects as ACP **client** over stdio, opens a session per swarm-room or per-chat,
streams `prompt()` results back, and proxies tool calls. OpenClaw stays the default;
Hermes is opt-in via `KRAB_AGENT_ENGINE=hermes|auto` and `!engine` command.
A/B telemetry compares both engines for 1–2 weeks before any further commitment.

## Architecture

```
                   ┌─────────────────────────────────────────────┐
                   │  Krab userbot_bridge.py  (Pyrogram MTProto) │
                   └────────────┬────────────────────────────────┘
                                │ message
                   ┌────────────▼────────────┐
                   │ src/userbot/llm_flow.py │
                   └────────────┬────────────┘
                                │ resolve engine
                   ┌────────────▼─────────────────────┐
                   │ src/core/agent_engine_router.py  │
                   │   • env: KRAB_AGENT_ENGINE       │
                   │   • per-chat: !engine override   │
                   │   • per-room policy              │
                   │   • health gate + fallback       │
                   └──┬──────────────────────────┬────┘
                      │                          │
        ┌─────────────▼──────────┐   ┌───────────▼──────────────────┐
        │ AgentEngineClient(IF)  │   │ AgentEngineClient(IF)        │
        │ ── OpenClawAdapter     │   │ ── HermesACPAdapter          │
        │    (existing client)   │   │    (new bridge)              │
        └─────────────┬──────────┘   └───────────┬──────────────────┘
                      │ HTTP                     │ stdio JSON-RPC (ACP)
                      ▼                          ▼
              OpenClaw :18789          hermes acp  (subprocess or LaunchAgent)
                                       └─ HermesACPAgent.prompt()
                                          └─ AIAgent.run_conversation()
                                             └─ tool calls (native MCP)
```

`AgentEngineClient` is a small Protocol (`async def stream(prompt, ctx) -> AsyncIterator[Chunk]`,
`async def cancel()`, `async def health() -> EngineHealth`). Existing
`openclaw_client.OpenClawClient` is wrapped to satisfy it; `HermesACPAdapter` is new.

## Subprocess vs standalone-running Hermes

Two modes, both supported behind the same adapter:

1. **Spawned per Krab process** (default for Phase 2) — Krab `subprocess.Popen(["hermes", "acp"], stdin=PIPE, stdout=PIPE, env=clean_subprocess_env())`,
   speaks JSON-RPC framing per ACP spec. One process per Krab restart; sessions
   persist in `~/.hermes/state.db`. Simple, no extra LaunchAgent.
2. **Standalone LaunchAgent** (`ai.krab.hermes-acp`) — Hermes runs detached on
   a Unix domain socket; Krab connects via socket. Survives Krab restarts,
   shares one Hermes process across multiple Krab processes (worktree dev).
   Defer to Phase 3.

## Session lifecycle

- One ACP session per logical "channel": swarm room (`session:swarm:analysts`),
  per-chat session (`session:chat:{chat_id}`), or per-task spawn.
- `new_session(cwd=krab_repo_root, mcp_servers=[...])` at first use; cached in
  `KrabHermesACPBridge._sessions`. Hermes already knows MCP via session/load
  registration — Krab passes the same MCP server list it already configures
  for OpenClaw (yung-nagato 8011, p0lrd 8012, hammerspoon 8013).
- `prompt(blocks, session_id)` streams `AgentMessageChunk` / tool events back.
  Bridge forwards chunks to the Telegram message (typing indicator + edited
  message progressive update, same plumbing as current OpenClaw streaming).
- `cancel(session_id)` on user `!stop` or message-batcher reset.
- `load_session` / `resume_session` on Krab restart — sessions survive because
  Hermes persists them in its SessionDB.

## Tool calls

Hermes runs tools natively (no Krab-side execution). Krab MCP servers are
already exposed via SSE/stdio; we register them with Hermes via ACP
`new_session(mcp_servers=...)`. Tool results flow inside Hermes; Krab sees only
`tool_progress` events (for live UI) and the final assistant message.
Krab's `mcp_client.py` relay is bypassed when Hermes is the engine — that's
expected (the eval report flagged this as a Hermes-side improvement).

## Streaming back to Telegram

`HermesACPAdapter` exposes `async def stream(prompt) -> AsyncIterator[StreamChunk]`
matching the OpenClaw shape exactly (text, role, tool_progress, finish_reason).
`llm_flow.py` doesn't know which engine produced the chunk — that's the win.

## Routing decision logic

Order of resolution (first match wins):

1. **Per-chat override** — `!engine hermes|openclaw|auto` writes to
   `~/.openclaw/krab_runtime_state/agent_engine_overrides.json`.
2. **Per-swarm-room policy** — `swarm_engine.json`:
   `{"traders": "openclaw", "analysts": "hermes", "coders": "auto", "creative": "openclaw"}`.
3. **Env default** — `KRAB_AGENT_ENGINE=openclaw` (default), `hermes`, or
   `auto` (LLM-side router uses cost/latency hints).
4. **Health gate** — if selected engine fails health probe in last 60s, fall
   back to the other. `agent_engine_router.py` records a sticky failure with
   exponential backoff on repeated falls.

Hard rules:
- DM/simple-chat → always OpenClaw (latency-sensitive, no need for agent loops).
- `!swarm <team> <topic>` → engine per per-swarm-room policy.
- `!swarm research` long-running → prefer Hermes (delegate_tool, curator).
- Translator path → always OpenClaw (Gemini Flash, no agent loop needed).

## A/B comparison instrumentation

New table `agent_engine_runs` in `archive.db`:

| col | type | note |
|---|---|---|
| run_id | TEXT PK | uuid |
| started_at, finished_at | INTEGER | epoch ms |
| engine | TEXT | `openclaw` / `hermes` |
| chat_id, room | TEXT | nullable |
| prompt_tokens, completion_tokens, reasoning_tokens | INTEGER | |
| tool_calls | INTEGER | count |
| latency_ms_ttfb, latency_ms_total | INTEGER | |
| success | INTEGER | 0/1 |
| fallback_engine | TEXT | nullable, set when fallback fired |
| user_signal | TEXT | reaction emoji / delete / null |
| cost_usd | REAL | from cost_analytics |

Endpoints (added to existing `web_routers/`):

- `GET /api/agent-engine/comparison?window=7d` → side-by-side aggregates.
- `GET /api/agent-engine/runs?engine=hermes&limit=100` → recent run inspector.
- Prometheus: `krab_agent_engine_latency_seconds{engine}`,
  `krab_agent_engine_runs_total{engine,success}`, `krab_agent_engine_fallback_total`.

Logging: every `llm_request` event in structlog gets `agent_engine` field;
Sentry transaction tagged `engine=hermes|openclaw`.

## Phase 2 implementation roadmap (8–12 tasks)

| # | Task | Est. session-fraction |
|---|---|---|
| 1 | `src/core/agent_engine.py` — `AgentEngineClient` Protocol + dataclasses (`StreamChunk`, `EngineHealth`) | 0.2 |
| 2 | Wrap `OpenClawClient` to satisfy interface — no behavior change | 0.2 |
| 3 | `src/integrations/hermes_acp_bridge.py` — subprocess launch + ACP client wiring (`acp` Python pkg) | 1.0 |
| 4 | Streaming chunk translation Hermes events → `StreamChunk` shape | 0.5 |
| 5 | `src/core/agent_engine_router.py` — selection + fallback + health probe | 0.5 |
| 6 | `!engine` command + `agent_engine_overrides.json` store | 0.3 |
| 7 | `swarm_engine.json` per-room policy + `swarm.py` engine resolution | 0.3 |
| 8 | `archive.db` migration + `agent_engine_runs` writer | 0.4 |
| 9 | Web router `agent_engine_router.py` + 2 endpoints + Prometheus metrics | 0.4 |
| 10 | Tests: bridge unit (mock `acp` server), router decision matrix, fallback path | 0.6 |
| 11 | Standalone Hermes setup script + dry-run guide | 0.3 |
| 12 | Roll out one swarm room (analysts) on Hermes; observe 48h | 0.3 |

**Total estimate: ~2 sessions** for full Phase 2 (subprocess mode + analysts room rollout).

## A/B comparison plan

- **Length**: 1 week minimum, 2 weeks preferred.
- **Cohort**: `analysts` swarm room on Hermes; other 3 rooms stay on OpenClaw.
- **Sample size goal**: ≥50 runs per engine in `analysts`-comparable workload.
- **Success criteria** (Hermes wins → expand to coders + creative):
  - latency_ms_total median ≤ 1.2× OpenClaw,
  - success rate ≥ 95% (no crash/timeout/fallback),
  - tool-call accuracy parity (manual review of 20 random runs),
  - cost_usd ≤ 1.3× OpenClaw,
  - no negative user_signal trend.
- **Failure criteria** (rollback):
  - >5% prompts trigger fallback to OpenClaw,
  - any Hermes session corruption that drops user history,
  - p95 latency >2× OpenClaw.

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Hermes subprocess crash mid-stream | M | M | sticky-fail backoff + automatic OpenClaw fallback within 60s |
| Memory state desync (Hermes SessionDB vs Krab archive.db) | H | L | clear ownership: Hermes owns its session history; Krab archive remains source-of-truth for cross-engine memory; bridge writes summary back to archive.db on session close |
| Provider credential drift | M | M | bridge passes `OPENCLAW_API_KEY`/`GOOGLE_API_KEY`/`OPENROUTER_API_KEY` via env; Hermes reads same env. No second source. |
| Cost double-counting | L | M | per-run cost attributed once via `agent_engine_runs.cost_usd` derived from token counts in PromptResponse.usage |
| Hermes deps weight (≥12k LOC, voice extras) | M | L | install only `hermes-agent[base]` extras; pin version |
| Telegram MTProto mismatch (Hermes uses python-telegram-bot Bot API) | — | — | not relevant: Krab keeps userbot front-end, Hermes is engine-only |
| ACP protocol breakage on upgrade | L | M | pin `acp==X.Y.Z`; integration test on every Hermes bump |
| Two-engine cognitive load for ops | M | L | `/api/agent-engine/comparison` dashboard + `!engine status` |

## Open questions

1. **Hermes session persistence**: survives subprocess restart (Hermes saves to `~/.hermes/state.db`). But if Krab restarts and re-spawns Hermes, do we resume the same session ids? Bridge needs to persist the (chat_id → session_id) map in Krab side.
2. **Provider credentials**: shared env (single `GOOGLE_API_KEY`) — confirmed safe; both engines hit Google billing the same way.
3. **MCP server lifecycle**: with Hermes engine, do we still need Krab `mcp_client.py` SSE relay? Probably yes for OpenClaw paths; Hermes connects directly via stdio MCP.
4. **Cost tracking**: Hermes returns `Usage(input_tokens, output_tokens, cached_read_tokens)`. Map to existing `cost_analytics` exactly the same as OpenClaw — formula in `cost_analytics.py` is provider-driven, engine-agnostic.
5. **Observability cardinality**: adding `engine` label to every metric doubles series count. Acceptable on local Prometheus (single host).

## Decision criteria for Phase 3

After 1–2 week A/B in `analysts`:

- **Hermes-first** (deprecate OpenClaw): Hermes wins on ≥4/5 success criteria,
  no critical failures, ops feedback positive. → migrate remaining 3 rooms +
  `!swarm research` + DM agent loops.
- **Hybrid permanent**: Hermes wins on long-running agent tasks but loses on
  simple chat latency. → keep OpenClaw for simple chat / DM / translator,
  Hermes for swarm + research + cron LLM tasks.
- **OpenClaw-only** (rollback): Hermes hits failure criteria, ops cost too
  high, or tool-call accuracy regressions. → remove `hermes_acp_bridge.py`,
  archive `archive.db.agent_engine_runs`, document learnings.

## References

- Hermes ACP server: `hermes-agent-eval/acp_adapter/server.py` (`HermesACPAgent`)
- Hermes session manager: `hermes-agent-eval/acp_adapter/session.py` (`SessionManager`, `SessionState`)
- ACP spec: https://agentclientprotocol.com (Zed editor team)
- Python `acp` package: `pip install agent-client-protocol`
- Migration tool: `hermes claw migrate` (reads `~/.openclaw/`, dry-run flag)
- Eval report: `docs/architecture/HERMES_EVAL_REPORT.md`
