# -*- coding: utf-8 -*-
"""
CallbackHandler mixin для `KraabUserbot`.

Wave 31-C: извлечён из `src/userbot_bridge.py` (2026-05-05).
Содержит обработчики входящих callback query от inline-кнопок Telegram:
- роутер по prefix (confirm / page / action)
- обработка подтверждений, пагинации, произвольных action-ов

Зависимости через self.*:
- `self.client` — Pyrogram Client
- `self._safe_edit` — метод редактирования сообщения (bridge/telegram_send_utils)
- `self._deliver_response_parts` — метод доставки ответа (bridge/response_delivery)
"""

from __future__ import annotations

from ..core.logger import get_logger

logger = get_logger(__name__)


class CallbackHandlerMixin:
    """Wave 31-C: роутер и обработчики callback query (inline-кнопки)."""

    async def _handle_callback_query(self, callback_query) -> None:
        """
        Роутер входящих callback query от inline-кнопок.

        Схемы prefix:
          confirm:<action_id>:yes|no  — подтверждение/отказ
          page:<prefix>:<page>        — пагинация
          action:<action_id>          — произвольное действие
        """
        cq = callback_query
        data: str = cq.data or ""
        try:
            if data.startswith("confirm:"):
                await self._cb_confirm(cq, data)
            elif data.startswith("page:"):
                await self._cb_page(cq, data)
            elif data.startswith("action:"):
                await self._cb_action(cq, data)
            else:
                await cq.answer("⚠️ Неизвестное действие")
        except Exception as exc:  # noqa: BLE001
            logger.warning("callback_query_error", data=data, error=str(exc))
            try:
                await cq.answer("❌ Ошибка обработки")
            except Exception:
                pass

    async def _cb_confirm(self, cq, data: str) -> None:
        """Обработка confirm:<action_id>:yes|no."""
        parts = data.split(":", 2)
        if len(parts) < 3:
            await cq.answer("⚠️ Некорректный формат")
            return
        action_id = parts[1]
        choice = parts[2]
        if choice == "yes":
            await cq.answer("✅ Подтверждено")
            await cq.message.reply(f"✅ Действие `{action_id}` подтверждено.")
        else:
            await cq.answer("❌ Отменено")
            await cq.message.reply(f"❌ Действие `{action_id}` отменено.")

    async def _cb_page(self, cq, data: str) -> None:
        """Обработка page:<prefix>:<page>."""
        parts = data.split(":", 2)
        if len(parts) < 3:
            await cq.answer("⚠️ Некорректный формат")
            return
        page_str = parts[2]
        if page_str == "noop":
            await cq.answer()
            return
        try:
            page = int(page_str)
        except ValueError:
            await cq.answer("⚠️ Некорректный номер страницы")
            return
        await cq.answer(f"Страница {page + 1}")

    async def _cb_action(self, cq, data: str) -> None:
        """
        Обработка action:<action_id>.

        Известные action_id:
          swarm_team:<team>  — подсказка по запуску swarm для команды
          costs_detail       — подробная разбивка по моделям
          health_recheck     — повторный health check
        """
        action = data[len("action:") :]
        if action.startswith("swarm_team:"):
            team = action[len("swarm_team:") :]
            await cq.answer(f"🐝 {team}")
            await cq.message.reply(
                f"🐝 Используй команду:\n`!swarm {team} <тема>`\n\n"
                f"Например: `!swarm {team} анализ текущей ситуации`"
            )
        elif action == "costs_detail":
            await cq.answer("📊 Загружаю детали…")
            from ..core.cost_analytics import cost_analytics

            report = cost_analytics.build_usage_report_dict()
            by_model: dict = report.get("by_model", {})
            if not by_model:
                await cq.message.reply("ℹ️ Данных по моделям пока нет.")
                return
            lines = ["📊 **Детализация по моделям:**"]
            for mid, d in sorted(by_model.items(), key=lambda x: -x[1].get("cost_usd", 0)):
                calls = d.get("calls", 0)
                tokens = d.get("tokens", 0)
                cost = d.get("cost_usd", 0)
                lines.append(f"• `{mid}`: ${cost:.4f} | {calls} calls | {tokens} tokens")
            await cq.message.reply("\n".join(lines))
        elif action == "health_recheck":
            await cq.answer("🔄 Перепроверяю…")
            await cq.message.reply(
                "🔄 Запускаю повторный health check…\nИспользуй `!health` для полного отчёта."
            )
        else:
            await cq.answer(f"⚠️ Неизвестный action: {action}")
