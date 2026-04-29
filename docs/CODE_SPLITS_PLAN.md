# Code Splits Plan — command_handlers.py + web_app.py

> Подготовлено в Session 24 (2026-04-26) sub-agent feature-dev:code-architect.
> P2 architectural задача — требует dedicated session с test discipline.

## Цели

- **command_handlers.py** (19,637 LOC, 175+ команд) → 11 модулей в `src/handlers/commands/`
- **web_app.py** (15,822 LOC, 249 endpoints) → 12 routers в `src/modules/web_app/`
- Применить mixin-паттерн от `src/userbot/` (USERBOT_BRIDGE_SPLIT_PROPOSAL.md)
- FastAPI APIRouter + `RouterContext` dataclass для DI

## Domain map

### command_handlers.py → 11 модулей

| Модуль | LOC | Команды |
|---|---|---|
| `_shared.py` | ~500 | `_reply_tech`, `_split_text_for_telegram`, `_parse_toggle_arg` |
| `text_utils.py` | ~2,000 | calc, eval, b64, hash, json, sed, diff, regex, translate, define, urban |
| `chat_commands.py` | ~900 | grep, context, collect, top, history, chatinfo, whois, fwd, monitor, log |
| `social_commands.py` | ~2,000 | pin, unpin, del, purge, afk, welcome, chatmute, slowmode, spam, react, chatban, poll, quiz, invite, blocked, contacts, members, who, archive |
| `memory_commands.py` | ~1,500 | remember, recall, memory, mem, chado, filter, memo, bookmark |
| `scheduler_commands.py` | ~1,500 | remind, schedule, timer, stopwatch, todo + module state (`_active_timers`) |
| `voice_commands.py` | ~1,200 | voice, tts, audio_message |
| `swarm_commands.py` | ~3,000 | swarm (entire) + `_AgentRoomRouterAdapter` |
| `translator_commands.py` | ~2,000 | translator (all subcommands) |
| `ai_commands.py` | ~2,500 | ask, search, agent, rate, explain, fix, rewrite, summary, catchup, report |
| `system_commands.py` | ~3,000 | health, stats, model, config, debug, diagnose, costs, inbox, notify, silence, restart |

### web_app.py → 12 routers

| Router | LOC | Endpoints |
|---|---|---|
| `health_router.py` | ~1,200 | `/api/health*`, `/api/uptime`, `/api/version`, `/api/system/*`, `/api/endpoints`, `/api/sla` |
| `openclaw_model_router.py` | ~2,500 | `/api/model/*`, `/api/thinking/*`, `/api/depth/*`, `/api/openclaw/model-*`, `/api/openclaw/routing/*` |
| `openclaw_runtime_router.py` | ~2,000 | `/api/openclaw/runtime-config`, `/api/openclaw/cron/*`, `/api/openclaw/cloud/*`, `/api/runtime/*`, `/api/context/*` |
| `swarm_router.py` | ~1,200 | `/api/swarm/*` (20+) |
| `translator_router.py` | ~1,500 | `/api/translator/*` (25+) |
| `voice_router.py` | ~500 | `/api/voice/*`, `/api/transcriber/*`, `/api/krab_ear/*` |
| `memory_router.py` | ~400 | `/api/memory/*` (search, stats, indexer, heatmap, doctor) |
| `ops_router.py` | ~1,500 | `/api/ops/*`, `/api/costs/*`, `/api/dashboard/summary` |
| `ecosystem_router.py` | ~800 | `/api/ecosystem/*`, `/api/capabilities/*`, `/api/policy/*`, `/api/userbot/acl/*` |
| `inbox_router.py` | ~600 | `/api/inbox/*`, `/api/provisioning/*`, `/api/queue` |
| `assistant_browser_router.py` | ~600 | `/api/assistant/*`, `/api/browser/*`, `/api/chrome/*`, `/api/openclaw/browser*` |
| `misc_router.py` | ~400 | прочие endpoints |

## Target structure

```
src/handlers/
  __init__.py           (re-exports register_handlers — backward compat)
  command_handlers.py   (loader/dispatcher + register_handlers, ~400 LOC)
  commands/
    __init__.py
    _shared.py
    ai_commands.py
    chat_commands.py
    memory_commands.py
    scheduler_commands.py
    social_commands.py
    swarm_commands.py
    system_commands.py
    text_utils.py
    translator_commands.py
    voice_commands.py

src/modules/
  __init__.py
  web_app.py            (WebApp class + middleware + _setup_routes loader, ~600 LOC)
  web_app/
    __init__.py
    _context.py         (RouterContext dataclass)
    _helpers.py         (promoted @classmethod helpers)
    health_router.py
    openclaw_model_router.py
    openclaw_runtime_router.py
    swarm_router.py
    translator_router.py
    voice_router.py
    memory_router.py
    ops_router.py
    ecosystem_router.py
    inbox_router.py
    assistant_browser_router.py
    misc_router.py
```

## Architectural decisions

**command_handlers**: pure function extraction, без classes. `register_handlers(bot)` импортирует из `commands.*` и wires Pyrogram decorators. Module-level state (`_active_timers`) остаётся в `scheduler_commands.py`, экспортируется через accessor.

**web_app**: APIRouter modules + `RouterContext` dataclass:

```python
@dataclass
class RouterContext:
    deps: dict
    project_root: Path
    web_api_key_fn: Callable[[], str]
    assert_write_access_fn: Callable[...]
    rate_state: dict           # mutable, shared
    idempotency_state: dict    # mutable, shared
```

`@classmethod` helpers без `self.deps` промотятся в `_helpers.py`.

## Build sequence (6 sessions)

| Phase | Sessions | Risk | Что |
|---|---|---|---|
| **1** | 0.5 | LOW | Scaffold dirs, `_shared.py`, `_context.py`, `_helpers.py`. Snapshot `/api/endpoints` + `/api/commands`. Pytest baseline. |
| **2a** | 0.5 | LOW | `health_router`, `memory_router`, `voice_router`. Pytest + endpoint snapshot diff = 0. |
| **2b** | 0.5 | LOW | `text_utils.py`, `chat_commands.py`. Pytest. |
| **3a** | 1.0 | MED | `swarm_router`, `inbox_router`, `translator_router`. `memory_commands`, `scheduler_commands`. |
| **3b** | 0.5 | MED | `ops_router`, `ecosystem_router`. `social_commands`, `voice_commands`. |
| **4a** | 1.0 | HIGH | `assistant_browser_router`. `translator_commands`, `ai_commands`. |
| **4b** | 1.0 | HIGH | `openclaw_model_router`, `openclaw_runtime_router` (вместе — share `_apply_openclaw_runtime_controls`). `system_commands`. |
| **4c** | 0.5 | HIGH | `swarm_commands` (последним — самый сложный, 1,200 LOC handler). `misc_router`. |
| **5** | 0.5 | LOW | Удалить extracted code из original files. Verify target LOC. Final pytest + snapshot pass. |

**Total: 6 session-days.**

## Suggested order (low → high risk)

**Старт (low-coupling):**
1. `text_utils.py` (zero bot state, safe_eval self-contained)
2. `health_router.py` (read-only, simple deps)
3. `memory_router.py` (singleton access)

**Mid-session:**
4. `chat_commands.py`, `social_commands.py` (Pyrogram only)
5. `scheduler_commands.py` (owns own state, extract as unit)
6. `swarm_router.py`, `voice_router.py` (clean singletons)

**Последним (high-coupling):**
7. `openclaw_model_router.py` + `openclaw_runtime_router.py` (вместе — общий helper)
8. `system_commands.py` (читает `_active_timers` из scheduler)
9. `swarm_commands.py` (~1,200 LOC handler с 15+ sub-branches; возможно intermediate split на core+admin)

## Test discipline

Перед каждым extraction:
- `pytest tests/unit/ -q` baseline pass
- Snapshot `/api/endpoints` (JSON array) → fixture
- Snapshot `/api/commands` (JSON array) → fixture
- Snapshot `!help` output → fixture

После extraction:
- `pytest tests/unit/ -q` green
- Endpoint diff = 0 (byte-identical)
- Command diff = 0
- `ruff check src/` clean

## Critical risks

1. **Circular imports** — `commands/_shared.py` НЕ импортирует из `commands/<domain>.py`.
2. **Module-level state** (`_active_timers` в scheduler) — используется в `handle_debug` (system). Решение: extract scheduler первым, expose `get_active_timer_count()`.
3. **`self.deps` access** — `@classmethod` chain в WebApp требует промоции `cls.*` → direct function calls (~40 call sites).
4. **`include_router` ordering** — FastAPI matches in registration order, preserve exactly.
5. **Pyrogram `TYPE_CHECKING` guard** — `from ..userbot_bridge import KraabUserbot` за `if TYPE_CHECKING:` в каждом домен-модуле.
6. **Route path conflicts** — `/api/swarm/task/{task_id}` GET+DELETE → отдельные `@router.get` + `@router.delete` декораторы.

## Detailed source

См. полный отчёт sub-agent в Session 24 transcript (тот же агент: `ae1d2c8c76d1d6db8`). Включает Phase-by-phase implementation map, RouterContext data flow, и build sequence checklist.
