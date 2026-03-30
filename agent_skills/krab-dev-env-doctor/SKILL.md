---
name: krab-dev-env-doctor
description: "Диагностировать и безопасно bootstrap-ить developer environment проекта `/Users/pablito/Antigravity_AGENTS/Краб` на текущей macOS-учётке: проверить CLI, Python-зависимости, каталоги `~/.codex` / `~/.claude`, доступность repo-level sync и при необходимости подготовить Codex/Claude skills без записи в `~/.openclaw`. Использовать на новой учётке, после долгого перерыва, перед первым запуском Codex/Claude или когда dev-инструменты начинают вести себя нестабильно."
---

# Krab Dev Env Doctor

Используй этот навык, когда проблема ещё не в коде Краба, а в самой developer-среде вокруг него.

Он помогает понять, чего не хватает для нормальной работы Codex/Claude, и безопасно довести среду до минимально рабочего состояния.

## Когда использовать

- новая учётка `USER2`, `USER3` или `pablito`;
- после обновления Python/CLI;
- если `quick_validate.py`, sync skills или локальный pytest внезапно падают;
- если нужно быстро подготовить Codex/Claude перед рабочей задачей.

## Канонический инструмент

- repo script: `/Users/pablito/Antigravity_AGENTS/Краб/scripts/bootstrap_krab_dev_tools.py`
- one-click launcher: `/Users/pablito/Antigravity_AGENTS/Краб/Bootstrap Krab Dev Tools.command`

## Рабочий цикл

1. Сними doctor-report без изменений:
   - `python3 scripts/bootstrap_krab_dev_tools.py --doctor-only`
2. Посмотри на блокеры:
   - missing CLI;
   - missing Python packages;
   - отсутствие sync scripts или проблемные каталоги.
3. Если среда в целом здорова, но не хватает минимальных пакетов, запусти:
   - `python3 scripts/bootstrap_krab_dev_tools.py --install-python-deps`
4. Если нужен сразу рабочий профиль skills, запусти bootstrap с профилем:
   - `python3 scripts/bootstrap_krab_dev_tools.py --profile dev-tools --install-python-deps`
5. Для one-click сценария используй `.command` launcher.

## Что он проверяет

- `python3`, `git`, `rg` как обязательный минимум;
- `gh`, `claude`, `codex`, `node`, `npx`, `openclaw` как полезные дополнительные CLI;
- `PyYAML` и `pytest`;
- каталоги `~/.codex/skills`, `~/.claude/krab-agents`, наличие sync scripts;
- что bootstrap/sync не пишут в `~/.openclaw`.

## Красные флаги

- Не использовать этот bootstrap как runtime installer.
- Не считать отсутствие `openclaw` или `claude` фатальной ошибкой для обычного Codex coding loop.
- Не пытаться лечить runtime-проблемы через dev bootstrap.

## Рекомендуемые связки с другими skills

- `krab-dev-session-bootstrapper` в самом начале новой сессии.
- `krab-multi-account-dev-coordinator`, если учётка вспомогательная.
- `krab-agent-request-router`, если нужно понять, какой режим и skill брать дальше.

## Ресурсы

- Bootstrap matrix: `references/dev-env-bootstrap-matrix.md`
- Быстрый чеклист новой учётки: `assets/dev-env-doctor-checklist.md`
