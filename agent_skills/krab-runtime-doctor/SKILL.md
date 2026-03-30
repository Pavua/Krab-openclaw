---
name: krab-runtime-doctor
description: "Диагностировать и безопасно чинить runtime OpenClaw/Krab в проекте `/Users/pablito/Antigravity_AGENTS/Краб`. Использовать, когда нужно понять, почему не поднимается runtime, ломаются `:8080` или `:18789`, расходятся repo и `~/.openclaw/*`, падает gateway, ломается auth/models registry или требуется controlled repair с проверкой результата."
---

# Krab Runtime Doctor

Используй этот навык как основной режим правдивой диагностики runtime. Сначала снимай факт, потом чини, потом перепроверяй тем же контуром.

## Предусловие

Работай только если текущий проект:

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

Если `cwd` другой, сначала перейди в этот репозиторий.

## Источники истины

- `~/.openclaw/openclaw.json`
- `~/.openclaw/agents/main/agent/models.json`
- `~/.openclaw/agents/main/agent/auth-profiles.json`
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/OPENCLAW_KRAB_ROADMAP.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/ops_incident_runbook.md`

Repo-level документы описывают архитектуру и намерения. Runtime-истина живёт в `~/.openclaw/*`.

## Рабочий цикл

1. Снять снимок состояния через `scripts/runtime_snapshot.py` или `scripts/run_runtime_snapshot.command`.
2. Проверить health runtime, gateway, web endpoints и model/auth truth.
3. Сопоставить фактическую проблему с конфигом и логами, а не с догадкой.
4. Если repair нужен, запустить `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_runtime_repair.command` или `scripts/openclaw_runtime_repair.py`.
5. После изменений повторно снять snapshot и проверить, что симптомы исчезли.
6. Если поломка касается UI, открыть `http://127.0.0.1:8080` в браузере и подтвердить восстановление действием.

## Полезные команды

```bash
python3 scripts/runtime_snapshot.py
./scripts/run_runtime_snapshot.command
python3 scripts/openclaw_runtime_repair.py --help
./openclaw_runtime_repair.command
pytest tests/unit/test_openclaw_runtime_repair.py -q
```

## На что смотреть в первую очередь

- не расходятся ли `models.json` и `auth-profiles.json`;
- не устарел ли runtime registry относительно repo-логики;
- не сломан ли `groupPolicy` или `dmPolicy`;
- не отвалился ли `Browser / MCP Readiness`;
- не завис ли launcher на stop/start;
- не врёт ли UI о состоянии runtime.

## Ограничения

- Не перезаписывай вручную `~/.openclaw/*`, пока не снят baseline.
- Не утверждай, что runtime починен, пока повторная проверка не прошла.
- Если repair меняет политику доступа или маршрутизацию моделей, обязательно покажи до/после.
- Для пользовательских точек входа предпочитай `.command`, а не произвольные shell-команды.
