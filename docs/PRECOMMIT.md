# Pre-commit hooks

Hooks установлены в `.git/hooks/pre-commit`.

## Что проверяется

На каждый `git commit` для staged `.py` файлов в `src/`:

1. **ruff check** — линт с `--no-fix` (только проверка, без изменений файлов)
2. **ruff format --check** — проверка форматирования
3. **mypy** — только для файлов в `src/handlers/`, с `--follow-imports=skip --ignore-missing-imports`  
   (не тянет тяжёлые транзитивные зависимости pyrogram/pyrofork)

## Как исправить ошибки

```bash
# Исправить ruff
venv/bin/ruff check --fix src/
venv/bin/ruff format src/

# Посмотреть mypy ошибки вручную
venv/bin/mypy --ignore-missing-imports --follow-imports=skip src/handlers/
```

## Обходной путь

```bash
git commit --no-verify -m "msg"
```

Использовать только в крайних случаях (hotfix, WIP-коммит в worktree).

## Ручной запуск hook

```bash
bash .git/hooks/pre-commit
```

## Установка (если hook потерялся)

Hook хранится в `.git/hooks/pre-commit` и НЕ отслеживается git.  
Для переустановки запустить:

```bash
python3 scripts/install_hooks.py   # если скрипт создан
# или вручную: cp <backup> .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

## Mypy Strict Mode

Начиная с Session 15 mypy работает в **non-blocking** режиме по умолчанию —
pre-existing legacy ошибки в `src/handlers/command_handlers.py` не блокируют
commit. Для включения strict mode:

```bash
KRAB_PRECOMMIT_MYPY_STRICT=1 git commit -m "..."
```

Это полезно для новых файлов которые должны проходить strict type-check.
Старый код постепенно мигрируется — см. Session 20+ roadmap.
