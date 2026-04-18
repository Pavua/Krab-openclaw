🦀 Krab AI — Продвинутый Телеграм-Юзербот с ИИ

Krab AI — это мощный инструмент на базе Pyrogram, интегрирующий современные языковые модели (через OpenClaw API или LM Studio) прямо в ваш Telegram аккаунт. Проект прошел полный цикл архитектурного рефакторинга для обеспечения стабильности, безопасности и производительности.

🚀 Ключевые особенности

🛡️ Защищенная архитектура: Полная декомпозиция кода и использование кастомной иерархии исключений (KrabError) для стабильной работы.

⚡ Sliding Window Context: Интеллектуальное управление историей сообщений. Бот передает в ИИ только актуальное окно контекста, что экономит токены и ускоряет ответы.

🪵 Профессиональное логирование: Структурированный вывод через structlog (JSON/Console) для прозрачного мониторинга и быстрой отладки.

⚙️ Умное кэширование: Система кэширования на базе SQLite (TTL), подготовленная к бесшовному переходу на Redis.

🐳 Docker-Native: Готовые конфигурации для запуска всей инфраструктуры одной командой через Docker Compose.

🛠 Технологический стек

Язык: Python 3.11+

Библиотеки: Pyrogram (Asyncio), structlog, httpx, sqlite3

AI: OpenClaw Gateway / LM Studio

Инфраструктура: Docker, Docker Compose

📦 Быстрый старт (Docker)

Настройка окружения:
Создайте файл .env в корне проекта на основе config.py и заполните свои API ключи.

Подготовка сессии:
Если у вас уже есть файл сессии Telegram, назовите его kraab.session и положите в корень проекта. Если нет — создайте пустой файл: touch kraab.session.

Запуск:

docker-compose up --build -d


Просмотр логов:

docker logs -f krab-ai-bot


📁 Структура проекта

src/main.py — Точка входа и инициализация всех систем.

src/core/ — Ядро: логгер, кастомные исключения, валидация конфигурации.

src/handlers/ — Обработчики команд и бизнес-логика сообщений.

src/web/ — Интеграция с API и внешними провайдерами ИИ.

src/voice_channel/ — Голосовой канал (новый модуль, интеграция с Voice Gateway и Krab Ear).

src/mcp_tools/voice_assistant_tools.py — MCP инструменты для голосового ассистента.

artifacts/ — Локальное хранилище данных, кэша и истории.

## Voice Channel (новый)

Краб агент теперь поддерживает **голосовой канал** параллельно Telegram-юзеботу. Голос работает как отдельный интерфейс к единому мозгу агента с полным доступом к памяти, MCP инструментам и OpenClaw.

### Endpoint

```
POST /v1/voice/message
Host: localhost:8081
Content-Type: application/json

{
  "chat_id": "user_123",
  "text": "Какова сумма мои последних расходов?",
  "language": "ru"
}
```

Ответ возвращается в виде SSE потока:

```
data: {"token": "Сумма"}\n\n
data: {"token": " ваших"}\n\n
data: {"token": " последних"}\n\n
...
```

### Интеграция

1. **Voice Gateway** (порт 8090) выполняет STT через Krab Ear.
2. Отправляет распознанный текст на `/v1/voice/message` (порт 8081).
3. Krab агент маршрутизирует запрос через OpenClaw + MCP инструменты.
4. Токены отклика стримятся обратно в Voice Gateway для TTS.

Порт **8081** изолирован от OpenClaw Dashboard (18789) и Krab Ear панели (8080).

## MCP Голосовые Инструменты

Голосовой ассистент имеет доступ к специализированным MCP инструментам:

### `voice:get_recent_dictations(n)`

Получить последние **n** диктовок/транскрипций из Krab Ear через Unix socket IPC.

```python
# Пример
result = await voice_tools.get_recent_dictations(n=5)
# Возвращает: [{"id": "...", "text": "...", "timestamp": "...", "confidence": 0.95}, ...]
```

### `voice:send_telegram(chat_id, text)`

Отправить сообщение в Telegram через интеграцию Krab юзербота.

```python
# Пример
await voice_tools.send_telegram(chat_id=12345678, text="Голосом из ассистента")
```

### `voice:search_memory(query, limit)`

Поиск в памяти агента (ChromaDB) по семантическому сходству.

```python
# Пример
results = await voice_tools.search_memory(query="последние встречи", limit=3)
# Возвращает: [{"text": "...", "score": 0.92, "source": "..."}, ...]
```

## Запуск

Голосовой канал запускается вместе с основным юзеботом или отдельным процессом:

```bash
# Вариант 1: вместе с юзеботом (docker-compose)
docker-compose up --build -d

# Вариант 2: отдельно (develop)
python -m src.voice_channel.server --port 8081 --openai-api-key $OPENAI_KEY
```

Проверить доступность:

```bash
curl -X POST http://localhost:8081/v1/voice/message \
  -H "Content-Type: application/json" \
  -d '{"chat_id": "test", "text": "привет", "language": "ru"}'
```

Разработчик: Pavua

Версия: 2.6 (Stable / Dockerized / Voice Channel)