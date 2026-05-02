# Hermes Agent — Evaluation Report (Wave 13 PoC)

**Date**: 2026-05-02 (Session 33)
**Repo**: https://github.com/NousResearch/hermes-agent
**Eval directory**: `/Users/pablito/Antigravity_AGENTS/hermes-agent-eval/`
**Status**: clone OK, source-level inspection done, no smoke test (heavy deps not installed)

## TL;DR

Hermes Agent — это **прямой наследник OpenClaw** от Nous Research. В репозитории буквально присутствует команда `hermes claw migrate`, которая читает `~/.openclaw/` (и легаси `~/.clawdbot/`, `~/.moltbot/`), и official migration guide (`website/docs/guides/migrate-from-openclaw.md`). Это не «конкурент» — это **upstream** того же кодового семейства, под MIT лицензией, активно поддерживаемое (текущая версия `0.12.0`, releases v0.2 → v0.12).

**Feasibility для интеграции в Krab swarm: 7/10.** Hybrid-pattern through ACP реалистичен. Полная замена OpenClaw — крупная миграция (2-4 сессии), но дорожка проложена самим Nous.

## Repo facts

| Field | Value |
|-------|-------|
| Lang | Python ≥3.11 (≈12k LOC core `run_agent.py`, ≈11k LOC `cli.py`, ≈700 test files) |
| License | MIT |
| Build | setuptools + uv.lock |
| Core deps | openai≥2.21, anthropic≥0.39, httpx, pydantic, croniter, prompt_toolkit, jinja2 |
| Optional extras | `[messaging]` (telegram-bot, discord, slack), `[matrix]`, `[modal]`, `[daytona]`, `[vercel]` |
| Frontend | Ink/React TUI (`ui-tui/`), web Docusaurus site |
| Smoke test | **NOT executed** — would require uv install + provider key configuration; out of 30-min budget |

## Architecture (load-bearing pieces)

```
hermes-agent/
├─ run_agent.py        # AIAgent class — main conversation/tool loop
├─ model_tools.py      # discover_builtin_tools(), handle_function_call()
├─ toolsets.py         # _HERMES_CORE_TOOLS — togglable toolsets
├─ cli.py              # HermesCLI — interactive
├─ hermes_state.py     # SQLite SessionDB w/ FTS5 cross-session search
├─ agent/
│  ├─ memory_manager.py / memory_provider.py   # Memory abstraction
│  ├─ context_engine.py / context_compressor.py # Context layers
│  ├─ curator.py        # Skill self-improvement loop
│  ├─ prompt_caching.py # Anthropic + OAI cache
│  └─ trajectory.py     # RL trajectory capture (Atropos)
├─ acp_adapter/        # ACP protocol server (VS Code/Zed/JetBrains)
│  ├─ server.py         # exposes Hermes via Agent Client Protocol
│  ├─ session.py / events.py / tools.py / permissions.py / auth.py
├─ gateway/            # Multi-platform messaging gateway
│  └─ platforms/       # telegram, discord, slack, whatsapp, signal, matrix, ...
├─ plugins/
│  ├─ memory/          # honcho, mem0, supermemory, holographic, byterover, hindsight, retaindb, openviking
│  ├─ context_engine/
│  └─ ...
├─ tools/
│  └─ delegate_tool.py # Subagent spawning — isolated context, restricted toolsets, parallel batch mode
├─ cron/               # Built-in scheduler (jobs.py, scheduler.py)
└─ optional-skills/
   └─ migration/openclaw-migration/  # Official OpenClaw → Hermes path
```

### Tool execution loop
- Centralised in `model_tools.handle_function_call()` invoked from `run_agent.py` AIAgent loop. Streaming tool output supported.
- Provider adapters under `agent/` (anthropic_adapter, codex_responses_adapter, gemini_native_adapter, gemini_cloudcode_adapter, bedrock_adapter, lmstudio_reasoning).

### Memory layers
- `agent/memory_manager.py` + pluggable `plugins/memory/*`. Curator (`agent/curator.py`) drives self-improvement and persistence nudges. `~/.hermes/memories/MEMORY.md` echoes Krab's pattern.
- FTS5 session search in `hermes_state.py` (analogous to Krab Memory Phase 2 archive.db).

### MCP integration
- `mcp_serve.py` at root. Dev-extra includes `mcp>=1.2.0`. Hermes consumes MCP servers natively (vs Krab `mcp_client.py` relay).

### ACP protocol
- Real implementation in `acp_adapter/server.py` (`import acp`, `from acp.schema import AgentCapabilities, ...`). Sessions, events, tools, permissions, auth — all wired. This is the integration channel.

### Subagent / swarm
- `tools/delegate_tool.py` already exists. Spawns isolated child `AIAgent` instances — own task_id, own terminal session, restricted toolset, blocked-tools enforcement. Supports single + batch parallel mode. Parent context only sees delegation call + summary (Krab's swarm shape exactly).

## Krab swarm compatibility

| Krab piece | Hermes equivalent | Compat |
|---|---|---|
| 4 separate Pyrogram clients (traders/coders/analysts/creative) | `gateway/platforms/telegram.py` (single) + `delegate_tool` for parallel agents | partial — Hermes gateway is one process per platform; for 4 personas нужно либо 4 gateway-инстанса с разными tokens, либо in-process delegation |
| `_active_tool_calls` tracking | Structured trajectory + acp events | better |
| `mcp_client.py` relay | Native MCP via `mcp_serve.py` + `mcp` package | better |
| `swarm_memory.py` (JSON FIFO) | `plugins/memory/*` + curator | much better |
| `swarm_task_board.py` (Kanban) | None obvious — would остаться в Krab | keep |
| Cron / scheduler | `cron/scheduler.py` (croniter built-in) | parity |
| OpenClaw routing/gateway | Hermes is self-contained; ACP заменяет gateway | replaces |
| Owner panel `:8080` | None — Krab keeps it | keep |

## Recommended next step

**Hybrid via ACP**, как и предлагал Wave 12-A:

1. Краб остаётся как Telegram/voice/owner-panel front-end.
2. Hermes Agent runs side-by-side как ACP сервер на отдельном порту.
3. `src/core/swarm.py` execution loop делегирует в Hermes через ACP: создаёт session, посылает tool prompts, потребляет AgentMessageChunk events.
4. `tools/delegate_tool.py` Hermes-а дальше параллелит 4 swarm rooms (traders/coders/analysts/creative) внутри своего процесса.
5. MCP servers (8011/8012/8013) переключаются с Krab `mcp_client` relay на native Hermes MCP consumption.

### Phased plan
- **Session 33 (this)**: report only. ✅
- **Session 34**: standalone Hermes install (`hermes setup` в `~/.hermes-eval/`), provider key wired, smoke test, run `hermes claw migrate --dry-run` against `~/.openclaw/` чтобы увидеть полный preview.
- **Session 35**: write `src/integrations/hermes_acp_bridge.py` — ACP client который подключается к Hermes ACP server и проксирует swarm tool calls. Feature flag `KRAB_SWARM_VIA_HERMES`. Один room (analysts) первым.
- **Session 36**: roll out на all 4 rooms, deprecate `swarm.py` AgentRoom internal loop, keep task_board / channels / verifier как Krab-side adapters.

## Risks / concerns

- **Two memory stores**: `~/.hermes/memories/MEMORY.md` vs Krab archive.db — нужен sync layer или решение «one source of truth».
- **Provider config drift**: Hermes own `config.yaml`/`models.json` отдельно от `~/.openclaw/agents/main/agent/models.json`. Migration script это решает однократно, но runtime ongoing — отдельный canonical нужен.
- **Telegram MTProto**: Hermes gateway использует `python-telegram-bot` (Bot API), Krab — `pyrofork` (MTProto userbot). Это разные API — Hermes НЕ заменит userbot, только дополнит как backend.
- **Heavy dep footprint**: full `[all]` extra включает voice (edge-tts), discord voice, mautrix encryption — много того, что Krab уже решил иначе.
- **Coupling lock-in**: ACP стандартизован (Zed/JetBrains/VS Code), но Hermes-specific features (curator, trajectory, RL Atropos) are off-protocol.

## Estimated full integration effort

- Hybrid PoC (1 swarm room через ACP): **1 session (~Session 35)**.
- Full swarm migration (4 rooms + memory sync): **2-3 sessions**.
- Полная замена OpenClaw на Hermes (with migration): **3-4 sessions** + observation period.
- **Total если идём до конца: ~4-5 sessions.**

## Verdict

Continue evaluation. Recommend **hybrid integration**, NOT abandon, NOT full immediate replacement. Hermes — реальный путь вперёд OpenClaw-ecosystem; миграционный мост в репо подтверждает это явно. Основной риск — двойное хранилище состояния; адресуется через clear ownership: Hermes владеет skill/curator/trajectory, Krab владеет Telegram MTProto/owner panel/swarm task board.
