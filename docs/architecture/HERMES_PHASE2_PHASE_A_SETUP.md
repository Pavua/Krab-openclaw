# Hermes Phase 2 — Phase A Setup (Wave 15-D)

Status: COMPLETE (smoke-tested 02.05.2026)

## Goal
Prepare a standalone Hermes Agent installation that the Krab ACP bridge
(Phase B) can spawn as a subprocess for selective integration with one swarm
room (analysts).

## Important deviation from original spec

The Wave 15-D task description called for a **standalone Hermes service on
port 3101** behind a `LaunchAgent`, with an HTTP `/health` endpoint.

After investigating Hermes v0.12.0, this turns out not to match how `hermes
acp` actually works:

- `hermes acp` is a **stdio JSON-RPC server** (Agent Client Protocol),
  not an HTTP service. There is no listening port and no `--port` flag.
- The protocol expects a single ACP client connected over stdin/stdout
  (typical pattern: editor integrations like Zed/VS Code/JetBrains).
- A `LaunchAgent` daemon for `hermes acp` would block on stdin forever
  with no client attached — pointless overhead.

This matches the ACP bridge design doc
(`docs/architecture/HERMES_ACP_BRIDGE_DESIGN.md`, §"Two modes" lines 57-89),
which correctly anticipates Hermes being spawned as a stdio subprocess by
Krab's bridge process. The "service on 3101" phrasing in the Phase A spec
was aspirational and is superseded by the design doc.

What `:3101` *could* refer to is `hermes dashboard` (web UI on port 9119
by default, configurable) — but that's a separate concern from ACP bridging
and not needed for Phase B.

## Phase A deliverables (this wave)

| Item | Path | Notes |
|------|------|-------|
| Hermes install | `/Users/pablito/Antigravity_AGENTS/hermes-agent-eval/.venv/` | uv-managed |
| Hermes version | `0.12.0` (release `2026.4.30`) | Python 3.14.4 |
| ACP optional deps | `agent-client-protocol==0.9.0` | added via `uv pip install -e '.[acp]'` |
| Hermes home | `~/.hermes/` | created previously, retained |
| Hermes config | `~/.hermes/config.yaml` | `model: gpt-4o-mini` set; **no Telegram MCP** (avoids hallucination vector — Wave 9-B lesson) |
| API keys | `~/.hermes/.env` (mode 0600) | `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY` mirrored from Krab `.env` |
| Launch script | `scripts/start_hermes_standalone.command` | `exec hermes acp` — stdio entry for Phase B bridge |
| LaunchAgent plist | `scripts/launchagents/ai.hermes.acp.plist` | **Reference only — NOT loaded.** See plist comment for rationale. |
| Logs directory | `Краб/logs/` (existing) | reused if plist ever loaded |

## Smoke test (live, 02.05.2026)

```bash
cd /Users/pablito/Antigravity_AGENTS/hermes-agent-eval
( printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{}}}\n'; sleep 2 ) \
  | timeout 5 .venv/bin/hermes acp
```

**Result** (verbatim, JSON pretty-printed for readability):
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "agentCapabilities": {
      "loadSession": true,
      "promptCapabilities": { "image": true },
      "sessionCapabilities": { "fork": {}, "list": {}, "resume": {} }
    },
    "agentInfo": { "name": "hermes-agent", "version": "0.12.0" },
    "authMethods": [
      {
        "id": "openrouter",
        "name": "openrouter runtime credentials",
        "description": "Authenticate Hermes using the currently configured openrouter runtime credentials."
      }
    ],
    "protocolVersion": 1
  }
}
```

Exit code `0`, stderr clean (only `Loaded env` + `Starting … ACP adapter` +
`ACP client connected`). ACP server is fully functional.

## Phase B prerequisites

Phase B (ACP bridge for one swarm room — analysts) needs the following:

1. **Python ACP client library** — `agent-client-protocol` is available on
   PyPI; pin `==0.9.0` to match server. Install in Krab's `venv/`.
2. **Bridge module** — likely `src/integrations/hermes_bridge.py` per design
   doc §"ACP Bridge component". Spawns `start_hermes_standalone.command`
   with stdin/stdout pipes, manages session lifecycle, translates between
   Krab's swarm room API and ACP `prompt`/`session/*` methods.
3. **Feature flag** — `KRAB_HERMES_BRIDGE_ENABLED` env var; default off.
4. **A/B routing** — `analysts` team only (per Phase B spec); other teams
   continue using existing swarm executor.
5. **Metrics** — Prometheus counters: `krab_hermes_bridge_invocations_total`,
   `krab_hermes_bridge_errors_total`, `krab_hermes_bridge_latency_seconds`.
6. **Smoke tests** — unit test for bridge spawn/teardown, integration test
   round-tripping a single `prompt` request.

## Constraints honored

- [x] No Telegram MCP servers configured in Hermes (`mcp_servers` not set
      in `~/.hermes/config.yaml`) — same lesson as Wave 9-B.
- [x] LaunchAgent plist created but **not loaded** — left as user step
      after testing, and only as a reference template since stdio ACP
      doesn't fit a daemon model.
- [x] No production traffic — Phase A is setup only; bridge wiring is
      Phase B work.
- [x] API keys reused from Krab — no new keys required. `~/.hermes/.env`
      mode 0600.

## Re-running the smoke test

```bash
bash /Users/pablito/Antigravity_AGENTS/Краб/scripts/start_hermes_standalone.command < /dev/null &
PID=$!
sleep 2
kill $PID 2>/dev/null
```

(Without ACP traffic on stdin, the process will idle until killed — expected.)

For a meaningful smoke test, send the JSON-RPC `initialize` payload as in
the live test above.
