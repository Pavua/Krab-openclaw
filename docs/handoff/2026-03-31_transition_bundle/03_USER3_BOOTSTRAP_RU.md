# USER3 Bootstrap

## Важное

Автоматический sync из-под `pablito` в `/Users/USER3/.codex/skills` упёрся в права доступа.
Это нормальная граница между macOS-учётками, а не баг текущего recovery-блока.

## Что запускать уже из-под USER3

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
python3 scripts/sync_krab_agent_skills.py --target-home /Users/USER3 --profile dev-tools
```

Если нужен только Codex layer:

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
python3 scripts/sync_krab_agent_skills.py --target-home /Users/USER3 --profile dev-tools --codex-only
```

## Что этот sync поставит

- `krab-agent-request-router`
- `krab-artifact-freshness-auditor`
- `krab-branch-handoff-governor`
- `krab-dev-env-doctor`
- `krab-dev-session-bootstrapper`
- `krab-live-acceptance-brief-writer`
- `krab-multi-account-dev-coordinator`
- `krab-parallel-agent-orchestrator`
- `krab-pr-review-triager`
- `krab-provider-auth-relogin-coordinator`
- `krab-release-readiness-pack`
- `krab-role-split-delivery`
- `krab-runtime-change-impact-auditor`
- `krab-shared-repo-drift-reconciler`
- `krab-shared-repo-permission-keeper`
- `krab-telegram-transport-regression-pack`

## Что проверить на USER3 после sync

1. Есть ли `~/.codex/skills/*`
2. Есть ли нужные MCP entries в `~/.codex/config.toml`
3. Открыт ли правильный branch:
   - `codex/telegram-runtime-recovery-handoff`
4. Прочитан ли bundle из этой папки

## Чего не делать

- Не копировать `~/.openclaw` между учётками
- Не перетирать `USER3` runtime/auth/browser state с `pablito`
- Не пытаться исправлять права в домашнем каталоге `USER3` из-под `pablito` силой
