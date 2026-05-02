# SkillCurator Design — cherry-picked from Hermes (Wave 14-C)

**Status**: design only, no code. Session 33, branch `claude/optimistic-ptolemy-e98bdf`.
**Source**: `/Users/pablito/Antigravity_AGENTS/hermes-agent-eval/agent/curator.py` (Phase 1 dry-run finding).

## 1. Hermes Curator — что копируем

Curator у Hermes — фоновый «библиотекарь скиллов»: запускается **inactivity-triggered** (нет cron-демона) — когда агент простаивает и `last_run_at` старше `interval_hours` (default 7 дней), `maybe_run_curator()` форкает aux-агента, который читает `skill_usage` и:

- **Auto-transitions** (pure, no-LLM): `active → stale` через 30 дней без активности; `stale → archived` через 90; reactivate если использовался снова. Pinned skills never touched.
- **LLM review pass**: aux-модель читает skill library, ищет **prefix clusters** и консолидирует в **umbrella skills**, затем archive (не delete) поглощённых.
- **Hard invariants**: only agent-created skills, archive-only (recoverable), pinned bypass, dry-run mode выдаёт structured YAML без мутаций.
- **State storage**: `~/.hermes/skills/.curator_state` (atomic write), `last_run_at / paused / run_count / last_report_path`.
- **Reports**: `~/.hermes/logs/curator/{YYYYMMDD-HHMMSS}/run.json + REPORT.md`, removed-skills classified `consolidated` vs `pruned` через анализ tool-calls.

**Ключевой инсайт**: Curator не «улучшает скиллы» автономно — он **куратит коллекцию** (merge/archive/promote) и **форкает aux-агента** для LLM-решений. Все мутации archive-recoverable, есть dry-run + approval gate.

## 2. Krab adaptation — концепт

У Краба нет «skill library» в Hermes-смысле, но есть **аналог: swarm team prompts + trajectories**.

| Hermes | Krab |
|---|---|
| Skill library `~/.hermes/skills/` | `src/core/swarm_team_prompts.py` (4 команды × системный промпт) |
| Trajectory JSONL | `swarm_memory.py` (FIFO 50/team) + `swarm_artifact_store` |
| Auto-transitions (state) | Prompt versioning + rollback |
| LLM review (aux agent) | Aux-Gemini-3-flash анализирует last 50 rounds → предлагает diff |
| Archive | Старая версия промпта в `~/.openclaw/krab_runtime_state/curator/prompts_archive/` |
| Pinned | `prompt.locked = true` в metadata |
| Dry-run | `!curator dry-run <team>` → отчёт без мутаций |

## 3. Krab Curator — concrete design

### Файлы
- `src/core/skill_curator.py` (~400 LOC) — orchestrator
- `src/core/skill_curator_state.py` — atomic JSON store (`~/.openclaw/krab_runtime_state/curator/state.json`)
- `src/core/skill_curator_report.py` — report renderer (markdown + structured YAML)
- `src/handlers/commands/curator_commands.py` — `!curator [status|dry-run|run|approve|rollback]`
- API: `GET /api/curator/state`, `POST /api/curator/dry-run`, `POST /api/curator/apply`, `POST /api/curator/rollback`

### Класс
```python
@dataclass
class CuratorReport:
    team: str
    rounds_analyzed: int
    success_rate: float          # из reactions/follow-ups
    failure_patterns: list[str]  # топ-3 повторяющихся симптома
    successful_patterns: list[str]
    proposed_prompt: str         # diff vs current
    metric_delta_estimate: dict  # cost, latency, success_rate
    confidence: float            # 0..1, gate для auto-apply

class SkillCurator:
    async def analyze_recent_rounds(team: str, days: int = 7) -> CuratorReport
    async def propose_prompt_update(team: str, report: CuratorReport) -> str
    async def apply_with_approval(team: str, new_prompt: str, *, force: bool=False) -> bool
    async def rollback(team: str, version: int = -1) -> bool
```

### Storage schema
```
~/.openclaw/krab_runtime_state/curator/
├── state.json                    # {last_run_at, paused, run_count, locked_teams[]}
├── prompts_archive/
│   └── {team}/v{N}_{timestamp}.md   # все версии (recoverable)
├── reports/
│   └── {YYYYMMDD-HHMMSS}/{team}.md
└── ab_tests/
    └── {team}_{run_id}.json      # control vs candidate metrics
```

### Triggers
1. **Cron** (preferred, как в нашей экосистеме): weekly `Sunday 03:00`, через `cron_native_scheduler`. Не «inactivity-driven» как Hermes — у нас сложно определить idle.
2. **Manual**: `!curator run <team>`.
3. **Threshold**: после каждых 100 swarm-rounds команды → авто-dry-run (только отчёт, без apply).

### Approval flow
```
trigger → analyze_recent_rounds → CuratorReport
       → если confidence < 0.7  → отчёт в Saved Messages, ждём !curator approve
       → если confidence >= 0.7 → A/B test (см. §4)
       → если A/B win           → apply (archive old prompt, write new)
       → если A/B lose          → rollback автоматом, лог в report
```

## 4. A/B testing framework

- **Setup**: следующие N rounds (N=10) команда чередует control prompt и candidate prompt по round-robin (seed = round_id).
- **Metrics** (per round): `cost_usd`, `latency_s`, `tool_calls_count`, `user_reaction_score` (👍/👎 на финальный artifact), `verifier_pass` (через `swarm_verifier`).
- **Decision** (после N=10): candidate wins если `success_rate ≥ control + 0.05` AND `cost ≤ control * 1.10` AND `latency ≤ control * 1.10`.
- **Storage**: `ab_tests/{team}_{run_id}.json` для повторного анализа.
- **Manual override**: `!curator approve <team>` пропускает A/B и применяет немедленно (для срочных правок).

## 5. Implementation roadmap (3-5 sessions)

1. **Session 33 (this)** — design doc only, no code. ✅
2. **Session 34** — `skill_curator_state.py` + `skill_curator_report.py` + `!curator status/dry-run` (read-only path), API endpoints. Без LLM-анализа: пока только статистика по `swarm_memory`.
3. **Session 35** — LLM-анализатор: `analyze_recent_rounds` через aux Gemini-3-flash, `propose_prompt_update`. Cron weekly. Manual `!curator run` пишет отчёт в Saved Messages.
4. **Session 36** — A/B framework: round-robin selector в `swarm.py`, метрики в `swarm_artifact_store`, decision logic.
5. **Session 37** — auto-apply gate (confidence + A/B win), `!curator approve/rollback`, archive cleanup. Default OFF за env-флагом `KRAB_CURATOR_AUTO_APPLY=1`.

**Estimated effort**: 4 sessions × ~6h = 24h. Single most valuable feature first → see §7.

## 6. Risk register

| Риск | Митигация |
|---|---|
| **Prompt drift** — кумулятивные micro-changes деградируют качество | Hard limit: max 1 apply / week / team; rollback if `success_rate` падает 2 раза подряд |
| **A/B small-sample bias** (N=10 мало) | Decision требует ≥0.05 absolute delta; иначе keep control. Альтернатива: N=20 для high-stakes teams (traders) |
| **Aux-LLM hallucinates улучшения** | confidence < 0.7 → manual approval только; structured YAML diff обязателен |
| **Поглощение всех промптов в один umbrella** (в Hermes есть, у нас нет — n/a) | n/a, у нас 4 fixed teams |
| **Rollback не работает из-за corrupt archive** | atomic write через `tempfile + os.replace`, integrity check on read |
| **Бесконтрольный расход на aux-LLM** | rate limit: max 4 LLM calls / week (1/team); cost cap $0.50/run |
| **Curator меняет prompt во время активного раунда** | mutex per-team, отложить apply до `swarm.is_idle(team)` |

## 7. Backward compat

Existing `swarm_team_prompts.py` не трогается до первого `!curator approve`. До Session 35 — read-only path (status/dry-run отчёты). Auto-apply за env-флагом `KRAB_CURATOR_AUTO_APPLY=1`, default OFF навсегда (manual approval — primary mode).

Existing rounds, artifacts, memory схемы не меняются. Curator — pure overlay.

## 8. Most valuable single feature to implement first

**`!curator dry-run <team>` (Session 34)** — read-only анализ последних N rounds команды:
- success_rate из reactions/verifier
- топ-3 failure patterns (regex-кластеризация error messages)
- топ-3 successful patterns
- markdown отчёт в Saved Messages

Без LLM, без mutations, без A/B — но даёт **немедленную observability** «как работает каждая команда» и **очерчивает baseline** для последующих A/B test. 80% value за 20% effort.

---

**References**: Hermes `agent/curator.py` (curator orchestrator), `agent/trajectory.py` (JSONL save). Krab counterparts: `src/core/swarm.py`, `src/core/swarm_memory.py`, `src/core/swarm_team_prompts.py`, `src/core/swarm_verifier.py`, `src/core/swarm_artifact_store.py`.
