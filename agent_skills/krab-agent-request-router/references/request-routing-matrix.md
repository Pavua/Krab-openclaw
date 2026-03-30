# Request Routing Matrix

## По типу запроса

| Тип запроса | Стартовый skill-stack | Учётка по умолчанию |
| --- | --- | --- |
| Новый чат / новая учётка / install | `krab-dev-session-bootstrapper` + `krab-dev-env-doctor` | `USER2` / `USER3` / `pablito` |
| Multi-account / drift / shared repo | `krab-multi-account-dev-coordinator` + `krab-shared-repo-drift-reconciler` | helper-учётка, затем `pablito` при необходимости |
| Крупная feature-задача | `krab-role-split-delivery` + `krab-parallel-agent-orchestrator` + `krab-branch-handoff-governor` | helper-учётка для code, `pablito` для финала |
| Runtime prompt/auth | `krab-openclaw-workspace-prompt-curator` или `krab-provider-auth-relogin-coordinator` | чаще `pablito` |
| Smoke / acceptance / handoff | предметный skill + `krab-artifact-freshness-auditor` + `krab-live-acceptance-brief-writer` | helper или `pablito` по критичности |

## По режиму работы

- `code-only` — код, tests, docs, skills, launchers.
- `dev-admin` — sync, install, shared repo tooling, doctor/bootstrap.
- `runtime-admin` — prompt/auth/runtime truth только при ясном ownership.
