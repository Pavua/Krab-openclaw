# Dev Env Doctor Checklist

- Сначала запусти doctor-only.
- Проверь, какие CLI реально отсутствуют.
- Если не хватает только `PyYAML` и `pytest`, используй bootstrap с `--install-python-deps`.
- После bootstrap проверь, что skills появились в `~/.codex/skills` и `~/.claude/krab-agents`.
- Не интерпретируй этот процесс как установку чего-либо в OpenClaw runtime.
