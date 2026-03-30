# Session Bootstrap Matrix

## Когда какой режим выбирать

| Ситуация | Режим | Что можно | Что нельзя |
| --- | --- | --- | --- |
| Новый чат на `USER2` или `USER3`, задача про код/доки/skills | `code-only` | править repo, тесты, docs, launchers, repo-level skills | трогать `~/.openclaw`, OAuth и live runtime |
| Нужно синхронизировать skills для Codex/Claude | `dev-admin` | запускать `sync_krab_agent_skills.py`, обновлять install docs | считать это установкой в OpenClaw runtime |
| Задача про prompt/auth/runtime truth и ownership понятен | `runtime-admin` | работать с runtime-слоем через Codex/Claude | лезть в чужую учётку или смешивать account-local state |
| Ветка грязная, ownership спорный или задача неясна | `handoff-only` | собрать truthful summary, зафиксировать ограничения | делать рискованные правки наугад |

## Минимальный набор сигналов

- `whoami`
- текущая ветка и `git status --short`
- нужен ли только dev-layer или ещё runtime
- какой профиль skills уже поставлен
- кто отвечает за финальный acceptance verdict
