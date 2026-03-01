# Technical Addendum: Phase 9+ Implementation Rules

## Stage A: Safety First
- **Centralized Stop Propagation**: In `userbot_bridge.py`, the `run_cmd` wrapper MUST use a `finally` block to call `m.stop_propagation()`. This is the primary protection against double responses.
- **Guard Logic**: The early return guard in `_process_message` should only trigger if the command is found in `self._known_commands` to avoid blocking regular messages starting with prefixes.

## Stage B: Stability & Memory
- **Model Locking**: Initialize `self._lock = asyncio.Lock()` in `ModelManager.__init__`. Every call to `load_model` or `unload_model` must be wrapped in `async with self._lock:`.
- **VRAM Cooling**: After a successful `unload` or `free_vram()` call, always execute `await asyncio.sleep(1.5)` before the next load command to ensure the GPU driver has released the memory.
- **API Resilience**: Use the fallback chain for LM Studio endpoints: try `/api/v1/models/load` first, then `/v1/models/load`.
- **Vision Integrity**: Ensure `openclaw_client.py` does not strip the `images` array from the payload when falling back to direct LM Studio calls.

## Stage C: State & Paths
- **Persistence**: Save the routing mode in a file defined by `config.RUNTIME_STATE_PATH` (or similar absolute path) to ensure it survives restarts.