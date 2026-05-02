# CLI tool_calls_executed — Telemetry Contract Design

**Status:** Draft (Session 33, Wave 11-C)
**Owners:** Krab runtime · OpenClaw gateway team
**Related:** Wave 9-A guard (`llm_flow.py`), Wave 9-B blindspot RCA, Wave 9 hallucination incident

## 1. Problem statement

For OpenAI-style providers Krab observes the full tool-call loop: the
`message.tool_calls` array arrives in the response, Krab dispatches each call
through `mcp_manager.call_tool_unified`, appends the `role=tool` reply, and
recurses. `_active_tool_calls` is populated synchronously, and the Wave 9-A
guard can verify *"did the model claim a tool ran?"* against ground truth.

CLI providers (`codex-cli`, `claude-cli`, `gemini-cli`, `opencode`) break this
contract. The CLI is a black box: OpenClaw spawns a subprocess that owns its
own MCP client, runs tools internally, and returns **only the final text**.
From Krab's side `tool_calls` is empty, `_active_tool_calls` stays empty, and
the model can claim *"я отправил сообщение в чат X"* without any reply ever
being written. Wave 9 was exactly this failure mode.

We need a structured signal — emitted by OpenClaw, consumed by Krab — that
lets Krab **verify** which MCP tools the CLI actually executed.

## 2. Proposed contract

OpenClaw extends the chat-completions response with a top-level
`tool_calls_executed` array (sibling of `choices`, `usage`):

```json
{
  "choices": [...],
  "usage": {...},
  "tool_calls_executed": [
    {
      "tool": "krab-telegram.telegram_send_message",
      "args_redacted": {"chat_id": 123, "text_sha256": "ab12…", "text_len": 184},
      "status": "done",
      "result_summary": {"message_id": 456, "ok": true},
      "started_at_ms": 1714650000123,
      "elapsed_ms": 234,
      "provider": "codex-cli",
      "trace_id": "ocl-7f3a…"
    }
  ]
}
```

Field semantics:

- `tool` — fully-qualified MCP tool name (`<server>.<tool>`), matches Krab's
  `mcp_manager` namespace.
- `args_redacted` — privacy-safe projection of arguments (see §3).
- `status` — `done | error | timeout | cancelled`. `running` MUST NOT appear in
  a final response (only in streaming/SSE deltas).
- `result_summary` — bounded subset of the tool result. For Telegram tools:
  `message_id`, `chat_id`, `ok`. For HTTP fetch: `status_code`, `bytes_len`.
  Never the full payload.
- `elapsed_ms` — wall-clock duration of the MCP call.
- `trace_id` — links to OpenClaw structured logs / Sentry breadcrumbs.

The array MUST preserve execution order. Empty array means *"CLI ran but
called no tools"*; **absence** of the field means *"legacy/unknown — Krab
falls back to existing behavior"* (backwards-compat key).

## 3. Privacy & redaction

Tool arguments often carry user content (Telegram message text, search
queries, file paths). To prevent log/Sentry exfiltration:

- Free-text fields (`text`, `caption`, `query`, `body`) → replaced with
  `<field>_sha256` (first 16 hex chars) + `<field>_len`.
- Identifiers (`chat_id`, `message_id`, `url` host) → kept verbatim.
- Filesystem paths → kept (already non-secret in Krab's scope).
- Allowlist of redaction rules lives in OpenClaw config, NOT in the response.

`result_summary` follows the same rule: surface IDs/status, drop bodies.

## 4. Reliability — partial / failure modes

| CLI behaviour | OpenClaw emits | Krab interpretation |
|---|---|---|
| Tool ran, returned ok | `status=done`, `result_summary` | Verified. |
| Tool raised | `status=error`, `error_kind`, `elapsed_ms` | Treat as attempted-but-failed. Log to Sentry. |
| MCP call hung past gateway timeout | `status=timeout`, `elapsed_ms=<deadline>`, no `result_summary` | Treat as attempted; emit `cli_tool_timeout` Sentry event. |
| CLI killed mid-call | `status=cancelled` | Same as timeout. |
| CLI claims tool ran but no telemetry | array missing the entry | **Ground truth: it did NOT run.** Wave 9-A guard fires. |

Streaming responses MAY emit interim `running` entries via SSE deltas, but the
final completion frame consolidates to a terminal status.

## 5. Backwards compatibility

- Field is **optional**. Legacy responses parse unchanged.
- Krab's `_openclaw_completion_once` checks `data.get("tool_calls_executed")`.
  `None` → treat provider as opaque (status quo).
  `[]` or list → use as ground truth, populate parallel
  `_active_cli_tool_calls`, expose via `get_active_tool_calls_summary()`.
- The Wave 9-A regex/keyword guard becomes a **fallback** (only triggers when
  `tool_calls_executed` is absent and content claims an action).

## 6. Implementation roadmap

**Phase 1 — opt-in, codex-cli only.**
OpenClaw env flag `OPENCLAW_EMIT_CLI_TELEMETRY=codex-cli`. Krab parses the
field if present, no behavioural change otherwise. Validate format end-to-end
on staging via `!codex` smoke tests.

**Phase 2 — extend to claude-cli + opencode.**
Same flag, additive providers. Krab adds metrics:
`krab_cli_tool_calls_emitted_total{provider,tool,status}`. Surface in Grafana.

**Phase 3 — production hard-require.**
For `provider in {codex-cli, claude-cli, opencode}` Krab refuses to forward
the model's textual claim of a destructive action (`telegram_send_message`,
`telegram_delete_message`, `fs_write`) **unless** the matching entry exists in
`tool_calls_executed`. Replaces the heuristic Wave 9-A guard for these
providers. Gemini-cli onboarded once OpenClaw upstream supports it.

## 7. Krab-side changes (post-contract)

1. `src/openclaw_client.py::_openclaw_completion_once` — parse the new field,
   merge into `self._active_tool_calls` (or a sibling list) so existing
   downstream consumers (`get_active_tool_calls_summary`, progress notices,
   FinOps `tool_calls_count`) keep working unchanged.
2. `src/userbot/llm_flow.py` Wave 9-A guard — gate by provider class. For CLI
   providers prefer structured verification; keep regex fallback only when
   `tool_calls_executed` absent.
3. Sentry — emit `cli_tool_call_executed` breadcrumb per entry, plus
   `cli_tool_call_missing` event when the guard fires due to a
   claim-without-telemetry mismatch (replaces the noisy Wave 9 fallback).
4. Owner panel — `/api/runtime/cli-tool-calls?since=…` for ops debugging.

## 8. Risks & mitigations

- **Upstream coordination.** OpenClaw must ship the emitter. Mitigation:
  contract is opt-in via env flag, Krab side is feature-gated by field
  presence — both sides can land independently.
- **Spec drift across CLI versions.** Codex-cli's MCP exposure changes
  occasionally. Mitigation: OpenClaw owns the redaction/normalisation layer;
  Krab treats the contract as stable regardless of upstream CLI churn.
- **Privacy regression** if OpenClaw mis-redacts. Mitigation: redaction
  allowlist tested with golden fixtures; Krab additionally re-hashes anything
  that looks like prose before logging.
- **False negatives in Phase 3 hard-require** (CLI ran the tool but telemetry
  was dropped). Mitigation: keep an emergency env switch
  `KRAB_CLI_TELEMETRY_ENFORCE=0` to demote enforcement back to Phase 2 mode.

## 9. Open questions for the OpenClaw team

1. Where in the gateway pipeline do we intercept the CLI's MCP traffic — at
   the stdio boundary, or via a wrapping MCP proxy? Implications for hung-call
   detection and `status=timeout` accuracy.
2. Streaming: do we need interim `running` deltas for long tool calls, or is a
   single terminal entry per response enough for current UX?
3. Result summary schema: per-tool bespoke shapes (`message_id` for
   `telegram_send_message`) vs. a uniform `{ok, id, kind}`. Bespoke is more
   useful but creates a maintenance surface — preference?
4. `trace_id` — reuse OpenClaw's existing request id, or mint per tool call?
   Krab wants per-call to correlate with Sentry.
5. Phase 3 scope: which destructive tools require hard-enforcement on day
   one? Proposed set: `telegram_send_message`, `telegram_delete_message`,
   `telegram_edit_message`, `fs_write`, `notes_create`, `reminders_create`.
