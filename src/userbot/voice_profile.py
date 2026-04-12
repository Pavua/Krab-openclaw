# -*- coding: utf-8 -*-
"""
Voice-profile mixin для `KraabUserbot`.

Третий шаг декомпозиции `src/userbot_bridge.py` (session 4+, 2026-04-09).
Содержит runtime-профиль голоса (скорость, голос, режим доставки),
per-chat voice blocklist, детекцию аудио-вложений, транскрипцию входящих
голосовых и fire-and-forget обновление chat-capability cache.

Замечания:
- `cls._voice_delivery_modes` — class-level frozenset, остаётся в `KraabUserbot`,
  доступен через MRO (cls → KraabUserbot).
- `self._looks_like_error_surface_text` — из `LLMTextProcessingMixin`, доступен
  через MRO (KraabUserbot наследует оба mixin-а).
- Module-level singletons (`config`, `chat_capability_cache`, `telegram_rate_limiter`,
  `model_manager`, `logger`) импортируются лениво внутри тел методов, чтобы
  избежать циклических зависимостей при старте.

См. `docs/USERBOT_BRIDGE_SPLIT_PROPOSAL.md` для полной стратегии разбиения.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any


class VoiceProfileMixin:
    """
    Голосовой runtime-профиль и voice-delivery логика.

    Mixin для `KraabUserbot`: нормализация voice-параметров, per-chat blocklist,
    определение voice/audio вложений, STT транскрипция, capability-cache refresh.
    """

    # ------------------------------------------------------------------
    # Нормализация voice-параметров (classmethods)
    # ------------------------------------------------------------------

    @classmethod
    def _normalize_voice_reply_speed(cls, value: Any) -> float:
        """
        Нормализует коэффициент скорости TTS.

        Почему clamp здесь:
        - команда `!voice speed` не должна ломать TTS мусорным значением;
        - сохраняем предсказуемый диапазон и для runtime, и для .env.
        """
        del cls
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = 1.5
        return max(0.75, min(2.5, round(numeric, 2)))

    @classmethod
    def _normalize_voice_reply_voice(cls, value: Any) -> str:
        """Возвращает непустой voice-id для edge-tts."""
        del cls
        normalized = str(value or "").strip()
        return normalized or "ru-RU-DmitryNeural"

    @classmethod
    def _normalize_voice_reply_delivery(cls, value: Any) -> str:
        """Нормализует режим доставки voice-ответа."""
        normalized = str(value or "").strip().lower()
        if normalized in cls._voice_delivery_modes:
            return normalized
        return "text+voice"

    # ------------------------------------------------------------------
    # Voice runtime profile (get / update)
    # ------------------------------------------------------------------

    def get_voice_runtime_profile(self) -> dict[str, Any]:
        """
        Возвращает живой профиль voice-runtime userbot.

        Это source-of-truth для команд, web API и handoff:
        - включена ли озвучка ответов;
        - какой голос/скорость/режим доставки активны;
        - готов ли входящий voice ingress через perceptor.
        """
        perceptor = getattr(self, "perceptor", None)
        perceptor_ready = bool(perceptor) and hasattr(perceptor, "transcribe")
        return {
            "enabled": bool(getattr(self, "voice_mode", False)),
            "delivery": self._normalize_voice_reply_delivery(
                getattr(self, "voice_reply_delivery", "text+voice")
            ),
            "speed": self._normalize_voice_reply_speed(getattr(self, "voice_reply_speed", 1.5)),
            "voice": self._normalize_voice_reply_voice(
                getattr(self, "voice_reply_voice", "ru-RU-DmitryNeural")
            ),
            "input_transcription_ready": perceptor_ready,
            "output_tts_ready": True,
            "live_voice_foundation": bool(perceptor_ready),
            # Per-chat voice blocklist — нужен renderer'у, owner UI и handoff,
            # чтобы не вычитывать config.VOICE_REPLY_BLOCKED_CHATS дважды.
            "blocked_chats": self.get_voice_blocked_chats(),
        }

    def update_voice_runtime_profile(
        self,
        *,
        enabled: Any | None = None,
        speed: Any | None = None,
        voice: Any | None = None,
        delivery: Any | None = None,
        persist: bool = False,
    ) -> dict[str, Any]:
        """
        Обновляет voice-профиль userbot и при необходимости сохраняет его в `.env`.

        Держим это в runtime-классе, а не в command handler:
        - web API и Telegram команды используют одну и ту же логику;
        - handoff/runtime-status не расходятся с фактическим поведением доставки.
        """
        # Ленивый импорт config — избегаем циклических зависимостей при import-time.
        from ..config import config  # noqa: PLC0415

        if enabled is not None:
            self.voice_mode = bool(enabled)
            if persist:
                config.update_setting("VOICE_MODE_DEFAULT", "1" if self.voice_mode else "0")
        if speed is not None:
            self.voice_reply_speed = self._normalize_voice_reply_speed(speed)
            if persist:
                config.update_setting("VOICE_REPLY_SPEED", str(self.voice_reply_speed))
        if voice is not None:
            self.voice_reply_voice = self._normalize_voice_reply_voice(voice)
            if persist:
                config.update_setting("VOICE_REPLY_VOICE", self.voice_reply_voice)
        if delivery is not None:
            self.voice_reply_delivery = self._normalize_voice_reply_delivery(delivery)
            if persist:
                config.update_setting("VOICE_REPLY_DELIVERY", self.voice_reply_delivery)
        return self.get_voice_runtime_profile()

    # ------------------------------------------------------------------
    # Voice delivery decisions
    # ------------------------------------------------------------------

    def _should_send_voice_reply(self) -> bool:
        """Определяет, нужно ли вообще генерировать TTS для текущего ответа."""
        return bool(self.voice_mode)

    def _should_send_full_text_reply(self) -> bool:
        """
        Определяет, нужен ли полный текстовый дубль вместе с voice.

        `voice-only` полезен для будущего live-режима и для чатов, где длинные
        текстовые полотна мешают. По умолчанию остаёмся в безопасном `text+voice`.
        """
        if not self._should_send_voice_reply():
            return True
        return self._normalize_voice_reply_delivery(self.voice_reply_delivery) != "voice-only"

    def _should_send_voice_for_response(self, text: str) -> bool:
        """
        Решает, нужно ли озвучивать конкретный ответ.

        Голос для полезного ответа нужен, а для transport/model fallback только
        мешает и создаёт странные голосовые вроде «No response from OpenClaw».
        """
        if not self._should_send_voice_reply():
            return False
        return not self._looks_like_error_surface_text(text)

    # ------------------------------------------------------------------
    # Per-chat voice blocklist
    # ------------------------------------------------------------------

    def _is_voice_blocked_for_chat(self, chat_id: Any) -> bool:
        """
        True → в этом чате голосовые ответы запрещены (per-chat blocklist).

        Причина: в некоторых группах модерация (или пользователи через report spam)
        метит TTS userbot как нежелательный → chat-level USER_BANNED_IN_CHANNEL,
        после которого Краб вообще не может туда ничего писать. Blocklist — это
        явный opt-out, чтобы Краб продолжал работать текстом, но не триггерил
        повторный бан голосом.

        Источник списка — `config.VOICE_REPLY_BLOCKED_CHATS`, он перечитывается
        на каждый вызов (через `getattr`), чтобы runtime-команды `!voice block`
        применялись без рестарта.
        """
        from ..config import config  # noqa: PLC0415

        blocked = getattr(config, "VOICE_REPLY_BLOCKED_CHATS", None) or []
        if not blocked:
            return False
        target = str(chat_id or "").strip()
        if not target:
            return False
        return target in {str(v).strip() for v in blocked}

    def get_voice_blocked_chats(self) -> list[str]:
        """
        Возвращает актуальный per-chat voice blocklist (копию).

        Копия, а не ссылка, чтобы случайный `.append` у caller'а не мутировал
        общий `config.VOICE_REPLY_BLOCKED_CHATS`. Реальные изменения идут только
        через `add_voice_blocked_chat` / `remove_voice_blocked_chat`.
        """
        from ..config import config  # noqa: PLC0415

        blocked = getattr(config, "VOICE_REPLY_BLOCKED_CHATS", None) or []
        return [str(v).strip() for v in blocked if str(v).strip()]

    def add_voice_blocked_chat(self, chat_id: Any, *, persist: bool = True) -> list[str]:
        """
        Добавляет `chat_id` в voice blocklist и persist'ит в `.env`.

        Идемпотентно: дубликат просто игнорируется. Возвращает актуальный список
        ПОСЛЕ операции, чтобы caller мог сразу отрендерить его пользователю.
        """
        from ..config import config  # noqa: PLC0415
        from ..core.logger import get_logger  # noqa: PLC0415

        logger = get_logger("krab.userbot")

        target = str(chat_id or "").strip()
        if not target:
            raise ValueError("chat_id required")
        current = self.get_voice_blocked_chats()
        if target not in current:
            current.append(target)
            if persist:
                config.update_setting("VOICE_REPLY_BLOCKED_CHATS", ",".join(current))
            else:
                config.VOICE_REPLY_BLOCKED_CHATS = list(current)
            logger.info("voice_blocklist_added", chat_id=target, size=len(current))
        return self.get_voice_blocked_chats()

    def remove_voice_blocked_chat(self, chat_id: Any, *, persist: bool = True) -> list[str]:
        """
        Убирает `chat_id` из voice blocklist и persist'ит в `.env`.

        Идемпотентно: если элемента нет — просто возвращает текущий список.
        """
        from ..config import config  # noqa: PLC0415
        from ..core.logger import get_logger  # noqa: PLC0415

        logger = get_logger("krab.userbot")

        target = str(chat_id or "").strip()
        if not target:
            raise ValueError("chat_id required")
        current = self.get_voice_blocked_chats()
        if target in current:
            current = [v for v in current if v != target]
            if persist:
                config.update_setting("VOICE_REPLY_BLOCKED_CHATS", ",".join(current))
            else:
                config.VOICE_REPLY_BLOCKED_CHATS = list(current)
            logger.info("voice_blocklist_removed", chat_id=target, size=len(current))
        return self.get_voice_blocked_chats()

    # ------------------------------------------------------------------
    # Chat capability refresh (fire-and-forget)
    # ------------------------------------------------------------------

    async def _refresh_chat_capabilities_background(self, chat_id: Any) -> None:
        """
        Fire-and-forget fetch `client.get_chat(chat_id)` → upsert в capability cache.

        Вызывается асинхронно из `_process_message`, но результат нигде не
        блокирует — точно как chat_ban_cache marking в `_finish_ai_request_background`.
        Это и есть весь смысл B.6: один раз в TTL (24ч default) мы платим
        API-запросом `get_chat` чтобы дальше hot-path voice/text decisions
        работал без вопросов к Telegram.

        Fire-and-forget: exceptions не валят flow, они просто логгируются
        как `chat_capability_refresh_failed`.
        """
        from ..core.chat_capability_cache import chat_capability_cache  # noqa: PLC0415
        from ..core.logger import get_logger  # noqa: PLC0415
        from ..core.telegram_rate_limiter import telegram_rate_limiter  # noqa: PLC0415

        logger = get_logger("krab.userbot")

        target = str(chat_id or "").strip()
        if not target:
            return
        # Если в cache уже есть свежая запись (до TTL), ничего не делаем.
        if chat_capability_cache.get(target) is not None:
            return
        client = getattr(self, "client", None)
        get_chat_fn = getattr(client, "get_chat", None) if client is not None else None
        if not callable(get_chat_fn):
            return
        try:
            # B.7: даже capability refresh идёт через global rate limiter,
            # чтобы массовый first-sight fetch нескольких чатов подряд не
            # триггерил FloodWait. refresh не-critical, ждать можно.
            await telegram_rate_limiter.acquire(purpose="get_chat_capability")
            # Можно передавать int или str — pyrofork resolve'ит оба.
            try:
                chat_obj = await get_chat_fn(int(target))
            except (TypeError, ValueError):
                chat_obj = await get_chat_fn(target)
        except Exception as exc:  # noqa: BLE001
            # B.9.3 (silent-failure-hunter review): различаем severity по типу
            # ошибки. Раньше всё шло в debug, что прятало session revoke /
            # auth key unregistered — критичные события нельзя оставлять в
            # debug потоке, их должен видеть owner.
            error_type_name = type(exc).__name__
            # Критичные session/auth события → error. При них Краб фактически
            # перестаёт работать до ручного восстановления сессии.
            if error_type_name in {
                "AuthKeyUnregistered",
                "SessionRevoked",
                "SessionExpired",
                "UserDeactivated",
                "UserDeactivatedBan",
            }:
                logger.error(
                    "chat_capability_refresh_session_error",
                    chat_id=target,
                    error=str(exc),
                    error_type=error_type_name,
                )
            # FloodWait → warning. Не критично, но означает что rate limiter
            # не справился и Telegram нас тормозит принудительно.
            elif error_type_name == "FloodWait":
                logger.warning(
                    "chat_capability_refresh_flood_wait",
                    chat_id=target,
                    error=str(exc),
                )
            # Остальное (ChannelPrivate, ChatAdminRequired, PeerIdInvalid,
            # ValueError на приватках с ID, etc.) — ожидаемо → debug.
            # Это именно тот класс ошибок для которого debug severity правильная.
            else:
                logger.debug(
                    "chat_capability_refresh_failed",
                    chat_id=target,
                    error=str(exc),
                    error_type=error_type_name,
                )
            return
        try:
            chat_capability_cache.upsert_from_chat(chat_obj)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "chat_capability_upsert_failed",
                chat_id=target,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Audio detection and transcription
    # ------------------------------------------------------------------

    @staticmethod
    def _message_has_audio(message) -> bool:
        """Определяет voice/audio attachment, который можно отдать в STT."""
        return bool(getattr(message, "voice", None) or getattr(message, "audio", None))

    @staticmethod
    def _voice_download_suffix(message) -> str:
        """Подбирает расширение для временного voice/audio файла."""
        voice = getattr(message, "voice", None)
        if voice:
            return ".ogg"
        audio = getattr(message, "audio", None)
        if audio:
            file_name = str(getattr(audio, "file_name", "") or "").strip()
            suffix = Path(file_name).suffix.strip()
            if suffix:
                return suffix if suffix.startswith(".") else f".{suffix}"
        return ".ogg"

    async def _transcribe_audio_message(self, message) -> tuple[str, str]:
        """
        Скачивает входящее аудио и прогоняет его через Perceptor.

        Возвращает `(текст, ошибка)`, чтобы вызывающий код мог честно показать
        пользователю реальную причину сбоя, а не маскировать её placeholder-ом.
        """
        from ..config import config  # noqa: PLC0415
        from ..core.logger import get_logger  # noqa: PLC0415
        from ..model_manager import model_manager  # noqa: PLC0415

        logger = get_logger("krab.userbot")

        perceptor = getattr(self, "perceptor", None)
        if not perceptor or not hasattr(perceptor, "transcribe"):
            return "", "❌ Голосовой контур сейчас не подключён. Нужен активный perceptor/STT."
        if not self.client:
            return "", "❌ Telegram client не готов к загрузке аудио."

        voice_dir = config.BASE_DIR / "data" / "voice_inbox"
        voice_dir.mkdir(parents=True, exist_ok=True)
        message_id = int(getattr(message, "id", 0) or 0)
        file_path = voice_dir / (
            f"voice_{int(time.time() * 1000)}_{message_id}{self._voice_download_suffix(message)}"
        )
        download_timeout_sec = float(getattr(config, "VOICE_DOWNLOAD_TIMEOUT_SEC", 45.0))
        stt_timeout_sec = float(
            max(
                20.0,
                float(getattr(perceptor, "stt_worker_timeout_seconds", 240) or 240) + 15.0,
            )
        )
        saved_path = file_path

        try:
            downloaded = await asyncio.wait_for(
                self.client.download_media(message, file_name=str(file_path)),
                timeout=max(5.0, download_timeout_sec),
            )
            if downloaded:
                saved_path = Path(str(downloaded))
            transcript = await asyncio.wait_for(
                perceptor.transcribe(str(saved_path), model_manager),
                timeout=stt_timeout_sec,
            )
            normalized = str(transcript or "").strip()
            if not normalized:
                return "", "❌ Не удалось распознать голосовое сообщение."
            if normalized.lower().startswith("ошибка транскрибации"):
                return "", f"❌ {normalized}"
            return normalized, ""
        except asyncio.TimeoutError:
            return "", "❌ Таймаут обработки голосового сообщения. Попробуй отправить его ещё раз."
        except Exception as exc:  # noqa: BLE001
            logger.error("voice_message_transcription_failed", error=str(exc))
            return "", "❌ Ошибка обработки голосового сообщения. Попробуй отправить его ещё раз."
        finally:
            try:
                if saved_path.exists():
                    saved_path.unlink()
            except Exception:
                pass
