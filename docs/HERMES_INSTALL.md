# Hermes Install для Krab

Wave 19-C. Hermes Phase A/B/C/D wire-up уже реализован — нужен только binary.

## Quick Install

```bash
./scripts/install_hermes.sh
```

Скрипт автоматически:
- Проверяет наличие hermes в PATH / `~/.hermes/bin/`
- Устанавливает через `pipx` (предпочтительно) или `pip3` + venv
- Создаёт симлинк `~/.hermes/bin/hermes`
- Проверяет `~/.hermes/config.yaml` (Phase A)
- Печатает следующие шаги

## Activation

```bash
# .env
KRAB_AGENT_ENGINE=hermes           # или 'auto' для health-gated routing
KRAB_AGENT_ENGINE_DISPATCH_ENABLED=1

# Если binary только в ~/.hermes/bin/ и не в PATH:
KRAB_HERMES_BINARY=/Users/<you>/.hermes/bin/hermes
```

Перезапуск Krab:

```bash
'new Stop Krab.command' && 'new start_krab.command'
```

Проверка:

```bash
curl http://127.0.0.1:8080/api/agent-engine/status
```

## Binary Resolution Order

`HermesACPBridge._resolve_hermes_binary()` (Wave 19-C):

1. `KRAB_HERMES_BINARY` env var — явный override
2. `shutil.which("hermes")` — системный PATH
3. `~/.hermes/bin/hermes` — стандартное место Phase A install

## Per-room A/B (Phase D)

```
!engine room analysts hermes   # только analysts на Hermes
!engine room coders openclaw   # остальные на OpenClaw
!engine status                 # текущая карта routing
```

## SkillCurator A/B (Phase D)

```
!curator ab start traders <proposal_id>   # A/B тест для команды
!curator ab status                         # активные A/B тесты
!curator ab evaluate <ab_id> --apply      # decision + auto-apply
```

## Troubleshooting

| Симптом | Действие |
|---------|----------|
| `hermes binary not found` в логах | Запусти `./scripts/install_hermes.sh` |
| `Hermes Phase C unhealthy → fallback OpenClaw` | Проверь `health()` output в Sentry |
| `KRAB_HERMES_BINARY` указывает на невалидный файл | Исправь путь или уберри переменную |
| `pipx install hermes-agent` fails | Попробуй `pip install --user hermes-agent` |
| Config not found | Запусти `scripts/start_hermes_standalone.command` (Phase A) |

## Phase Map

| Wave | Компонент | Статус |
|------|-----------|--------|
| 15-D | `scripts/start_hermes_standalone.command` + `~/.hermes/` | Done |
| 16-B | `src/integrations/hermes_acp_bridge.py` | Done |
| 17-B | `src/core/agent_engine_resolver.py` + endpoints | Done |
| 19-A | Swarm A/B integration | Done |
| 19-C | Binary installer + `_resolve_hermes_binary()` | **This wave** |
