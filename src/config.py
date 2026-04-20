"""
Конфигурация проекта Краб
"""

import json
import logging
import os
import warnings
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

_config_logger = logging.getLogger(__name__)

# Загрузить .env файл
load_dotenv()


class Config:
    """Центральная конфигурация приложения"""

    # Paths
    BASE_DIR: Path = Path(__file__).parent.parent

    # Telegram
    TELEGRAM_API_ID: int = int(os.getenv("TELEGRAM_API_ID", "0"))
    TELEGRAM_API_HASH: str = os.getenv("TELEGRAM_API_HASH", "")
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")  # Optional fallback
    TELEGRAM_SESSION_NAME: str = os.getenv("TELEGRAM_SESSION_NAME", "kraab")
    # Если включено, обычный runtime может запускать интерактивный Telegram login прямо в start().
    # По умолчанию выключено: relogin выполняется только через telegram_relogin.command.
    TELEGRAM_ALLOW_INTERACTIVE_LOGIN: bool = os.getenv(
        "TELEGRAM_ALLOW_INTERACTIVE_LOGIN",
        "0",
    ).strip().lower() in ("1", "true", "yes")

    # OpenClaw
    OPENCLAW_URL: str = os.getenv(
        "OPENCLAW_URL", os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18789")
    )
    OPENCLAW_TOKEN: str = os.getenv(
        "OPENCLAW_GATEWAY_TOKEN", os.getenv("OPENCLAW_TOKEN", os.getenv("OPENCLAW_API_KEY", ""))
    )

    # LM Studio (trailing slash stripped for API calls)
    LM_STUDIO_URL: str = os.getenv("LM_STUDIO_URL", "http://192.168.0.171:1234").rstrip("/")
    # Каноничный токен локального LM Studio API.
    # `LM_STUDIO_AUTH_TOKEN` оставляем как legacy alias, чтобы не ломать старые env.
    LM_STUDIO_API_KEY: str = os.getenv(
        "LM_STUDIO_API_KEY",
        os.getenv("LM_STUDIO_AUTH_TOKEN", ""),
    ).strip()

    # Gemini (fallback): free key first, paid key as opt-in
    GEMINI_API_KEY_FREE: Optional[str] = os.getenv("GEMINI_API_KEY_FREE")
    GEMINI_API_KEY_PAID: Optional[str] = os.getenv("GEMINI_API_KEY_PAID")
    # Платный ключ НЕ используется пока GEMINI_PAID_KEY_ENABLED != 1.
    # Защита от случайных расходов: ключ в .env можно оставить, но без флага
    # он игнорируется. Включить: GEMINI_PAID_KEY_ENABLED=1 в .env.
    GEMINI_PAID_KEY_ENABLED: bool = os.getenv("GEMINI_PAID_KEY_ENABLED", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    GEMINI_API_KEY: Optional[str] = (
        (
            os.getenv("GEMINI_API_KEY_PAID")
            if os.getenv("GEMINI_PAID_KEY_ENABLED", "0").strip().lower() in ("1", "true", "yes")
            else None
        )
        or os.getenv("GEMINI_API_KEY_FREE")
        or os.getenv("GEMINI_API_KEY")
    )
    GEMINI_MODELS: list[str] = [
        "google/gemini-2.5-flash",
        "google/gemini-2.5-pro",
        "google/gemini-flash-latest",
    ]
    MODEL: str = os.getenv("MODEL", "google/gemini-2.5-flash")

    # LM Studio: preferred local model (substring match)
    LOCAL_PREFERRED_MODEL: str = os.getenv("LOCAL_PREFERRED_MODEL", "nvidia/nemotron-3-nano")
    # LM Studio: preferred local vision model (для фото/изображений)
    LOCAL_PREFERRED_VISION_MODEL: str = os.getenv(
        "LOCAL_PREFERRED_VISION_MODEL",
        "auto",
    )
    # Держать только одну локальную модель в памяти (минимизация RAM/SWAP)
    SINGLE_LOCAL_MODEL_MODE: bool = os.getenv("SINGLE_LOCAL_MODEL_MODE", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    # Guarded idle-unload: в режиме стабильности не выгружаем локальную модель автоматически,
    # чтобы каналы OpenClaw (bot/iMessage/dashboard) не ловили "No models loaded" после простоя.
    GUARDED_IDLE_UNLOAD: bool = os.getenv("GUARDED_IDLE_UNLOAD", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    # Минимальная пауза (сек) после любого запроса перед авто-выгрузкой модели.
    # Нужна, чтобы не выгружать модель "на стыке" между соседними сообщениями/каналами.
    GUARDED_IDLE_UNLOAD_GRACE_SEC: float = float(os.getenv("GUARDED_IDLE_UNLOAD_GRACE_SEC", "90"))
    # После idle-unload задачной/vision-модели автоматически загружать LOCAL_PREFERRED_MODEL обратно.
    # Полезно при активном использовании локальных моделей: после vision-задачи chat-модель
    # возвращается в память без ручного !model load.
    RESTORE_PREFERRED_ON_IDLE_UNLOAD: bool = os.getenv(
        "RESTORE_PREFERRED_ON_IDLE_UNLOAD", "0"
    ).strip().lower() in ("1", "true", "yes")
    # qwen3-30b routing: preferred LLM для voice channel + text (VA Phase 1.6)
    KRAB_MODEL_QWEN3_30B: str = os.getenv("KRAB_MODEL_QWEN3_30B", "qwen3-30b-a3b-instruct-2507")
    # LRU eviction TTL (секунды): если модель не использовалась дольше,
    # она выгружается при загрузке другой модели (LRU policy).
    KRAB_LRU_EVICT_AFTER_SEC: float = float(os.getenv("KRAB_LRU_EVICT_AFTER_SEC", "300"))
    # Таймауты stream-ответа OpenClaw (сек):
    # - CHUNK: ожидание нового чанка стриминга (между порциями данных);
    # - FIRST_CHUNK: ожидание первого чанка для текстового запроса;
    # - PHOTO_FIRST_CHUNK: ожидание первого чанка для фото/vision запроса.
    #
    # Зачем 3600 (1 час):
    # - Краб на практике обрабатывает запросы по 30-40 минут (Gemini Pro, локальные модели);
    # - старый лимит 180 сек обрывал живой stream и порождал ложную ошибку;
    # - Gateway сам управляет своим lifecycle — двойной таймаут вреден.
    OPENCLAW_CHUNK_TIMEOUT_SEC: float = float(os.getenv("OPENCLAW_CHUNK_TIMEOUT_SEC", "3600"))
    OPENCLAW_FIRST_CHUNK_TIMEOUT_SEC: float = float(
        os.getenv("OPENCLAW_FIRST_CHUNK_TIMEOUT_SEC", "3600")
    )
    OPENCLAW_PHOTO_FIRST_CHUNK_TIMEOUT_SEC: float = float(
        os.getenv("OPENCLAW_PHOTO_FIRST_CHUNK_TIMEOUT_SEC", "3600")
    )
    # Отдельные read-timeout бюджеты для buffered `stream=False` completion.
    # Зачем:
    # - userbot сейчас получает финальный ответ только после полного JSON-ответа провайдера;
    # - без read-timeout некоторые cloud-маршруты (особенно codex-cli/openai-codex)
    #   могут "держать" запрос очень долго, не считаясь упавшими;
    # - из-за этого fallback-цепочка не стартует, хотя с точки зрения пользователя
    #   Краб уже выглядит зависшим.
    #
    # None/пусто = не ограничивать buffered read-timeout на уровне клиента.
    OPENCLAW_BUFFERED_READ_TIMEOUT_SEC: Optional[float] = (
        float(x) if (x := os.getenv("OPENCLAW_BUFFERED_READ_TIMEOUT_SEC", "").strip()) else None
    )
    # CLI-провайдеры: по умолчанию None — OpenClaw Gateway сам управляет fallback/retry.
    # Двойной read-timeout (Gateway 180с + Краб 240с) вызывал ложные ошибки "Провайдер недоступен".
    # Переопредели через .env если нужен явный бюджет.
    OPENCLAW_CODEX_CLI_BUFFERED_READ_TIMEOUT_SEC: Optional[float] = (
        float(x)
        if (x := os.getenv("OPENCLAW_CODEX_CLI_BUFFERED_READ_TIMEOUT_SEC", "").strip())
        else None  # codex-cli может думать долго; зависание ловит inactivity watchdog
    )
    OPENCLAW_GOOGLE_GEMINI_CLI_BUFFERED_READ_TIMEOUT_SEC: Optional[float] = (
        float(x)
        if (x := os.getenv("OPENCLAW_GOOGLE_GEMINI_CLI_BUFFERED_READ_TIMEOUT_SEC", "").strip())
        else 120.0  # 2 мин — Gemini CLI быстрее codex
    )
    OPENCLAW_OPENAI_CODEX_BUFFERED_READ_TIMEOUT_SEC: Optional[float] = (
        float(x)
        if (x := os.getenv("OPENCLAW_OPENAI_CODEX_BUFFERED_READ_TIMEOUT_SEC", "").strip())
        else None
    )
    # Ранние тех-уведомления до hard-timeout.
    # Зачем:
    # - пользователь должен понимать, что запрос жив и модель действительно думает;
    # - эти уведомления не обрывают stream, а только редактируют временное сообщение.
    # INITIAL_SEC=15: первый сигнал через 15 сек — быстро подтверждаем, что запрос принят.
    # REPEAT_SEC=30: повторяем каждые 30 сек с обновлённым статусом инструмента.
    OPENCLAW_PROGRESS_NOTICE_INITIAL_SEC: float = float(
        os.getenv("OPENCLAW_PROGRESS_NOTICE_INITIAL_SEC", "15")
    )
    OPENCLAW_PROGRESS_NOTICE_REPEAT_SEC: float = float(
        os.getenv("OPENCLAW_PROGRESS_NOTICE_REPEAT_SEC", "30")
    )
    OPENCLAW_PHOTO_PROGRESS_NOTICE_INITIAL_SEC: float = float(
        os.getenv("OPENCLAW_PHOTO_PROGRESS_NOTICE_INITIAL_SEC", "30")
    )
    OPENCLAW_PHOTO_PROGRESS_NOTICE_REPEAT_SEC: float = float(
        os.getenv("OPENCLAW_PHOTO_PROGRESS_NOTICE_REPEAT_SEC", "60")
    )
    # Auto-retry при инфраструктурных ошибках LLM (quota, timeout, 5xx).
    # Не применяется при ошибках пользователя (safety block, неверный запрос).
    # RETRY_COUNT=0 — отключить auto-retry полностью.
    # RETRY_DELAY_SEC — пауза перед каждой повторной попыткой.
    OPENCLAW_AUTO_RETRY_COUNT: int = int(os.getenv("OPENCLAW_AUTO_RETRY_COUNT", "1"))
    OPENCLAW_AUTO_RETRY_DELAY_SEC: float = float(os.getenv("OPENCLAW_AUTO_RETRY_DELAY_SEC", "2.0"))
    # Таймаут ожидания готовности OpenClaw при старте userbot.
    # При высокой нагрузке на CPU startup может занять до 42 мин — минимум 10s enforced.
    OPENCLAW_HEALTH_WAIT_TIMEOUT_SEC: int = max(
        10, int(os.getenv("OPENCLAW_HEALTH_WAIT_TIMEOUT_SEC", "90"))
    )
    # Ограничение длины ответа userbot (ускоряет локальные модели в чатах).
    USERBOT_MAX_OUTPUT_TOKENS: int = int(os.getenv("USERBOT_MAX_OUTPUT_TOKENS", "1200"))
    USERBOT_PHOTO_MAX_OUTPUT_TOKENS: int = int(os.getenv("USERBOT_PHOTO_MAX_OUTPUT_TOKENS", "420"))
    # Debounce-окно для склейки нескольких private-сообщений подряд в один запрос.
    # Почему это нужно:
    # - Telegram режет длинный recap на несколько сообщений;
    # - без короткого окна склейки userbot отправляет в LLM несколько отдельных задач;
    # - это усиливает очередь, даёт ложное ощущение "зависания" и плодит таймауты.
    TELEGRAM_MESSAGE_BATCH_WINDOW_SEC: float = float(
        os.getenv("TELEGRAM_MESSAGE_BATCH_WINDOW_SEC", "1.4")
    )
    TELEGRAM_MESSAGE_BATCH_MAX_MESSAGES: int = int(
        os.getenv("TELEGRAM_MESSAGE_BATCH_MAX_MESSAGES", "12")
    )
    TELEGRAM_MESSAGE_BATCH_MAX_CHARS: int = int(
        os.getenv("TELEGRAM_MESSAGE_BATCH_MAX_CHARS", "12000")
    )
    # Фото-path userbot по умолчанию всегда уводим в cloud.
    # Почему так:
    # - внешние чаты важнее держать предсказуемыми, чем экспериментировать с локальным VL;
    # - это не даёт выгружать Nemotron ради случайной маленькой vision-модели;
    # - локальный vision остаётся доступен только через явный opt-in флаг.
    USERBOT_FORCE_CLOUD_FOR_PHOTO: bool = os.getenv(
        "USERBOT_FORCE_CLOUD_FOR_PHOTO",
        "1",
    ).strip().lower() in ("1", "true", "yes")
    # Нативный local-direct путь LM Studio:
    # - reasoning по умолчанию отключаем только на API-уровне нашего клиента,
    #   чтобы скрытое "мышление" не съедало бюджет ответа в Telegram/user-facing каналах;
    # - при насыщении лимита можем автоматически запросить продолжение.
    LM_STUDIO_NATIVE_REASONING_MODE: str = (
        os.getenv(
            "LM_STUDIO_NATIVE_REASONING_MODE",
            "off",
        )
        .strip()
        .lower()
    )
    LM_STUDIO_NATIVE_AUTO_CONTINUE_MAX_ROUNDS: int = int(
        os.getenv("LM_STUDIO_NATIVE_AUTO_CONTINUE_MAX_ROUNDS", "2")
    )
    LM_STUDIO_NATIVE_OUTPUT_CAP_MARGIN: int = int(
        os.getenv("LM_STUDIO_NATIVE_OUTPUT_CAP_MARGIN", "8")
    )

    # Skills / APIs
    BRAVE_SEARCH_API_KEY: Optional[str] = os.getenv(
        "BRAVE_SEARCH_API_KEY", os.getenv("BRAVE_API_KEY")
    )

    # Memory limits
    MAX_RAM_GB: int = int(os.getenv("MAX_RAM_GB", "24"))

    # Dialog history: sliding window (Phase 6)
    HISTORY_WINDOW_MESSAGES: int = int(os.getenv("HISTORY_WINDOW_MESSAGES", "50"))
    HISTORY_WINDOW_MAX_CHARS: Optional[int] = (
        int(x) if (x := os.getenv("HISTORY_WINDOW_MAX_CHARS", "").strip()) else None
    )
    # Более жёсткое окно для локального inference-маршрута.
    # Почему отдельно:
    # - LM Studio на длинных диалогах начинает aggressively truncate context;
    # - это повышает шанс `EMPTY MESSAGE`, долгого prompt-processing и аварийных сбоев.
    # Cloud-маршрут при этом может жить с более широким окном.
    LOCAL_HISTORY_WINDOW_MESSAGES: int = int(os.getenv("LOCAL_HISTORY_WINDOW_MESSAGES", "18"))
    LOCAL_HISTORY_WINDOW_MAX_CHARS: Optional[int] = (
        int(x) if (x := os.getenv("LOCAL_HISTORY_WINDOW_MAX_CHARS", "12000").strip()) else None
    )
    # Controlled retry после `EMPTY MESSAGE` / `model crashed`.
    # Почему отдельно:
    # - повтор с тем же длинным хвостом часто воспроизводит тот же сбой;
    # - retry-контекст должен быть заметно компактнее основного local budget.
    RETRY_HISTORY_WINDOW_MESSAGES: int = int(os.getenv("RETRY_HISTORY_WINDOW_MESSAGES", "8"))
    RETRY_HISTORY_WINDOW_MAX_CHARS: int = int(os.getenv("RETRY_HISTORY_WINDOW_MAX_CHARS", "4000"))
    RETRY_MESSAGE_MAX_CHARS: int = int(os.getenv("RETRY_MESSAGE_MAX_CHARS", "1200"))

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Routing: force_cloud полностью обходит локальный путь (Фаза 2.2)
    FORCE_CLOUD: bool = os.getenv("FORCE_CLOUD", "0").strip().lower() in ("1", "true", "yes")
    # Переводить ли браузерную вкладку на передний план при каждом обращении.
    # Дефолт 0 = тихий режим (Краб работает с браузером не переключая окно).
    # Включить: BROWSER_FOCUS_TAB=1 в .env или !config BROWSER_FOCUS_TAB=1
    BROWSER_FOCUS_TAB: bool = os.getenv("BROWSER_FOCUS_TAB", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    # Разрешать ли автоматический fallback cloud -> local при ошибках облака.
    # Важно: это НЕ отключает ручной local-режим (!model local), а только аварийный
    # автопереход в локаль из cloud-сценариев.
    LOCAL_FALLBACK_ENABLED: bool = os.getenv(
        "LOCAL_FALLBACK_ENABLED",
        "1",
    ).strip().lower() in ("1", "true", "yes")

    # Погода: город по умолчанию для !weather без аргументов
    DEFAULT_WEATHER_CITY: str = os.getenv("DEFAULT_WEATHER_CITY", "Barcelona")

    # Tor SOCKS5 proxy (опционально, для анонимных запросов через Tor)
    TOR_ENABLED: bool = os.getenv("TOR_ENABLED", "0").strip().lower() in ("1", "true", "yes")
    TOR_SOCKS_PORT: int = int(os.getenv("TOR_SOCKS_PORT", "9050"))
    TOR_CONTROL_PORT: int = int(os.getenv("TOR_CONTROL_PORT", "9051"))

    # User settings
    OWNER_USERNAME: str = os.getenv("OWNER_USERNAME", "@yung_nagato")
    ALLOWED_USERS: list[str] = [
        u.strip().lstrip("@")
        for u in os.getenv("ALLOWED_USERS", "pablito,admin").split(",")
        if u.strip()
    ]
    # Явные owner/full/partial ACL-списки для userbot.
    # Почему не убираем ALLOWED_USERS сразу:
    # - старый allowlist уже используется в runtime;
    # - в новой схеме он остаётся legacy-источником full-доступа до миграции UI.
    OWNER_USER_IDS: list[str] = [
        u.strip() for u in os.getenv("OWNER_USER_IDS", "").split(",") if u.strip()
    ]
    FULL_ACCESS_USERS: list[str] = [
        u.strip().lstrip("@")
        for u in os.getenv("FULL_ACCESS_USERS", os.getenv("ALLOWED_USERS", "pablito,admin")).split(
            ","
        )
        if u.strip()
    ]
    PARTIAL_ACCESS_USERS: list[str] = [
        u.strip().lstrip("@") for u in os.getenv("PARTIAL_ACCESS_USERS", "").split(",") if u.strip()
    ]
    # Ручной чёрный список: username-ы или числовые ID, которым Краб не отвечает автоматически.
    # Формат: "username1,@username2,123456" (@ необязателен, нормализуется к lower-case).
    MANUAL_BLOCKLIST: frozenset[str] = frozenset(
        u.strip().lstrip("@").lower()
        for u in os.getenv("MANUAL_BLOCKLIST", "").split(",")
        if u.strip()
    )
    USERBOT_ACL_FILE: Path = Path(
        os.getenv(
            "USERBOT_ACL_FILE",
            str(Path.home() / ".openclaw" / "krab_userbot_acl.json"),
        )
    )
    OPENCLAW_MAIN_WORKSPACE_DIR: Path = Path(
        os.getenv(
            "OPENCLAW_MAIN_WORKSPACE_DIR",
            str(Path.home() / ".openclaw" / "workspace-main-messaging"),
        )
    )
    TRIGGER_PREFIXES: list[str] = [
        p.strip()
        for p in os.getenv("TRIGGER_PREFIXES", "!краб,@краб,/краб,Краб,,краб,").split(",")
        if p.strip()
    ]
    # Опциональный дисклеймер в начале диалога: честный автоответчик без маскировки.
    AI_DISCLOSURE_ENABLED: bool = os.getenv("AI_DISCLOSURE_ENABLED", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    AI_DISCLOSURE_TEXT: str = os.getenv(
        "AI_DISCLOSURE_TEXT",
        "Я автоассистент Краб 🦀. Пишу от имени владельца и могу помочь по его задачам.",
    )
    # Пересылать owner-у входящие сообщения от незнакомых контактов (GUEST).
    # Включено по умолчанию: аптека написала "препараты приехали" → owner должен знать.
    FORWARD_UNKNOWN_INCOMING: bool = os.getenv("FORWARD_UNKNOWN_INCOMING", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    # Safe-mode для неавторизованных контактов:
    # - отдельный изолированный контекст (не пересекается с owner-чатами),
    # - нейтральный системный промпт без персональных данных владельца.
    NON_OWNER_SAFE_MODE_ENABLED: bool = os.getenv(
        "NON_OWNER_SAFE_MODE_ENABLED",
        "1",
    ).strip().lower() in ("1", "true", "yes")
    NON_OWNER_SAFE_PROMPT: str = os.getenv(
        "NON_OWNER_SAFE_PROMPT",
        (
            "Ты — живой помощник. Отвечай дружелюбно, естественно и кратко, как если бы ты был реальный человек. "
            "НЕ представляйся ботом или ИИ. Не упоминай tools, exec, search или внутренние возможности. "
            "Не выдавай личные данные владельца, внутренние заметки, отчёты, ключи, конфиги или историю других чатов. "
            "Не обращайся к собеседнику как к владельцу. "
            "Если собеседник просит передать что-то важное или срочное — подтверди что передашь."
        ),
    )
    PARTIAL_ACCESS_PROMPT: str = os.getenv(
        "PARTIAL_ACCESS_PROMPT",
        (
            "Ты — ассистент Краб в режиме частичного доступа. "
            "Можно помогать с обычными вопросами, безопасным поиском и статусом runtime, "
            "но нельзя раскрывать owner-only команды, файловый доступ, ключи, конфиги, память других чатов и внутренние заметки."
        ),
    )
    # Удалять transport-маркеры от внешних каналов/моделей (например [[reply_to:123]]).
    STRIP_REPLY_TO_TAGS: bool = os.getenv(
        "STRIP_REPLY_TO_TAGS",
        "1",
    ).strip().lower() in ("1", "true", "yes")
    # Streaming UI: интервал обновления сообщения при получении LLM chunks (секунды).
    # Telegram rate limit ~20 edits/min на чат; 2s = безопасный default.
    TELEGRAM_STREAM_UPDATE_INTERVAL_SEC: float = float(
        os.getenv("TELEGRAM_STREAM_UPDATE_INTERVAL_SEC", "2.0")
    )
    # Streaming UI: интервал проверки tool progress (секунды).
    OPENCLAW_TOOL_PROGRESS_POLL_SEC: float = float(
        os.getenv("OPENCLAW_TOOL_PROGRESS_POLL_SEC", "3.0")
    )
    # Порог стагнации для hard-cancel LLM-call через gateway watchdog.
    # Если ни одна из активных задач OpenClaw (runs.sqlite) не апдейтила last_event_at
    # дольше этого порога — Краб отменяет текущий запрос (codex-cli hung
    # после gateway restart). Default 120 сек: меньше 90 — ложные срабатывания
    # на больших моделях, больше 180 — пользователь заметно "виснет".
    LLM_STAGNATION_THRESHOLD_SEC: float = float(os.getenv("LLM_STAGNATION_THRESHOLD_SEC", "120"))
    # Streaming UI: показывать reasoning (chain-of-thought) в сообщении.
    TELEGRAM_STREAM_SHOW_REASONING: bool = os.getenv(
        "TELEGRAM_STREAM_SHOW_REASONING", "0"
    ).strip().lower() in ("1", "true", "yes")
    # Фоновые deferred-задачи (cron/reminders) в текущем userbot контуре.
    # По умолчанию включено: reminders должны работать "из коробки" после старта runtime.
    SCHEDULER_ENABLED: bool = os.getenv(
        "SCHEDULER_ENABLED",
        "1",
    ).strip().lower() in ("1", "true", "yes")
    DEFERRED_ACTION_GUARD_ENABLED: bool = os.getenv(
        "DEFERRED_ACTION_GUARD_ENABLED",
        "1",
    ).strip().lower() in ("1", "true", "yes")

    # Автономные рекуррентные задачи свёрма (scheduled swarm runs).
    # По умолчанию ВЫКЛЮЧЕНО — включать только после стабилизации.
    # Тишина: дефолтное время mute при !тишина без аргументов (минуты).
    SILENCE_DEFAULT_MINUTES: int = int(os.getenv("SILENCE_DEFAULT_MINUTES", "30"))
    # Автоматический mute когда owner сам пишет в чат (минуты).
    OWNER_AUTO_SILENCE_MINUTES: int = int(os.getenv("OWNER_AUTO_SILENCE_MINUTES", "5"))
    # Гостевой режим: запретить tools/exec для GUEST-уровня.
    GUEST_TOOLS_DISABLED: bool = os.getenv("GUEST_TOOLS_DISABLED", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    # Реакции на owner-сообщения: 👀 при получении, ✅ успех, ❌ ошибка.
    TELEGRAM_REACTIONS_ENABLED: bool = os.getenv(
        "TELEGRAM_REACTIONS_ENABLED", "1"
    ).strip().lower() in ("1", "true", "yes")

    # JSON-файл с настройками per-team Telegram аккаунтов для свёрма.
    # Формат: {"traders": {"session_name": "swarm_traders", "phone": "+34..."}, ...}
    SWARM_TEAM_ACCOUNTS_PATH: Path = Path(
        os.getenv(
            "SWARM_TEAM_ACCOUNTS_PATH",
            str(Path.home() / ".openclaw" / "krab_runtime_state" / "swarm_team_accounts.json"),
        )
    )

    SWARM_AUTONOMOUS_ENABLED: bool = os.getenv(
        "SWARM_AUTONOMOUS_ENABLED",
        "0",
    ).strip().lower() in ("1", "true", "yes")
    # Разрешить voice-сообщения в группах как триггер для бота (только от allowed пользователей),
    # даже если нет текстового упоминания "Краб".
    GROUP_VOICE_FALLBACK_TRIGGER: bool = os.getenv(
        "GROUP_VOICE_FALLBACK_TRIGGER",
        "1",
    ).strip().lower() in ("1", "true", "yes")
    # Голосовые ответы userbot.
    # Эти поля обязаны жить в typed-config, иначе после рестарта `getattr(config, ...)`
    # вернёт fallback и voice silently выключится, даже если `.env` уже настроен.
    VOICE_MODE_DEFAULT: bool = os.getenv(
        "VOICE_MODE_DEFAULT",
        "0",
    ).strip().lower() in ("1", "true", "yes")
    VOICE_REPLY_SPEED: float = float(os.getenv("VOICE_REPLY_SPEED", "1.5"))
    VOICE_REPLY_VOICE: str = (
        os.getenv("VOICE_REPLY_VOICE", "ru-RU-DmitryNeural").strip() or "ru-RU-DmitryNeural"
    )
    VOICE_REPLY_DELIVERY: str = (
        os.getenv("VOICE_REPLY_DELIVERY", "text+voice").strip().lower() or "text+voice"
    )
    # Per-chat voice reply blocklist.
    # Причина: в некоторых группах TTS от userbot квалифицируется модерацией как spam
    # (How2AI → yung_nagato получил USER_BANNED_IN_CHANNEL после нескольких голосовых),
    # и каждое новое сообщение Краба туда триггерит report spam → chat-level ban.
    # Этот список — явный opt-out: в этих чатах Краб присылает ТОЛЬКО текст,
    # даже если глобально включён voice_mode и delivery=text+voice.
    # Формат: comma-separated chat_id (отрицательные для супергрупп и каналов).
    VOICE_REPLY_BLOCKED_CHATS: list[str] = [
        s.strip() for s in os.getenv("VOICE_REPLY_BLOCKED_CHATS", "").split(",") if s.strip()
    ] or ["-1001587432709"]

    # Session watchdog heartbeat (сек). Проверяет Telegram MTProto alive.
    # Не тратит токенов, чисто внутренняя проверка.
    TELEGRAM_SESSION_HEARTBEAT_SEC: int = int(os.getenv("TELEGRAM_SESSION_HEARTBEAT_SEC", "60"))

    # Tool narration в Telegram (показывает какой инструмент вызывается).
    # Можно переключать через !notify on/off.
    TOOL_NARRATION_ENABLED: bool = os.getenv("TOOL_NARRATION_ENABLED", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    # Voice Channel FastAPI port (VA Phase 1.4).
    # Voice Gateway POSTает сюда транскрипты → ответ LLM стримится как SSE.
    KRAB_VOICE_PORT: int = int(os.getenv("KRAB_VOICE_PORT", "8081"))

    @classmethod
    def validate(cls) -> list[str]:
        """Проверяет обязательные настройки и возвращает список ошибок"""
        errors = []

        if not cls.TELEGRAM_API_ID:
            errors.append("TELEGRAM_API_ID не установлен")
        if not cls.TELEGRAM_API_HASH:
            errors.append("TELEGRAM_API_HASH не установлен")

        return errors

    @classmethod
    def is_valid(cls) -> bool:
        """Проверяет валидность конфигурации"""
        return len(cls.validate()) == 0

    @classmethod
    def update_setting(cls, key: str, value: str) -> bool:
        """Обновляет настройку в памяти и в .env файле"""
        try:
            key = key.upper()
            if key == "LM_STUDIO_AUTH_TOKEN":
                key = "LM_STUDIO_API_KEY"
            # Обновляем в текущем процессе
            if hasattr(cls, key):
                if key == "ALLOWED_USERS":
                    cls.ALLOWED_USERS = [
                        u.strip().lstrip("@") for u in value.split(",") if u.strip()
                    ]
                elif key == "FULL_ACCESS_USERS":
                    cls.FULL_ACCESS_USERS = [
                        u.strip().lstrip("@") for u in value.split(",") if u.strip()
                    ]
                elif key == "PARTIAL_ACCESS_USERS":
                    cls.PARTIAL_ACCESS_USERS = [
                        u.strip().lstrip("@") for u in value.split(",") if u.strip()
                    ]
                elif key == "OWNER_USER_IDS":
                    cls.OWNER_USER_IDS = [u.strip() for u in value.split(",") if u.strip()]
                elif key == "MANUAL_BLOCKLIST":
                    cls.MANUAL_BLOCKLIST = frozenset(
                        u.strip().lstrip("@").lower() for u in value.split(",") if u.strip()
                    )
                elif key == "TRIGGER_PREFIXES":
                    cls.TRIGGER_PREFIXES = [p.strip() for p in value.split(",") if p.strip()]
                elif key == "MAX_RAM_GB":
                    cls.MAX_RAM_GB = int(value)
                elif key == "MODEL":
                    cls.MODEL = value
                elif key == "FORCE_CLOUD":
                    cls.FORCE_CLOUD = value.strip().lower() in ("1", "true", "yes")
                elif key == "BROWSER_FOCUS_TAB":
                    cls.BROWSER_FOCUS_TAB = value.strip().lower() in ("1", "true", "yes")
                elif key == "LOCAL_FALLBACK_ENABLED":
                    cls.LOCAL_FALLBACK_ENABLED = value.strip().lower() in ("1", "true", "yes")
                elif key == "LOCAL_PREFERRED_MODEL":
                    cls.LOCAL_PREFERRED_MODEL = value
                elif key == "LOCAL_PREFERRED_VISION_MODEL":
                    cls.LOCAL_PREFERRED_VISION_MODEL = value
                elif key == "LM_STUDIO_API_KEY":
                    cls.LM_STUDIO_API_KEY = value
                elif key == "SINGLE_LOCAL_MODEL_MODE":
                    cls.SINGLE_LOCAL_MODEL_MODE = value.strip().lower() in ("1", "true", "yes")
                elif key == "GUARDED_IDLE_UNLOAD":
                    cls.GUARDED_IDLE_UNLOAD = value.strip().lower() in ("1", "true", "yes")
                elif key == "GUARDED_IDLE_UNLOAD_GRACE_SEC":
                    cls.GUARDED_IDLE_UNLOAD_GRACE_SEC = float(value)
                elif key == "RESTORE_PREFERRED_ON_IDLE_UNLOAD":
                    cls.RESTORE_PREFERRED_ON_IDLE_UNLOAD = value.strip().lower() in (
                        "1",
                        "true",
                        "yes",
                    )
                elif key == "KRAB_MODEL_QWEN3_30B":
                    cls.KRAB_MODEL_QWEN3_30B = value
                elif key == "KRAB_LRU_EVICT_AFTER_SEC":
                    cls.KRAB_LRU_EVICT_AFTER_SEC = float(value)
                elif key == "OPENCLAW_CHUNK_TIMEOUT_SEC":
                    cls.OPENCLAW_CHUNK_TIMEOUT_SEC = float(value)
                elif key == "OPENCLAW_FIRST_CHUNK_TIMEOUT_SEC":
                    cls.OPENCLAW_FIRST_CHUNK_TIMEOUT_SEC = float(value)
                elif key == "OPENCLAW_PHOTO_FIRST_CHUNK_TIMEOUT_SEC":
                    cls.OPENCLAW_PHOTO_FIRST_CHUNK_TIMEOUT_SEC = float(value)
                elif key == "OPENCLAW_PROGRESS_NOTICE_INITIAL_SEC":
                    cls.OPENCLAW_PROGRESS_NOTICE_INITIAL_SEC = float(value)
                elif key == "OPENCLAW_PROGRESS_NOTICE_REPEAT_SEC":
                    cls.OPENCLAW_PROGRESS_NOTICE_REPEAT_SEC = float(value)
                elif key == "OPENCLAW_PHOTO_PROGRESS_NOTICE_INITIAL_SEC":
                    cls.OPENCLAW_PHOTO_PROGRESS_NOTICE_INITIAL_SEC = float(value)
                elif key == "OPENCLAW_PHOTO_PROGRESS_NOTICE_REPEAT_SEC":
                    cls.OPENCLAW_PHOTO_PROGRESS_NOTICE_REPEAT_SEC = float(value)
                elif key == "USERBOT_MAX_OUTPUT_TOKENS":
                    cls.USERBOT_MAX_OUTPUT_TOKENS = int(value)
                elif key == "USERBOT_PHOTO_MAX_OUTPUT_TOKENS":
                    cls.USERBOT_PHOTO_MAX_OUTPUT_TOKENS = int(value)
                elif key == "USERBOT_FORCE_CLOUD_FOR_PHOTO":
                    cls.USERBOT_FORCE_CLOUD_FOR_PHOTO = value.strip().lower() in (
                        "1",
                        "true",
                        "yes",
                    )
                elif key == "LM_STUDIO_NATIVE_REASONING_MODE":
                    cls.LM_STUDIO_NATIVE_REASONING_MODE = value.strip().lower()
                elif key == "LM_STUDIO_NATIVE_AUTO_CONTINUE_MAX_ROUNDS":
                    cls.LM_STUDIO_NATIVE_AUTO_CONTINUE_MAX_ROUNDS = int(value)
                elif key == "LM_STUDIO_NATIVE_OUTPUT_CAP_MARGIN":
                    cls.LM_STUDIO_NATIVE_OUTPUT_CAP_MARGIN = int(value)
                elif key == "AI_DISCLOSURE_ENABLED":
                    cls.AI_DISCLOSURE_ENABLED = value.strip().lower() in ("1", "true", "yes")
                elif key == "AI_DISCLOSURE_TEXT":
                    cls.AI_DISCLOSURE_TEXT = value
                elif key == "NON_OWNER_SAFE_MODE_ENABLED":
                    cls.NON_OWNER_SAFE_MODE_ENABLED = value.strip().lower() in ("1", "true", "yes")
                elif key == "NON_OWNER_SAFE_PROMPT":
                    cls.NON_OWNER_SAFE_PROMPT = value
                elif key == "STRIP_REPLY_TO_TAGS":
                    cls.STRIP_REPLY_TO_TAGS = value.strip().lower() in ("1", "true", "yes")
                elif key == "SCHEDULER_ENABLED":
                    cls.SCHEDULER_ENABLED = value.strip().lower() in ("1", "true", "yes")
                elif key == "DEFERRED_ACTION_GUARD_ENABLED":
                    cls.DEFERRED_ACTION_GUARD_ENABLED = value.strip().lower() in (
                        "1",
                        "true",
                        "yes",
                    )
                elif key == "GROUP_VOICE_FALLBACK_TRIGGER":
                    cls.GROUP_VOICE_FALLBACK_TRIGGER = value.strip().lower() in ("1", "true", "yes")
                elif key == "VOICE_MODE_DEFAULT":
                    cls.VOICE_MODE_DEFAULT = value.strip().lower() in ("1", "true", "yes")
                elif key == "VOICE_REPLY_SPEED":
                    cls.VOICE_REPLY_SPEED = float(value)
                elif key == "VOICE_REPLY_VOICE":
                    cls.VOICE_REPLY_VOICE = value.strip() or "ru-RU-DmitryNeural"
                elif key == "VOICE_REPLY_DELIVERY":
                    cls.VOICE_REPLY_DELIVERY = value.strip().lower() or "text+voice"
                elif key == "VOICE_REPLY_BLOCKED_CHATS":
                    cls.VOICE_REPLY_BLOCKED_CHATS = [
                        s.strip() for s in (value or "").split(",") if s.strip()
                    ]
                elif key == "TOOL_NARRATION_ENABLED":
                    cls.TOOL_NARRATION_ENABLED = value.strip().lower() in ("1", "true", "yes")
                elif key == "TELEGRAM_SESSION_HEARTBEAT_SEC":
                    cls.TELEGRAM_SESSION_HEARTBEAT_SEC = max(15, int(value))
                elif key == "GEMINI_API_KEY":
                    cls.GEMINI_API_KEY = value
                elif key == "GEMINI_PAID_KEY_ENABLED":
                    cls.GEMINI_PAID_KEY_ENABLED = value.strip().lower() in ("1", "true", "yes")
                elif key == "BRAVE_SEARCH_API_KEY":
                    cls.BRAVE_SEARCH_API_KEY = value
                elif key == "TELEGRAM_STREAM_UPDATE_INTERVAL_SEC":
                    cls.TELEGRAM_STREAM_UPDATE_INTERVAL_SEC = max(0.5, float(value))
                elif key == "TELEGRAM_REACTIONS_ENABLED":
                    cls.TELEGRAM_REACTIONS_ENABLED = value.strip().lower() in (
                        "1",
                        "true",
                        "yes",
                        "on",
                    )
                elif key == "DEFAULT_WEATHER_CITY":
                    cls.DEFAULT_WEATHER_CITY = value.strip()

            # Обновляем .env файл для сохранения между перезапусками
            env_path = cls.BASE_DIR / ".env"
            if not env_path.exists():
                # Попробуем создать из примера если нет (бэкап)
                example_path = cls.BASE_DIR / ".env.example"
                if example_path.exists():
                    import shutil

                    shutil.copy(example_path, env_path)
                else:
                    with open(env_path, "w") as f:
                        f.write("# Generated .env\n")

            lines = env_path.read_text().splitlines()
            found = False
            new_lines = []
            for line in lines:
                if line.strip().startswith(f"{key}="):
                    new_lines.append(f"{key}={value}")
                    found = True
                else:
                    new_lines.append(line)

            if not found:
                # Добавляем новую настройку в конец, но перед пустыми строками если можно
                new_lines.append(f"{key}={value}")

            env_path.write_text("\n".join(new_lines) + "\n")
            return True
        except Exception as e:
            print(f"Error updating config: {e}")
            return False

    @classmethod
    def load_swarm_team_accounts(cls) -> dict[str, dict[str, Any]]:
        """Загружает конфиг per-team Telegram аккаунтов для свёрма.

        Возвращает пустой dict если файл отсутствует или повреждён.
        """
        p = cls.SWARM_TEAM_ACCOUNTS_PATH
        if not p.exists():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            return data
        except Exception:
            return {}


# Синглтон для удобства
config = Config()


def emit_deprecation_warnings() -> None:
    """
    Логирует предупреждения об устаревших конфигурациях.
    Вызывается один раз при старте userbot'а.
    """
    owner_ids = getattr(config, "OWNER_USER_IDS", [])
    if owner_ids and os.getenv("OWNER_USER_IDS", "").strip():
        # env var присутствует и не пустой — предупреждаем
        msg = (
            "deprecated: OWNER_USER_IDS env var is set but will be removed in Session 20. "
            "Migrate owners to ACL json (~/.openclaw/krab_userbot_acl.json). "
            "See docs/OWNER_USER_IDS_DEPRECATION.md for migration guide."
        )
        _config_logger.warning("owner_user_ids_env_deprecated: %s", msg)
        warnings.warn(msg, DeprecationWarning, stacklevel=2)
