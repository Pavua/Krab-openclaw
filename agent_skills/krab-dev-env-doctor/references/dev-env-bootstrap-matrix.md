# Dev Env Bootstrap Matrix

## Что считать минимумом

| Категория | Обязательно | Желательно |
| --- | --- | --- |
| CLI | `python3`, `git`, `rg` | `gh`, `claude`, `codex`, `node`, `npx` |
| Python | `PyYAML`, `pytest` | локальная `.venv`, если команда проекта на неё опирается |
| Каталоги | `~/.codex/skills`, `~/.claude/krab-agents` | `~/.openclaw` только как read-only сигнал существования runtime слоя |

## Что делает bootstrap

- проверяет базовые CLI;
- проверяет минимальные Python-пакеты;
- может установить недостающие `PyYAML` и `pytest`;
- синхронизирует repo-level skills в Codex и Claude pack;
- не пишет в `~/.openclaw`.
