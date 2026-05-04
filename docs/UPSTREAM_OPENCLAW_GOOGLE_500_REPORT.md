# OpenClaw 2026.5.2: WebSocket → openresponses HTTP path returns 500 for google/* models

**TL;DR:** WebSocket gateway transport для `google/*` моделей возвращает `HTTP 500 {"error":{"message":"internal error","type":"api_error"}}`. CLI local transport — работает. Похоже на regression аналогичную fix'у для OpenAI GPT-5 в CHANGELOG 2026.5.2 (line 57), но для Google не зафикшено.

## Environment

- OpenClaw version: `2026.5.2 (8b2a6e5)`
- macOS 14+, Apple Silicon (M4 Max), Node 22.x
- Python clients (Krab userbot) connecting via WebSocket

## Reproduction

### Working (CLI local transport):

```bash
openclaw infer model run --local --model "google/gemini-3-pro-preview" --prompt "Скажи привет"
# Returns: "Привет! 👋 Чем я могу помочь?"
```

### Broken (gateway WebSocket transport):

```python
# Krab Python WebSocket client
async for chunk in openclaw_client.send_message_stream(
    message="hi",
    chat_id="test",
    preferred_model="google/gemini-3-pro-preview",
):
    print(chunk)
# Returns: HTTP 500 {"error":{"message":"internal error","type":"api_error"}}
```

### Direct Google API works:

```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-preview:generateContent?key=$GEMINI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"привет"}]}]}'
# Returns: {"candidates":[{"content":{"parts":[{"text":"Привет."}]}}]}
# Response time: ~3.5s — API key and quota are healthy
```

## Affected Models (verified)

| Model | CLI (`--local`) | Gateway (WebSocket) |
|-------|----------------|---------------------|
| `google/gemini-3-pro-preview` | ✅ works | ❌ HTTP 500 |
| `google/gemini-3.1-pro-preview` | ❌ fails (separate parsing issue) | ❌ HTTP 500 |
| `google/gemini-2.5-pro-preview-06-05` | ❌ fails (same parsing issue as 3.1) | ❌ HTTP 500 |
| `google/gemini-2.5-flash` | ✅ works | not tested |

Note: `gemini-3.1` and `gemini-2.5-pro-preview-06-05` CLI failures appear to be a separate issue (response parsing); the gateway 500 is a distinct regression affecting all `google/*` models.

## Code Analysis (best-effort from minified dist/)

### CLI path — works

`capability-cli-DdJKTRJF.js:1203`:

```js
defaultTransport: "local"
// → completeWithPreparedSimpleCompletionModel()
// → direct Google Generative AI SDK call (no gateway involved)
```

### Gateway path — broken

`server.impl-B11albXx.js:3471`:

```js
// client → callGateway({ method: "agent" })
//   → WebSocket RPC → agent runtime
//     → handleOpenResponsesHttpRequest()
//       → openresponses-http-DA77dquf.js
//         → ALL unhandled exceptions wrapped as generic 500 (lines 824, 1104)
```

`provider-stream-CFMwcPPe.js:783`:

```js
"google-generative-ai": "openclaw-google-generative-ai-transport"
// new transport wrapper introduced in 2026.5.2
```

`openai-transport-stream-BZZu1dNP.js:488`:

```js
// SYNTHETIC_TOOL_RESULT_APIS contains "openclaw-google-generative-ai-transport"
// suggests the new google transport is wired through OpenAI-compat tool result handling
```

The `openresponses-http` layer catches all upstream errors and re-wraps them as `{"error":{"message":"internal error","type":"api_error"}}` — masking the actual Google API error from callers.

## CHANGELOG Context

**CHANGELOG 2026.5.2, line 57:**

> "Agents/OpenAI: default GPT-5 API-key sessions to the SSE Responses transport unless WebSocket is explicitly selected, restoring replies in fresh Control UI and WebChat beta installs **where the auto WebSocket path connected but produced no model events**."

This fix targeted OpenAI specifically. The same class of bug — "WebSocket path connected but produced no model events" — appears to affect the new `openclaw-google-generative-ai-transport` wrapper introduced in the same 2026.5.2 release. For Google models, no analogous fix was applied.

## Workaround

В нашем проекте (Krab Python userbot) обошли проблему, добавив прямой вызов Google SDK в обход OpenClaw gateway:

- `src/integrations/google_genai_direct.py` — wraps `google.genai.Client.models.generate_content`
- Активируется только когда модель начинается с `google/` (не затрагивает `google-gemini-cli/`, `google-antigravity/`)
- ENV gate: `KRAB_GOOGLE_DIRECT_BYPASS_ENABLED=1` (default ON)

Этот workaround фактически воспроизводит поведение CLI `--local` transport внутри Python клиента. Работает, но лишает нас gateway-level features (routing, fallbacks, observability).

## Suggested Fix Priorities

1. **Apply analogous WebSocket → SSE Responses transport fix for `google/*`** — mirroring the OpenAI GPT-5 fix in line 57: default google/* API-key sessions to SSE Responses transport unless WebSocket is explicitly selected.
2. **OR: Surface real upstream Google API error** — the `openresponses-http` layer currently swallows all provider exceptions as generic 500. Propagating the actual error would at minimum make debugging feasible.
3. **Add integration test** covering WebSocket gateway path for all major providers (openai/*, google/*, anthropic/*), not just OpenAI.

## Additional Notes

- The issue is **not** an API key/quota problem — direct Google API calls succeed in 3.5s
- The issue is **not** a network/firewall problem — same machine, same process
- The regression is specifically in the **gateway WebSocket → openresponses HTTP path** for `google/*`
- Severity: **High** — blocks production use of any OpenClaw Python/WebSocket client with google/* models

## Logs

Happy to provide Sentry traces / gateway console output if maintainer requests. Our Sentry project captures the HTTP 500 responses with timestamps.

---

**Reporter:** Krab project  
**Discovery date:** 2026-05-04 (Session 36)  
**Severity:** High — blocks production использование любого OpenClaw WebSocket client с `google/*` models  
**Workaround:** Direct Google SDK bypass (replicates CLI `--local` transport behavior)
