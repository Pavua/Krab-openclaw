---
name: krab-openclaw-auth-models-keeper
description: "Проверять, синхронизировать и чинить `auth-profiles.json`, `models.json`, runtime registry и связанные OpenClaw-конфиги в проекте `/Users/pablito/Antigravity_AGENTS/Краб`. Использовать, когда расходятся auth/models truth, ломается cloud/local routing из-за неверного каталога, UI показывает не тот runtime registry или нужно безопасно привести OpenClaw auth/models в согласованное состояние."
---

# Krab Openclaw Auth Models Keeper

Используй этот навык для узкого контура `auth + models + registry`. Его задача не “чинить всё”, а приводить runtime auth/models truth в согласованное состояние.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Основные файлы

- `~/.openclaw/agents/main/agent/models.json`
- `~/.openclaw/agents/main/agent/auth-profiles.json`
- `~/.openclaw/openclaw.json`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/sync_openclaw_models.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/openclaw_model_registry_sync.py`

## Рабочий цикл

1. Считать текущее состояние auth/models/runtime registry.
2. Понять, это drift, missing token, неверный provider catalog или broken model id.
3. Запустить dry-run или read-only диагностику, если это возможно.
4. Применить минимальный repair через существующие скрипты.
5. Перепроверить итог через runtime status и релевантные unit tests.

## Полезные команды

```bash
python3 scripts/sync_openclaw_models.py
python3 scripts/openclaw_model_registry_sync.py
python3 scripts/openclaw_model_compat_probe.py
pytest tests/unit/test_openclaw_model_registry_sync.py -q
pytest tests/unit/test_openclaw_model_compat_probe.py -q
pytest tests/unit/test_openclaw_runtime_repair.py -q
```

## Ограничения

- Не подменять runtime truth repo-level ожиданиями.
- Не считать токен “валидным”, если он просто не пустой.
- После repair проверять не только файл, но и поведение runtime/UI.
