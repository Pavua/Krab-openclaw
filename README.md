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

artifacts/ — Локальное хранилище данных, кэша и истории.

Разработчик: Pavua

Версия: 2.6 (Stable / Dockerized)

## One-click запуск полного стека

Если хочешь запускать не только Krab/OpenClaw, но и соседние сервисы экосистемы:

- `/Users/pablito/Antigravity_AGENTS/Краб/Start Full Ecosystem.command`  
  Запускает Krab Voice Gateway, Krab Ear Agent и затем Krab/OpenClaw.
- `/Users/pablito/Antigravity_AGENTS/Краб/Stop Full Ecosystem.command`  
  Останавливает Krab/OpenClaw, Voice Gateway и Krab Ear.
- `/Users/pablito/Antigravity_AGENTS/Краб/Check Full Ecosystem.command`  
  Быстрый health-check по основным локальным endpoint-ам.
