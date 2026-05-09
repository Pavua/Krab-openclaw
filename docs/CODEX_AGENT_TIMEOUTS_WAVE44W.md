# Wave 44-W: Codex Agent Stagnation Detector

## Проблема

Wave 44-V (commit `25aad94`) ввёл per-account quota detection и rotation,
но контроль времени остался жёстким: `proc.communicate(timeout=timeout_sec)`
ждёт полный output ИЛИ убивает после фиксированного wall-clock.

Сценарий боли:
- codex hangs на 100s без output → ждём оставшиеся ~1700s до kill (30 min default)
- codex реально работает (chunks/tool_calls progress) → убиваем через 30 min даже если задача длинная

User feedback (pavua):
> «Если codex реально работает (chunks/tool_calls progress) — готов ждать дольше; если codex просто висит — fallback быстрее».

## Решение

Заменили `proc.communicate(timeout=...)` на streaming reader с двумя порогами:

1. **`idle_timeout_sec`** — primary detector. Любой byte в stdout/stderr resets clock. Если gap > порога → kill + `LLMRetryableError("stagnation_timeout")`.
2. **`hard_cap_sec`** — fallback. Total elapsed wall-clock; даже если codex эмитит byte раз в N секунд бесконечно. `0` = disabled.

Реализация: `src/integrations/cli_subprocess_bypass.py::_stream_with_stagnation()`.

## Конфигурация

| Env var | Default | Назначение |
|---|---|---|
| `KRAB_LLM_IDLE_TIMEOUT_SEC` | `180` | Idle gap между output bytes до kill (стагнация) |
| `KRAB_CODEX_AGENT_HARD_CAP_SEC` | `7200` | Soft hard-cap на codex agent run. `0` = disabled (полагаемся только на stagnation detector) |
| `KRAB_LLM_WALL_CLOCK_CAP_SEC` | `600` | Глобальный wall-clock cap LLM flow (не codex-specific). Wave 44-W не трогаем — отдельный концепт. |

## Migration

Если у вас были custom timeout настройки для codex:

| Old | New | Комментарий |
|---|---|---|
| `KRAB_LLM_WALL_CLOCK_CAP_SEC=1800` | `KRAB_CODEX_AGENT_HARD_CAP_SEC=7200` + `KRAB_LLM_IDLE_TIMEOUT_SEC=180` | Stagnation primary, hard cap fallback |

Recommended values для long agent tasks:
```env
KRAB_LLM_IDLE_TIMEOUT_SEC=300        # 5 min idle = hang
KRAB_CODEX_AGENT_HARD_CAP_SEC=0      # disable (rely on stagnation)
```

## Quota Detection Priority

Wave 44-V quota patterns имеют приоритет over stagnation. Если в stderr матчится паттерн (`weekly limit`, `rate limit`, etc.) — `_stream_with_stagnation` выходит без `kill()`, оставляя обработку для `_complete_codex_with_account_rotation` (account rotation + cooldown).

Stagnation timeout не false-trigger когда quota error возвращается быстро.

## Backwards Compatibility

- Gemini path (non-codex CLI) — тот же streaming reader с `idle_timeout` + `hard_cap=timeout_sec` (preserve legacy contract).
- Tests с `proc.communicate` mocks работают через legacy fallback (detect non-awaitable `readline` → fall back на `communicate` с `asyncio.wait_for`).
- `LLMRetryableError` для stagnation/hard_cap → caller `_run_llm_request_flow` уже умеет fallback на следующую модель chain (existing retry logic).

## Tests

`tests/unit/test_codex_stagnation_wave44w.py` — 12 cases:
1. Streaming с активностью → success без kill
2. Idle timeout → `LLMRetryableError("stagnation")`
3. Hard cap → `LLMRetryableError("hard cap")`
4. Quota detection mid-stream → bail без kill
5-7. Env config (idle timeout default/override/invalid)
8-10. Env config (hard cap default/zero/custom)
11. Legacy communicate-mock fallback
12. `_run_codex_subprocess_once` integration → propagates LLMRetryableError
