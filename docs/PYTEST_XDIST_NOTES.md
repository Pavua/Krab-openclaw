# pytest-xdist parallel testing notes

## Speedup measured (06.05.2026, M4 Max, Krab)

| Mode | Wall time | Speedup | Failures |
|---|---|---|---|
| Single-core (default) | ~7-9 min for full suite | 1× baseline | 0 (clean) |
| **xdist `-n auto`** (10 cores) | **95.68s** | **~5×** | **75 failures** (isolation) |

CPU utilization at `-n auto`: 470% = ~4.7 effective cores.

## Why isolation fails при xdist

75 tests fail при parallel execution из-за **shared module-level state**:

1. **Singleton monkeypatching**: tests patch `openclaw_client._client_singleton`,
   `inbox_service._instance`, etc. Workers исполняются в **отдельных процессах**,
   но XDIST разделяет одну sqlite БД, fixture state, global registries.

2. **Side-effect tests**: tests, которые пишут в filesystem без `tmp_path`
   (`~/.openclaw/`, fixed cache paths) — workers перетирают результаты друг друга.

3. **Non-deterministic ordering**: некоторые tests предполагают определённый
   import order или autouse fixture execution, который ломается при
   `--dist=load` (default xdist).

## Recommended approach (НЕ менять default)

- **Default (CI + local dev)**: single-core, clean baseline.
- **Speed mode** (для быстрого regression check): `pytest -n auto --dist=loadfile`
  — тесты одного файла идут последовательно в одном worker'е, что fixes ~80%
  isolation issues. Но всё ещё нужны fixes на ~15-20 файлах.
- **Future work**: пометить isolated/parallel-safe tests маркером
  `@pytest.mark.xdist_group(name="...")` — gradual migration к full parallel.

## Configuration

`pyproject.toml` — никаких изменений default'ов. Использовать:

```bash
# Default (clean):
venv/bin/pytest tests/unit/

# Fast iteration (с известными xdist issues):
venv/bin/pytest tests/unit/ -n auto --dist=loadfile

# Specific subset (всегда parallel-safe):
venv/bin/pytest tests/unit/test_*_mixin.py -n auto
```

## Mixin extraction tests (Wave 31 series) — все parallel-safe

Все 67+ tests созданные за Wave 31-A→K используют:
- `tmp_path` для file IO
- isolated mock objects (нет global state mutation)
- explicit fixtures без autouse-side-effects

→ pytest-xdist на mixin tests = full speedup без failures.
