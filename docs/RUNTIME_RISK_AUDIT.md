# Krab Runtime Risk Audit

Документ описывает `scripts/krab_runtime_risk_audit.py` — быстрый аудит рисков
текущего Krab runtime.

## Что проверяет

- HTTP endpoints:
  - `panel=http://127.0.0.1:8080/api/health/lite`
  - `gateway=http://127.0.0.1:18789/health`
- process probe через `ps` для `src.main`;
- наличие заполненных secret-like переменных в `.env`;
- большие логи и аварийные паттерны в хвостах `logs/*.log`;
- накопление `corrupt/broken/malformed/bak` session-артефактов в `data/sessions`.

## Запуск

```bash
venv/bin/python scripts/krab_runtime_risk_audit.py
```

Preview безопасных исправлений без изменения файлов:

```bash
venv/bin/python scripts/krab_runtime_risk_audit.py --plan-remediation
```

Применить безопасную remediation-фазу:

```bash
venv/bin/python scripts/krab_runtime_risk_audit.py --apply-remediation
```

Для запуска одним кликом на macOS:

```bash
open scripts/krab_runtime_risk_audit.command
```

## Артефакты

Скрипт пишет два JSON-файла:

- `artifacts/ops/krab_runtime_risk_audit_<timestamp>.json`
- `artifacts/ops/krab_runtime_risk_audit_latest.json`

В отчёте есть:

- `probes` — сырые результаты проверок;
- `risks` — максимум 3 главных риска, отсортированных по severity;
- `remediation` — план или результат исправлений, если передан
  `--plan-remediation`/`--apply-remediation`;
- `ok` — `false`, если найден хотя бы один `high` риск.

## Важные границы

- Скрипт ничего не удаляет и не чинит автоматически без `--apply-remediation`.
- Значения секретов из `.env` не попадают в отчёт: сохраняются только имена ключей.
- `Operation not permitted` для `ps` или localhost HTTP probe классифицируется как
  `blocked_by_sandbox`, а не как доказанный runtime down.
- Remediation делает только безопасные операции:
  - создаёт/обновляет `.env.template` без значений секретов;
  - ротирует большие `logs/*.log` через gzip copytruncate;
  - чистит только старые session backup/corrupt/malformed артефакты через
    существующую retention-политику `src.bootstrap.session_recovery`.

## Проверка

Фокусный regression gate:

```bash
venv/bin/python -m pytest \
  tests/unit/test_krab_runtime_risk_audit.py \
  tests/test_krab_core_health_watch.py \
  tests/unit/test_session_backups_retention.py \
  -q
```
