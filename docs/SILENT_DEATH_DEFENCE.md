# Silent Death Defence — 3-Layer Architecture

Silent death = Pyrogram client appears alive (connection open, no exception,
heartbeat OK), but internal dispatcher has stopped delivering updates. The
classic split-brain failure mode: TCP connection holds, MTProto session is
valid, but `dispatcher.handle_updates()` is stuck and no `Message` events fire.

Krab uses **3 independent probes** to detect and recover from this state.
Each probe represents one iteration of the "outcomes-not-heartbeats" pattern.

## Layer 1 — Wave 63-A: `updates.GetState` pts probe

Compares server `pts` against local `_last_seen_update_id`. If server has
advanced but local has not, we have a split-brain.

- Hot path: `_telegram_heartbeat_loop` calls `invoke(GetState)` every cycle
- Detection latency: ~4 minutes (down from 93 min before this wave)
- Action: trigger `_try_reconnect_pyrofork`

| Env var | Default | Purpose |
|---|---|---|
| `KRAB_HEARTBEAT_GET_STATE_PROBE_ENABLED` | `1` | Master switch |
| `KRAB_HEARTBEAT_GET_STATE_TIMEOUT_SEC` | `8.0` | Invoke timeout |

## Layer 2 — Wave 63-C: `dispatcher_tick` monotonic counter

The Pyrofork dispatcher loop increments an internal counter every time it
processes an update batch. We expose this counter via
`/api/network/probes` as `dispatcher_tick`.

- Detection: tick frozen for `KRAB_DISPATCHER_TICK_STALENESS_SEC` while
  server has pending updates → silent death
- Implementation: read `client.dispatcher._tick` (Pyrofork internal)
- Action: `_attempt_dispatcher_recovery` (re-binds handlers, fake update)

| Env var | Default | Purpose |
|---|---|---|
| `KRAB_DISPATCHER_RECOVERY_ENABLED` | `1` | Master switch |
| `KRAB_DISPATCHER_TICK_STALENESS_SEC` | `120` | Tick staleness threshold |
| `KRAB_DISPATCHER_FAKE_UPDATE_COUNT` | `2` | Fake updates to confirm recovery |

## Layer 3 — S53 P3.6 hotfix3: `Client.last_update_time`

The most reliable signal. Pyrofork sets `client.last_update_time = time.time()`
on **every** received update (see `pyrogram/client.py:628`). If this timestamp
is older than threshold while peers know we are online, the dispatcher is
silently stuck.

- This is the 4th iteration of "outcomes-not-heartbeats" (after Wave 63-A,
  Wave 50-B OAuth refresh, Wave 65-D anthropic-vertex preempt, Wave 62-G
  codex-cli preempt)
- Pyrofork internals: `dispatcher.py:393-406` is the loop that updates
  `last_update_time` after each successful batch
- Exposed via `/api/network/probes` as `pyrogram_last_update_age_sec`

| Env var | Default | Purpose |
|---|---|---|
| `KRAB_LAST_UPDATE_AGE_PROBE_ENABLED` | `1` | Master switch |
| `KRAB_LAST_UPDATE_AGE_THRESHOLD_SEC` | `180` | Age threshold for stuck-detect |

## Recovery chain

```
[Layer 1/2/3 fires] → silent death detected
    │
    ├─► _attempt_dispatcher_recovery()
    │       ├─► re-bind on_message handlers
    │       ├─► dispatch 2 fake updates (verify ticks increment)
    │       └─► verify Client.last_update_time advanced
    │
    ├─► success ? exit recovery, log dispatcher_recovery_ok
    │   failure ↓
    │
    ├─► _try_reconnect_pyrofork()
    │       ├─► disconnect MTProto cleanly
    │       ├─► re-create Client
    │       └─► reconnect + resume session
    │
    ├─► success ? exit, log reconnect_ok
    │   failure ↓
    │
    └─► _launchd_exit_78
            launchctl kickstart ai.krab.core → fresh process
```

## Production incident — 2026-05-17 (S53)

Timeline:

| Time | Event |
|---|---|
| 17:29 | Last `Client.last_update_time` advance recorded |
| 17:31 | `pyrogram_last_update_age_sec` crossed 180s threshold |
| 17:31 | Layer 3 fired → `_attempt_dispatcher_recovery` |
| 17:31 | Fake updates dispatched; ticks did **not** increment → failure |
| 17:32 | Escalated to `_try_reconnect_pyrofork` → succeeded |
| 17:32 → 19:24 | Stable; no further silent-death triggers |
| 19:24 | Routine restart for S55 D deploy |

Layer 1 (pts probe) and Layer 2 (dispatcher_tick) did **not** fire during
this incident — both reported healthy. Only Layer 3's `last_update_time`
caught the stall. Confirms why all 3 layers are needed: each catches a
different failure mode.

## Operational runbook

### Identify silent death

```bash
curl -sS http://127.0.0.1:8080/api/network/probes | python3 -m json.tool
```

Look for:

- `pyrogram_last_update_age_sec` > 180 → Layer 3 should have fired
- `dispatcher_tick_age_sec` > 120 → Layer 2 should have fired
- `pts_gap` > 0 → Layer 1 should have fired

If any are elevated **and** no recovery markers in the log, recovery is broken:

```bash
LOG=~/.openclaw/krab_runtime_state/krab_main.log
grep -E "dispatcher_recovery|reconnect_ok|_launchd_exit_78" $LOG | tail -20
```

### Force manual recovery

```bash
# Option A: kick the launchd job (escalation path)
launchctl kickstart -k gui/$UID/ai.krab.core

# Option B: full restart
/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command
sleep 5
/Users/pablito/Antigravity_AGENTS/new\ start_krab.command
```

## See also

- `src/userbot_bridge.py` — `_telegram_heartbeat_loop`, `_attempt_dispatcher_recovery`
- `src/userbot/runtime_status.py` — `/api/network/probes` payload
- Commits `30d7755`, `817f485`, `f1bcf84`, `75b74b3` — P3.6 + 3 hotfixes (S53)
- `pyrogram/client.py:628` — `last_update_time` write site
- `pyrogram/dispatcher.py:393-406` — dispatcher update loop
- CLAUDE.md "outcomes-not-heartbeats" — Wave 63-A / 50-B / 65-D / 62-G context
