# OpenClaw / Krab Ops Incident Runbook

Этот документ (шпаргалка) описывает порядок действий при возникновении частых инцидентов в инфраструктуре Krab и OpenClaw.

## 1. Инцидент: Signal канал отвалился (SSE error / 429 Rate Limit / Not Registered)

**Симптомы:**
- Приходят алерты от `signal_ops_guard.py` с severity `high` или `critical`.
- Сообщения об ошибках `fetch failed`, `Signal SSE stream error`, `Rate Limited` (429), `not registered`.
- Входящие сообщения в Signal не доходят до Krab.

**Действия:**
1. **Проверить логи канала:**
   `openclaw channels logs --channel signal --lines 100`
2. **Проверить статус канала:**
   `openclaw channels status --probe`
3. **Решение (Stream error / fetch failed):**
   Чаще всего это временные сетевые проблемы с Signal-сервером или утилитой `signal-cli`.
   - Попробуйте перезапустить процесс Signal:
     `killall java` (если используется signal-cli на java) или перезапустите docker-контейнер channel-провайдера.
4. **Решение (Rate Limited / 429):**
   - Это означает превышение лимитов отправки в Signal. Ограничьте исходящий спам или подождите (~1-2 часа).
5. **Решение (Not Registered):**
   - Устройство или номер отвязаны. Перепривяжите аккаунт Signal (qrencode или captcha) через консоль управления OpenClaw.

## 2. Инцидент: Ошибка отправки алертов в Telegram

**Симптомы:**
- `check_signal_alert_route.command` падает с ошибками.
- Ошибки отправки уведомлений в консоли Guard'a (`chat not found`).

**Действия:**
1. Запустите `./scripts/check_signal_alert_route.command --strict` для диагностики.
2. Проверьте `.env` на наличие `OPENCLAW_TELEGRAM_BOT_TOKEN`, `OPENCLAW_TELEGRAM_CHAT_ID`, `OPENCLAW_ALERT_TARGET`.
3. Если ошибка `chat not found`: бот не может писать юзеру с `OPENCLAW_ALERT_TARGET`. Нужно открыть диалог с ботом и нажать `/start`.
4. Для получения `chat_id` используйте: `./scripts/resolve_telegram_alert_target.command`. Убедитесь, что найденный ID совпадает с `OPENCLAW_TELEGRAM_CHAT_ID`.

## 3. Инцидент: Отсутствие или падение локальной модели в LM Studio (Отказ Fallback)

**Симптомы:**
- Пользователи получают сообщение `400 No models loaded` или боты в мессенджерах молчат.
- `openclaw_model_autoswitch.py` не может переключить default модель (ошибки CLI).

**Действия:**
1. **Диагностика:**
   `./scripts/openclaw_model_autoswitch.py --dry-run`
   Это покажет, видит ли скрипт загруженные локальные модели и куда собирается переключать.
2. **Проверка LM Studio:**
   - Откройте LM Studio и проверьте раздел Server. Он запущен? На 1234 порту?
   - Попробуйте `curl http://127.0.0.1:1234/v1/models` - должен возвращаться JSON с моделями.
3. **Ручной Fallback (если автоматика упала):**
   - Назначить cloud-модель принудительно:
     `openclaw models set google/gemini-2.5-flash`
   - Очистить локальные модели из настроек:
     `openclaw models fallbacks clear`
     `openclaw models fallbacks add openai/gpt-4o-mini`
4. **Лечение автоматики:**
   - Перезапустите daemon: `./scripts/openclaw_model_autoswitch.command` (или как он запускается в вашей ОС).

## 4. Live Smoke каналов и sanitizer (после фиксов/релиза)

**Сценарий:** нужно быстро проверить, что:
- `openclaw channels status --probe` проходит;
- в последних строках логов нет утечки служебного текста (`<|begin_of_box|>`, `I will now call ...`, `The model has crashed` и т.д.).

**Запуск (one-click):**
- `./scripts/live_channel_smoke.command`

**Запуск (CLI):**
- `python3 scripts/live_channel_smoke.py`
- Пример с параметрами:
  - `python3 scripts/live_channel_smoke.py --tail-lines 400 --probe-timeout 25`
  - `python3 scripts/live_channel_smoke.py --strict-warnings`
  - `python3 scripts/live_channel_smoke.py --no-openclaw-logs`

**Что делает скрипт:**
1. Выполняет `openclaw channels status --probe`.
2. Сканирует хвост ключевых логов:
   - `logs/krab_manual_bg.log`
   - `logs/krab.log`
   - `logs/ai_decisions.log`
   - `~/.openclaw/logs/gateway.log`
   - `~/.openclaw/logs/gateway.err.log`
3. Пишет отчет:
   - `artifacts/ops/live_channel_smoke_<UTC>.json`
   - `artifacts/ops/live_channel_smoke_latest.json`

**Критерий успеха (`ok=true`):**
- probe успешен;
- `error_findings_count == 0`.
- `warn_findings_count` допускается, если не включен `--strict-warnings`.

**Если smoke красный:**
1. Откройте `artifacts/ops/live_channel_smoke_latest.json`.
2. Посмотрите `channels_probe.stdout_tail/stderr_tail`.
3. Разберите `findings[]` и устраните источник утечки/сбоя.
4. Повторите smoke до зеленого результата.
