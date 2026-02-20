# -*- coding: utf-8 -*-
"""
Tools Handler — Инструменты: поиск, новости, перевод, TTS.

Извлечён из main.py. Включает:
- !scout: Deep Research (Web Search)
- !nexus: Extended research report
- !news: Дайджест новостей
- !translate: Перевод RU↔EN
- !say / !voice: TTS
- !callstart / !callstop / !callstatus: управление voice-сессией
- !notify / !calllang: runtime-настройки активной voice-сессии
- !callcost: оценка телеком + AI расходов по минутам
- !calldiag / !callsummary / !callphrase: диагностика, summary и быстрые реплики звонка
- !callwhy / !callphrases / !calltune: explain-диагностика, библиотека фраз и тюнинг runtime
"""

from pyrogram import filters, enums
from pyrogram.types import Message

from .auth import is_authorized

import structlog
logger = structlog.get_logger(__name__)

active_call_sessions: dict[int, str] = {}


def register_handlers(app, deps: dict):
    """Регистрирует обработчики инструментов."""
    router = deps["router"]
    # scout = deps["scout"]  # Deprecated
    safe_handler = deps["safe_handler"]
    openclaw = deps.get("openclaw_client")
    voice_gateway = deps.get("voice_gateway_client")
    config_manager = deps.get("config_manager")

    # --- !scout: Deep Research ---
    @app.on_message(filters.command("scout", prefixes="!"))
    @safe_handler
    async def scout_command(client, message: Message):
        """Deep Research: !scout <тема>"""
        if not openclaw:
            await message.reply_text("❌ OpenClaw client не инициализирован.")
            return

        if len(message.command) < 2:
            await message.reply_text(
                "🔎 Что исследовать? `!scout Квантовые вычисления 2025`"
            )
            return

        query = message.text.split(" ", 1)[1]
        # Единая логика для Deep Research / Nexus Intelligence
        await _process_research_task(
            client=client,
            message=message,
            openclaw=openclaw,
            query=query,
            mode="scout"
        )

    # --- !nexus: Extended Research ---
    @app.on_message(filters.command("nexus", prefixes="!"))
    @safe_handler
    async def nexus_command(client, message: Message):
        """Nexus Intelligence Report: !nexus <тема>"""
        if not openclaw:
            await message.reply_text("❌ OpenClaw client не инициализирован.")
            return

        if len(message.command) < 2:
            await message.reply_text(
                "🕵️ Что исследовать? `!nexus Криптовалюты и регуляция 2025`"
            )
            return

        query = message.text.split(" ", 1)[1]
        notification = await message.reply_text("🕵️‍♂️ **Nexus Intelligence: сканирую...**")

        # Единая логика для Deep Research / Nexus Intelligence
        await _process_research_task(
            client=client,
            message=message,
            openclaw=openclaw,
            query=query,
            mode="nexus"
        )

    # --- !news: Дайджест новостей ---
    @app.on_message(filters.command("news", prefixes="!"))
    @safe_handler
    async def news_command(client, message: Message):
        """Fresh News: !news <запрос>"""
        if not openclaw:
            await message.reply_text("❌ OpenClaw client не инициализирован.")
            return

        query = (
            "Криптовалюты"
            if len(message.command) < 2
            else message.text.split(" ", 1)[1]
        )
        notification = await message.reply_text(
            f"🗞️ Ищу свежие новости по теме `{query}`..."
        )

        # Use OpenClaw for news search (via web_search tool)
        logger.info(f"News Search via OpenClaw: {query}")
        
        try:
            # 1. Search recent news
            search_results = await openclaw.invoke_tool("web_search", {
                "query": f"news about {query}", 
                "count": 5,
                "freshness": "pd" # Past Day (Brave specific, might need check if supported by OpenClaw wrapper)
            })
            
            results_data = search_results.get("details", {}).get("results", [])
            # Fallback parsing if needed (same as in execute_agent_task)
            if not results_data and "content" in search_results:
                 try:
                     import json
                     text = search_results["content"][0]["text"]
                     parsed = json.loads(text)
                     results_data = parsed.get("results", [])
                 except:
                     pass

            if not results_data:
                await notification.edit_text("❌ Не удалось найти свежих новостей через OpenClaw.")
                return

            formatted_news = ""
            for i, res in enumerate(results_data, 1):
                if isinstance(res, dict):
                    title = res.get('title', 'No Title').replace("<<<EXTERNAL_UNTRUSTED_CONTENT>>>", "").strip()
                    url = res.get('url', '#')
                    date = res.get('published', 'Unknown date')
                    formatted_news += f"{i}. [{title}]({url}) ({date})\n"
                else:
                    formatted_news += f"{i}. {str(res)}\n"
            
            await notification.edit_text("🧠 **Анализирую новости...**")

            prompt = (
                f"Составь краткий дайджест самых свежих новостей по теме '{query}' "
                f"на основе этих заголовков:\n\n{formatted_news}\n\n"
                "Выдели главное. Используй Markdown."
            )
            
            # Use Router for summary to ensure consistency and logging
            summary = await router.route_query(prompt, task_type="summary")

            await notification.edit_text(
                f"🗞️ **Fresh News Digest: {query}**\n\n{summary}"
            )
            
        except Exception as e:
            logger.error(f"News command error: {e}")
            await notification.edit_text(f"❌ Ошибка: {e}")

    # --- !translate: Перевод ---
    @app.on_message(filters.command("translate", prefixes="!"))
    @safe_handler
    async def translate_command(client, message: Message):
        """Перевод текста: !translate <текст>"""
        if len(message.command) < 2:
            await message.reply_text(
                "🌐 Введи текст для перевода: `!translate Hello world`"
            )
            return

        text = message.text.split(" ", 1)[1]
        notification = await message.reply_text("🌐 **Перевожу...**")

        # Определяем направление: RU→EN или EN→RU
        prompt = (
            f"Переведи следующий текст. Если текст на русском — переведи на английский, "
            f"если на другом языке — переведи на русский.\n\nТекст: {text}"
        )

        translated = await router.route_query(prompt, task_type="chat")
        await notification.edit_text(f"🌐 **Перевод:**\n\n{translated}")

    # --- !say / !voice: TTS ---
    @app.on_message(filters.command(["say", "voice"], prefixes="!"))
    @safe_handler
    async def say_command(client, message: Message):
        """Text-to-Speech: !say <текст>"""
        if len(message.command) < 2:
            await message.reply_text("🗣️ Что сказать? `!say Привет, мир!`")
            return

        text = message.text.split(" ", 1)[1]
        notification = await message.reply_text("🔊 **Генерирую голос...**")

        try:
            perceptor = deps["perceptor"]
            audio_path = None
            # Совместимость: в текущем перцепторе основной метод называется speak.
            if hasattr(perceptor, "speak"):
                audio_path = await perceptor.speak(text)
            elif hasattr(perceptor, "text_to_speech"):
                audio_path = await perceptor.text_to_speech(text)

            if audio_path:
                await message.reply_voice(audio_path)
                await notification.edit_text("🔊 **Готово!**")
                import os
                if os.path.exists(audio_path):
                    os.remove(audio_path)
            else:
                await notification.edit_text("❌ TTS недоступен.")

        except Exception as e:
            logger.error(f"TTS error: {e}")
            await notification.edit_text(f"❌ Ошибка TTS: {e}")

    async def _reply_voice_gateway_error(message: Message, error_type: str, details: str = None):
        """
        Внутренний хелпер для формирования стандартизированных ответов об ошибках Voice Gateway (UX v2).
        Поддерживает коды VGW_* для операторской диагностики.
        """
        if details:
            # Безопасное экранирование: заменяем ` на ', чтобы не ломать Markdown блоки.
            details = str(details).replace("`", "'")

        # Маппинг внутренних типов на операторские коды и подсказки
        vgw_map = {
            "unavailable": {
                "code": "VGW_UNAVAILABLE",
                "text": "Voice Gateway недоступен (Connection Refused).",
                "tip": "Убедитесь, что сервис voice-gateway запущен и порт 8090 проброшен."
            },
            "no_session": {
                "code": "VGW_SESSION_ERR",
                "text": "Активная voice-сессия не найдена.",
                "tip": "Используйте `!callstart` для инициализации новой сессии."
            },
            "http_401": {
                "code": "VGW_AUTH_FAIL",
                "text": "Ошибка авторизации (Invalid API Key).",
                "tip": "Проверьте VOICE_GATEWAY_API_KEY в конфигурации."
            },
            "http_404": {
                "code": "VGW_NOT_FOUND",
                "text": "Ресурс или сессия не найдены на стороне шлюза.",
                "tip": "Возможно, сессия была завершена по таймауту на сервере."
            },
            "timeout": {
                "code": "VGW_TIMEOUT",
                "text": "Превышено время ожидания ответа от Voice Gateway.",
                "tip": "Проверьте нагрузку на сервис или сетевое соединение."
            },
            "update_fail": {
                "code": "VGW_UPDATE_ERR",
                "text": "Не удалось обновить параметры сессии.",
                "tip": "Проверьте валидность передаваемых параметров (mode/notify)."
            },
            "generic": {
                "code": "VGW_INTERNAL",
                "text": "Внутренняя ошибка шлюза или неожиданный ответ.",
                "tip": "Изучите логи Voice Gateway для уточнения причины."
            }
        }

        # Определяем ключ для маппинга
        map_key = error_type
        if error_type.startswith("http_"):
            if error_type == "http_401" or error_type == "http_403":
                map_key = "http_401"
            elif error_type == "http_404":
                map_key = "http_404"
            else:
                map_key = "generic"
        elif "timeout" in str(error_type).lower() or "connect" in str(error_type).lower():
            map_key = "timeout"
        
        entry = vgw_map.get(map_key, vgw_map["generic"])
        
        res_details = f"\n🛡️ **Детали:** `{details}`" if details else ""
        
        text = (
            f"❌ **Ошибка: {entry['code']}**\n"
            f"📝 {entry['text']}{res_details}\n\n"
            f"💡 **Подсказка:** {entry['tip']}"
        )
            
        await message.reply_text(text)

    # --- !callstart: запуск звонковой сессии через Voice Gateway ---
    @app.on_message(filters.command("callstart", prefixes="!"))
    @safe_handler
    async def callstart_command(client, message: Message):
        """Запускает сессию звонкового ассистента."""
        if not voice_gateway:
            await _reply_voice_gateway_error(message, "unavailable")
            return

        mode = "auto_to_ru"
        if len(message.command) >= 2:
            candidate = message.command[1].strip().lower()
            if candidate in {"auto_to_ru", "ru_es_duplex"}:
                mode = candidate

        source = "mic"
        if len(message.command) >= 3:
            candidate_source = message.command[2].strip().lower()
            if candidate_source in {"mic", "system_audio", "mic_plus_system"}:
                source = candidate_source

        notify_mode = "auto_on"
        tts_mode = "hybrid"
        if len(message.command) >= 4:
            for raw_arg in message.command[3:]:
                arg = raw_arg.strip().lower()
                if arg in {"on", "off"}:
                    notify_mode = "auto_on" if arg == "on" else "auto_off"
                elif arg in {"local", "cloud", "hybrid"}:
                    tts_mode = arg

        notification = await message.reply_text("📞 Запускаю voice-сессию...")
        result = await voice_gateway.start_session(
            translation_mode=mode,
            notify_mode=notify_mode,
            tts_mode=tts_mode,
            source=source,
        )
        if not result.get("ok"):
            error_details = f"Не удалось запустить сессию. 🛡️ Детали: `{result.get('error', 'unknown')}`"
            await notification.edit_text(
                f"❌ **Ошибка:** {error_details}\n\n"
                "💡 **Подсказка:** Проверьте логи Voice Gateway. Сервис может быть offline."
            )
            return

        payload = result.get("result", {})
        session_id = str(payload.get("id", "")).strip()
        if session_id:
            active_call_sessions[message.chat.id] = session_id
            if config_manager:
                config_manager.set("runtime.last_session_id", session_id)
        await notification.edit_text(
            "✅ Сессия запущена\n"
            f"- session_id: `{session_id}`\n"
            f"- mode: `{payload.get('translation_mode', mode)}`\n"
            f"- source: `{payload.get('source', source)}`\n"
            f"- notify: `{payload.get('notify_mode', notify_mode)}`\n"
            f"- tts: `{payload.get('tts_mode', tts_mode)}`"
        )

    # --- !callstop: остановка активной сессии ---
    @app.on_message(filters.command("callstop", prefixes="!"))
    @safe_handler
    async def callstop_command(client, message: Message):
        """Останавливает активную звонковую сессию."""
        if not voice_gateway:
            await _reply_voice_gateway_error(message, "unavailable")
            return
        session_id = active_call_sessions.get(message.chat.id)
        if not session_id:
            await _reply_voice_gateway_error(message, "no_session")
            return
        result = await voice_gateway.stop_session(session_id)
        if not result.get("ok"):
            await _reply_voice_gateway_error(message, "update_fail", details=result.get("error"))
            return
        active_call_sessions.pop(message.chat.id, None)
        await message.reply_text(f"🛑 Сессия остановлена: `{session_id}`")

    # --- !callstatus: статус активной сессии ---
    @app.on_message(filters.command("callstatus", prefixes="!"))
    @safe_handler
    async def callstatus_command(client, message: Message):
        """Показывает статус текущей звонковой сессии."""
        if not voice_gateway:
            await _reply_voice_gateway_error(message, "unavailable")
            return
        session_id = active_call_sessions.get(message.chat.id)
        if not session_id:
            await _reply_voice_gateway_error(message, "no_session")
            return
        result = await voice_gateway.get_session(session_id)
        if not result.get("ok"):
            await _reply_voice_gateway_error(message, "generic", details=f"Не удалось получить статус. 🛡️ Детали: `{result.get('error', 'unknown')}`")
            return
        state = result.get("result", {})
        # Добавляем детали состояния
        source = state.get('source', 'unknown')
        status = state.get('status', 'unknown')

        normalized_status = "running" if status in {"running", "active"} else "created" if status == "created" else status

        # Информативный статус
        status_icon = "🟢" if normalized_status == "running" else "🟡" if normalized_status == "created" else "🔴"
        health_text = "Активна" if normalized_status == "running" else "Ожидание" if normalized_status == "created" else "Завершена"
        health_suffix = "(🟢 OK)" if normalized_status == "running" else ""

        await message.reply_text(
            f"{status_icon} **Voice Session Status: {health_text}** {health_suffix}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 ID: `{state.get('id', session_id)}`\n"
            f"📡 Источник: `{source}`\n"
            f"🔹 source: `{source}`\n"
            f"🌍 Режим: `{state.get('translation_mode', 'n/a')}`\n"
            f"🔔 Уведомления: `{state.get('notify_mode', 'n/a')}`\n"
            f"🎙️ TTS: `{state.get('tts_mode', 'n/a')}`\n"
            f"⏱️ Обновлено: `{state.get('updated_at', 'n/a')}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 *Для диагностики: `!calldiag`*"
        )

    # --- !notify: переключение уведомления собеседника ---
    @app.on_message(filters.command("notify", prefixes="!"))
    @safe_handler
    async def notify_command(client, message: Message):
        """Меняет notify-mode активной сессии: !notify on|off."""
        if not voice_gateway:
            await _reply_voice_gateway_error(message, "unavailable")
            return
        if len(message.command) < 2:
            await message.reply_text("ℹ️ Использование: `!notify on` или `!notify off`")
            return
        session_id = active_call_sessions.get(message.chat.id)
        if not session_id:
            await _reply_voice_gateway_error(message, "no_session")
            return
        raw = message.command[1].strip().lower()
        if raw not in {"on", "off"}:
            await message.reply_text("⚠️ Допустимо только: `on` или `off`.")
            return
        notify_mode = "auto_on" if raw == "on" else "auto_off"
        result = await voice_gateway.set_notify_mode(session_id, notify_mode=notify_mode)
        if not result.get("ok"):
            await _reply_voice_gateway_error(message, "update_fail", details=result.get("error"))
            return
        await message.reply_text(f"✅ notify_mode обновлён: `{notify_mode}`")

    # --- !calllang: смена режима перевода в активной сессии ---
    @app.on_message(filters.command("calllang", prefixes="!"))
    @safe_handler
    async def calllang_command(client, message: Message):
        """Меняет translation mode: !calllang auto_to_ru|ru_es_duplex."""
        if not voice_gateway:
            await _reply_voice_gateway_error(message, "unavailable")
            return
        if len(message.command) < 2:
            await message.reply_text("ℹ️ Использование: `!calllang auto_to_ru` или `!calllang ru_es_duplex`")
            return
        session_id = active_call_sessions.get(message.chat.id)
        if not session_id:
            await _reply_voice_gateway_error(message, "no_session")
            return
        mode = message.command[1].strip().lower()
        if mode not in {"auto_to_ru", "ru_es_duplex"}:
            await message.reply_text("⚠️ Допустимо: `auto_to_ru` или `ru_es_duplex`.")
            return
        result = await voice_gateway.set_translation_mode(session_id, translation_mode=mode)
        if not result.get("ok"):
            await _reply_voice_gateway_error(message, "update_fail", details=result.get("error"))
            return
        await message.reply_text(f"✅ translation_mode обновлён: `{mode}`")

    # --- !callcost: оценка telephony + AI стоимости через Voice Gateway ---
    @app.on_message(filters.command("callcost", prefixes="!"))
    @safe_handler
    async def callcost_command(client, message: Message):
        """Считает бюджет звонков: !callcost [country] [inbound] [landline] [mobile] [media] [live|offline]."""
        if not voice_gateway:
            await _reply_voice_gateway_error(message, "unavailable")
            return

        country = "ES"
        inbound = 200.0
        outbound_landline = 100.0
        outbound_mobile = 100.0
        media = 400.0
        use_live = True

        args = message.command[1:]
        if len(args) >= 1 and args[0].strip():
            raw_country = args[0].strip().upper()
            if len(raw_country) == 2 and raw_country.isalpha():
                country = raw_country
        if len(args) >= 2:
            try:
                inbound = max(0.0, float(args[1]))
            except Exception:
                inbound = 200.0
        if len(args) >= 3:
            try:
                outbound_landline = max(0.0, float(args[2]))
            except Exception:
                outbound_landline = 100.0
        if len(args) >= 4:
            try:
                outbound_mobile = max(0.0, float(args[3]))
            except Exception:
                outbound_mobile = 100.0
        if len(args) >= 5:
            try:
                media = max(0.0, float(args[4]))
            except Exception:
                media = 400.0
        if len(args) >= 6:
            mode = args[5].strip().lower()
            if mode in {"offline", "manual", "false", "0"}:
                use_live = False

        result = await voice_gateway.estimate_cost(
            country=country,
            minutes_inbound=inbound,
            minutes_outbound_landline=outbound_landline,
            minutes_outbound_mobile=outbound_mobile,
            minutes_media_stream=media,
            use_live_pricing=use_live,
        )
        if not result.get("ok"):
            await _reply_voice_gateway_error(message, "generic", details=f"Не удалось получить оценку. 🛡️ Детали: `{result.get('error', 'unknown')}`")
            return

        payload = result.get("result", {})
        rates_source = payload.get("rates_source", "unknown")
        rates_note = payload.get("rates_note", "")
        telephony = payload.get("telephony_usd", {}) if isinstance(payload.get("telephony_usd"), dict) else {}
        ai = payload.get("ai_usd", {}) if isinstance(payload.get("ai_usd"), dict) else {}
        total = payload.get("total_usd", 0)

        note_line = f"\nℹ️ note: `{rates_note}`" if rates_note else ""
        await message.reply_text(
            "💸 **Call Cost Estimate**\n"
            f"- country: `{payload.get('country', country)}`\n"
            f"- rates_source: `{rates_source}`{note_line}\n"
            f"- telephony_total_usd: `{telephony.get('total', 0)}`\n"
            f"- ai_total_usd: `{ai.get('total', 0)}`\n"
            f"- total_usd: `{total}`\n\n"
            "Пример:\n"
            "`!callcost ES 220 110 140 470 live`\n"
            "`!callcost ES 220 110 140 470 offline`"
        )

    # --- !calldiag: диагностика активной voice-сессии ---
    @app.on_message(filters.command("calldiag", prefixes="!"))
    @safe_handler
    async def calldiag_command(client, message: Message):
        """Показывает диагностику звонковой сессии (latency/counters/fallback/cache)."""
        if not voice_gateway:
            await _reply_voice_gateway_error(message, "unavailable")
            return
        session_id = active_call_sessions.get(message.chat.id)
        if not session_id:
            await _reply_voice_gateway_error(message, "no_session")
            return
        result = await voice_gateway.get_diagnostics(session_id)
        if not result.get("ok"):
            await _reply_voice_gateway_error(message, "generic", details=f"Не удалось получить диагностику. 🛡️ Детали: `{result.get('error', 'unknown')}`")
            return
        
        payload = result.get("result", {})
        pipeline = payload.get("pipeline", {}) if isinstance(payload.get("pipeline"), dict) else {}
        counters = payload.get("counters", {}) if isinstance(payload.get("counters"), dict) else {}
        lat = payload.get("latency_ms", {}) if isinstance(payload.get("latency_ms"), dict) else {}

        await message.reply_text(
            "🩺 **Call Diagnostics**\n"
            f"- session: `{payload.get('session_id', session_id)}`\n"
            f"- status: `{payload.get('status', 'unknown')}`\n"
            f"- timeline: `{payload.get('timeline_size', 0)}`\n"
            f"- cache: hits `{pipeline.get('cache_hits', 0)}` / miss `{pipeline.get('cache_misses', 0)}`\n"
            f"- fallback: `{pipeline.get('last_fallback', '-')}`\n"
            f"- stt.partial: `{counters.get('stt_partial', 0)}`\n"
            f"- translation.partial: `{counters.get('translation_partial', 0)}`\n"
            f"- tts.ready: `{counters.get('tts_ready', 0)}`\n"
            f"- avg stt ms: `{lat.get('stt_partial', '-')}`\n"
            f"- avg tr ms: `{lat.get('translation_partial', '-')}`\n"
            f"- avg tts ms: `{lat.get('tts_ready', '-')}`\n\n"
            "💡 **Что делать дальше:**\n"
            "- Если `stt.partial` мало: проверьте микрофон/source.\n"
            "- Если `tts.ready` отстает: попробуйте `!calltune low`.\n"
            "- Для деталей: `!callwhy`."
        )

    # --- !callsummary: summary активной voice-сессии ---
    @app.on_message(filters.command("callsummary", prefixes="!"))
    @safe_handler
    async def callsummary_command(client, message: Message):
        """Генерирует краткую сводку звонка: !callsummary [max_items]."""
        if not voice_gateway:
            await _reply_voice_gateway_error(message, "unavailable")
            return
        session_id = active_call_sessions.get(message.chat.id)
        if not session_id:
            await _reply_voice_gateway_error(message, "no_session")
            return

        max_items = 30
        if len(message.command) >= 2:
            try:
                max_items = int(message.command[1].strip())
            except Exception:
                max_items = 30
        max_items = max(1, min(max_items, 120))

        result = await voice_gateway.build_summary(session_id, max_items=max_items)
        if not result.get("ok"):
            await _reply_voice_gateway_error(message, "generic", details=f"Не удалось собрать summary. 🛡️ Детали: `{result.get('error', 'unknown')}`")
            return

        payload = result.get("result", {})
        summary_text = str(payload.get("summary", "")).strip() or "—"
        tasks = payload.get("tasks", [])
        if isinstance(tasks, list) and tasks:
            tasks_block = "\n".join(f"• {str(task)}" for task in tasks[:8])
        else:
            tasks_block = "_Задач не обнаружено_"

        await message.reply_text(
            "🧾 **Call Intelligent Summary**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 `{payload.get('session_id', session_id)}` | 🎙️ `{payload.get('items_used', 0)}` чанков\n\n"
            f"📝 **Суть разговора:**\n{summary_text}\n\n"
            f"✅ **Action Items:**\n{tasks_block}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📢 *Summary сгенерирован автоматически AI-ассистентом.*"
        )

    # --- !callphrase: быстрый перевод + озвучка ---
    @app.on_message(filters.command("callphrase", prefixes="!"))
    @safe_handler
    async def callphrase_command(client, message: Message):
        """Быстрая фраза: !callphrase <текст> [ru->es|es->ru]."""
        if not voice_gateway:
            await message.reply_text(
                "❌ **Ошибка:** Voice Gateway недоступен.\n\n"
                "💡 **Подсказка:** Убедитесь, что сервис voice-gateway запущен."
            )
            return
        session_id = active_call_sessions.get(message.chat.id)
        if not session_id:
            await _reply_voice_gateway_error(message, "no_session")
            return
        if len(message.command) < 2:
            await message.reply_text("ℹ️ Использование: `!callphrase <текст> [ru->es|es->ru]`")
            return

        text = message.text.split(" ", 1)[1].strip()
        if not text:
            await message.reply_text("⚠️ Пустой текст. Пример: `!callphrase Говорите медленнее, пожалуйста`")
            return

        source_lang = "ru"
        target_lang = "es"
        if text.endswith(" ru->es"):
            text = text[:-7].strip()
            source_lang, target_lang = "ru", "es"
        elif text.endswith(" es->ru"):
            text = text[:-7].strip()
            source_lang, target_lang = "es", "ru"

        if not text:
            await message.reply_text("⚠️ После направления не осталось текста.")
            return

        result = await voice_gateway.quick_phrase(
            session_id=session_id,
            text=text,
            source_lang=source_lang,
            target_lang=target_lang,
            voice="default",
            style="chat",
        )
        if not result.get("ok"):
            await _reply_voice_gateway_error(message, "generic", details=f"Не удалось отправить фразу. 🛡️ Детали: `{result.get('error', 'unknown')}`")
            return

        payload = result.get("result", {})
        await message.reply_text(
            "⚡ **Quick Phrase**\n"
            f"- from: `{source_lang}` -> `{target_lang}`\n"
            f"- source: {payload.get('source_text', text)}\n"
            f"- translated: {payload.get('translated_text', '—')}\n"
            f"- audio: `{payload.get('audio_url', '-')}`\n"
            f"- cache_hit: `{payload.get('cache_hit', False)}`"
        )

    # --- !callphrases: библиотека быстрых фраз ---
    @app.on_message(filters.command("callphrases", prefixes="!"))
    @safe_handler
    async def callphrases_command(client, message: Message):
        """Показывает библиотеку быстрых фраз: !callphrases [ru->es|es->ru]."""
        if not voice_gateway:
            await message.reply_text(
                "❌ **Ошибка:** Voice Gateway недоступен.\n\n"
                "💡 **Подсказка:** Убедитесь, что сервис voice-gateway запущен."
            )
            return
        direction = "ru->es"
        if len(message.command) >= 2 and message.command[1].strip().lower() in {"ru->es", "es->ru"}:
            direction = message.command[1].strip().lower()
        source_lang = "ru" if direction == "ru->es" else "es"
        target_lang = "es" if direction == "ru->es" else "ru"

        result = await voice_gateway.list_quick_phrases(
            source_lang=source_lang,
            target_lang=target_lang,
            category="all",
            limit=12,
        )
        if not result.get("ok"):
            await _reply_voice_gateway_error(message, "update_fail", details=result.get("error"))
            return
        payload = result.get("result", {})
        items = payload.get("items", []) if isinstance(payload.get("items"), list) else []
        if not items:
            await message.reply_text("ℹ️ Библиотека быстрых фраз пуста.")
            return

        lines = []
        for idx, item in enumerate(items[:10], start=1):
            text = str(item.get("source_text", "")).strip()
            trans = str(item.get("translated_text", "")).strip()
            lines.append(f"{idx}. {text}\n   → {trans}")
        await message.reply_text(
            "📚 **Quick Phrases**\n"
            f"- direction: `{direction}`\n"
            f"- count: `{payload.get('count', len(items))}`\n\n"
            + "\n".join(lines)
        )

    # --- !callwhy: explain диагностика ---
    @app.on_message(filters.command("callwhy", prefixes="!"))
    @safe_handler
    async def callwhy_command(client, message: Message):
        """Объясняет причину отсутствия перевода в активной сессии."""
        if not voice_gateway:
            await message.reply_text(
                "❌ **Ошибка:** Voice Gateway недоступен.\n\n"
                "💡 **Подсказка:** Убедитесь, что сервис voice-gateway запущен."
            )
            return
        session_id = active_call_sessions.get(message.chat.id)
        if not session_id:
            await _reply_voice_gateway_error(message, "no_session")
            return

        result = await voice_gateway.get_diagnostics_why(session_id)
        if not result.get("ok"):
            await _reply_voice_gateway_error(message, "generic", details=f"Не удалось получить explain-диагностику. 🛡️ Детали: `{result.get('error', 'unknown')}`")
            return
        payload = result.get("result", {})
        why = payload.get("why", {}) if isinstance(payload.get("why"), dict) else {}
        metrics = why.get("metrics", {}) if isinstance(why.get("metrics"), dict) else {}
        recs = why.get("recommendations", []) if isinstance(why.get("recommendations"), list) else []
        rec_text = "\n".join(f"- {str(item)}" for item in recs[:4]) if recs else "- (нет)"
        await message.reply_text(
            "🧭 **Почему не перевелось**\n"
            f"- code: `{why.get('code', '-')}`\n"
            f"- message: {why.get('message', '-')}\n"
            f"- stt: `{metrics.get('stt_partial', 0)}`\n"
            f"- tr: `{metrics.get('translation_partial', 0)}`\n"
            f"- speech_ratio: `{metrics.get('speech_ratio', 0)}`\n"
            f"- buffer: `{metrics.get('buffering_mode', '-')}`\n\n"
            f"Рекомендации:\n{rec_text}"
        )

    # --- !calltune: runtime тюнинг буфера/VAD ---
    @app.on_message(filters.command("calltune", prefixes="!"))
    @safe_handler
    async def calltune_command(client, message: Message):
        """Тюнинг runtime: !calltune [adaptive|low|stable] [latency_ms] [vad]."""
        if not voice_gateway:
            await message.reply_text(
                "❌ **Ошибка:** Voice Gateway недоступен.\n\n"
                "💡 **Подсказка:** Убедитесь, что сервис voice-gateway запущен."
            )
            return
        session_id = active_call_sessions.get(message.chat.id)
        if not session_id:
            await _reply_voice_gateway_error(message, "no_session")
            return

        mode_raw = message.command[1].strip().lower() if len(message.command) >= 2 else "adaptive"
        mode_map = {"adaptive": "adaptive", "low": "low_latency", "stable": "stable"}
        buffering_mode = mode_map.get(mode_raw, "adaptive")

        target_latency_ms = 420
        if len(message.command) >= 3:
            try:
                target_latency_ms = int(message.command[2].strip())
            except Exception:
                target_latency_ms = 420
        target_latency_ms = max(150, min(target_latency_ms, 4000))

        vad = 0.38
        if len(message.command) >= 4:
            try:
                vad = float(message.command[3].strip())
            except Exception:
                vad = 0.38
        vad = max(0.05, min(vad, 0.95))

        result = await voice_gateway.tune_runtime(
            session_id,
            buffering_mode=buffering_mode,
            target_latency_ms=target_latency_ms,
            vad_sensitivity=vad,
        )
        if not result.get("ok"):
            await _reply_voice_gateway_error(message, "update_fail", details=result.get("error"))
            return
        runtime = result.get("result", {}).get("runtime", {})
        await message.reply_text(
            "🎛️ **Runtime Tune Applied**\n"
            f"- mode: `{runtime.get('buffering_mode', buffering_mode)}`\n"
            f"- target_latency_ms: `{runtime.get('target_latency_ms', target_latency_ms)}`\n"
            f"- vad_sensitivity: `{runtime.get('vad_sensitivity', vad)}`"
        )

    # --- !browse: Browser Automation (Phase 9.2) ---
    @app.on_message(filters.command("browse", prefixes="!"))
    @safe_handler
    async def browse_command(client, message: Message):
        """Browser: !browse <url>"""
        browser_agent = deps.get("browser_agent")
        if not openclaw:
            await message.reply_text("❌ OpenClaw client не инициализирован.")
            return

        if len(message.command) < 2:
            await message.reply_text("🌐 Какой URL открыть? Пример: `!browse https://example.com`")
            return
            
        url = message.text.split(" ", 1)[1]
        notification = await message.reply_text(f"🌐 **Навигация:** `{url}`...")
        
        try:
            # OpenClaw-first: web_fetch
            fetched = await openclaw.invoke_tool("web_fetch", {"url": url})
            if not fetched.get("error"):
                details = fetched.get("details", {}) if isinstance(fetched, dict) else {}
                title = details.get("title", url)
                text = ""
                try:
                    text = fetched.get("content", [{}])[0].get("text", "")
                except Exception:
                    text = ""
                content_snippet = (text or "")[:3000]
                if len(text or "") > 3000:
                    content_snippet += "\n... [далее обрезано]"
                await notification.edit_text(
                    f"📄 **OpenClaw Fetch:** `{title}`\n\n```text\n{content_snippet}\n```"
                )
                return

            # Fallback: локальный BrowserAgent (если включен)
            if not browser_agent:
                await notification.edit_text("❌ OpenClaw web_fetch не сработал, а локальный BrowserAgent выключен.")
                return

            result = await browser_agent.browse(url)
            if "error" in result:
                await notification.edit_text(f"❌ Ошибка загрузки: {result['error']}")
                return

            screenshot_path = result.get("screenshot_path")
            if screenshot_path:
                await message.reply_photo(
                    photo=screenshot_path,
                    caption=f"📄 **{result['title']}**\n🔗 `{result['url']}`"
                )

            content_snippet = result.get("content", "")[:3000]
            if len(result.get("content", "")) > 3000:
                content_snippet += "\n... [далее обрезано]"

            await notification.edit_text(
                f"📄 **Fallback Browser Preview:**\n\n```text\n{content_snippet}\n```"
            )
            
        except Exception as e:
            logger.error(f"Browse command error: {e}")
            await notification.edit_text(f"❌ Критическая ошибка браузера: {e}")

    # --- !screenshot: Web Screenshot ---
    @app.on_message(filters.command("screenshot", prefixes="!"))
    @safe_handler
    async def screenshot_command(client, message: Message):
        """Screenshot: !screenshot <url>"""
        browser_agent = deps.get("browser_agent")

        if not browser_agent:
            await message.reply_text("❌ Browser Agent не инициализирован.")
            return

        if len(message.command) < 2:
            await message.reply_text("📸 Какой URL снять? Пример: `!screenshot https://google.com`")
            return

        url = message.text.split(" ", 1)[1]
        notification = await message.reply_text(f"📸 **Снимаю страницу:** `{url}`...")

        try:
            path = await browser_agent.screenshot_only(url)
            
            if path and path.endswith(".png"):
                await message.reply_photo(photo=path, caption=f"📸 Screenshot: {url}")
                await notification.delete()
            else:
                await notification.edit_text(f"❌ Не удалось сделать скриншот.")
        except Exception as e:
            logger.error(f"Screenshot error: {e}")
            await notification.edit_text(f"❌ Ошибка: {e}")
    # --- Helper Functions ---
    async def _process_research_task(client, message, openclaw, query: str, mode: str = "scout"):
        """
        Delegates research task to OpenClaw Engine.
        """
        if not openclaw:
            await message.reply_text("❌ OpenClaw client не инициализирован.")
            return

        icon = "🔎" if mode == "scout" else "🕵️‍♂️"
        title = "OpenClaw Scout" if mode == "scout" else "Nexus Intelligence"
        
        notification = await message.reply_text(
            f"{icon} **{title}: Transmitting to Engine...** `{query}`"
        )

        try:
            # Determine agent based on mode
            agent_id = "research_deep" if mode == "nexus" else "research_fast"
            
            # Execute via OpenClaw Client
            response = await openclaw.execute_agent_task(query, agent_id=agent_id)
            
            # Send result
            await notification.edit_text(
                f"{icon} **{title}: Report**\n\n{response}"
            )
            
        except Exception as e:
            logger.error(f"OpenClaw Request failed: {e}")
            await notification.edit_text(f"❌ **Engine Error:** {e}")
