# Krab Local Monitoring Stack

Prometheus + Grafana + Alertmanager в Docker Compose.

## Порты

| Сервис | URL |
|--------|-----|
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin / krab_local) |
| Alertmanager | http://localhost:9093 |

## Запуск

```bash
./scripts/start_monitoring.command
```

или вручную:

```bash
cd deploy/monitoring
docker compose up -d
```

## Остановка

```bash
cd deploy/monitoring
docker compose down
```

## Настройка Alertmanager → Telegram

Alertmanager по умолчанию роутит через Krab `/api/notify` endpoint (localhost:8080).

Если нужно прямое оповещение через Telegram bot:
1. Получите `BOT_TOKEN` у @BotFather
2. Узнайте свой `CHAT_ID` (отправьте боту сообщение, проверьте `getUpdates`)
3. В `alertmanager.yml` замените url на:
   ```
   https://api.telegram.org/bot<BOT_TOKEN>/sendMessage
   ```
   и добавьте шаблон с `chat_id` и `text` полями.

## Данные

Метрики хранятся 30 дней (`--storage.tsdb.retention.time=30d`).
Данные в Docker volumes: `krab_prometheus_data`, `krab_grafana_data`, `krab_alertmanager_data`.

Удаление данных:

```bash
docker compose down -v
```

## Validate config

```bash
cd deploy/monitoring
docker compose config
```
