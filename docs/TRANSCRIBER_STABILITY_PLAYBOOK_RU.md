# Transcriber Stability Playbook (macOS)

## Что это
Короткий runbook для случаев, когда транскрибатор падает во время разработки.

## Быстрый старт
1. Базовая диагностика:
```bash
./transcriber_doctor.command
```
2. Мягкое восстановление (heal):
```bash
./transcriber_doctor.command --heal
```

## Что проверяет doctor
- `OpenClaw /health`
- `Voice Gateway /health`
- слушатель порта `8090`
- heavy `pyrefly` процессы по RAM
- хвост логов:
  - `/Users/pablito/Antigravity_AGENTS/Краб/krab.log`
  - `/Users/pablito/Antigravity_AGENTS/Краб/openclaw.log`
  - `/Users/pablito/Antigravity_AGENTS/Krab Voice Gateway/gateway.log`

## Защита от AGX/SIGABRT
В `Perceptor` добавлен изолированный STT-воркер:
- `STT_ISOLATED_WORKER=1`
- `STT_WORKER_TIMEOUT_SECONDS=240`

Смысл: если `mlx_whisper` аварийно падает внутри Metal/AGX, падает только дочерний процесс STT, а не весь Krab.
