# -*- coding: utf-8 -*-
"""
Finance Handler — Криптовалюты и финансы phase 9.4.
"""

from pyrogram import filters
from pyrogram.types import Message
import structlog

logger = structlog.get_logger(__name__)

def register_handlers(app, deps: dict):
    """Регистрация финансовых команд."""
    crypto_intel = deps.get("crypto_intel")
    
    # --- !crypto: Цена монеты ---
    @app.on_message(filters.command("crypto", prefixes="!"))
    async def crypto_command(client, message: Message):
        """
        Crypto Price Check: !crypto <symbol>
        Example: !crypto bitcoin
        """
        if not crypto_intel:
            await message.reply_text("❌ CryptoIntel module not available.")
            return

        if len(message.command) < 2:
            await message.reply_text("💰 Usage: `!crypto bitcoin` or `!crypto eth`")
            return
            
        coin = message.text.split(" ", 1)[1].lower()
        msg = await message.reply_text(f"🔍 **Checking price for {coin}...**")
        
        # 1. Поиск ID монеты
        real_id = coin
        if len(coin) <= 5:
            results = await crypto_intel.search(coin)
            if results:
                real_id = results[0]['id']
            else:
                 await msg.edit_text(f"❌ Coin '{coin}' not found.")
                 return
        
        # 2. Запрос цены
        data = await crypto_intel.get_price(real_id, "usd")
        
        if "error" in data:
             await msg.edit_text(f"❌ Error: {data['error']}")
             return
             
        # 3. Форматирование
        price = data.get("usd", 0)
        change_24h = data.get("usd_24h_change", 0)
        emoji = "📈" if change_24h >= 0 else "📉"
        
        text = (
            f"💰 **{real_id.upper()} (USD)**\n\n"
            f"💵 **Price:** `${price:,.2f}`\n"
            f"{emoji} **24h Change:** `{change_24h:+.2f}%`\n"
            f"🕒 Updated: Just now"
        )
        await msg.edit_text(text)

    # --- !portfolio: Портфолио (Mock) ---
    @app.on_message(filters.command("portfolio", prefixes="!"))
    async def portfolio_command(client, message: Message):
        """My Portfolio Status."""
        await message.reply_text(
            "💼 **Crypto Portfolio:**\n\n"
            "• **BTC:** 0.5 ($45,000)\n"
            "• **ETH:** 10.0 ($32,000)\n"
            "• **Total:** $77,000\n"
            "_(Mock Data - DB implementation pending)_"
        )
