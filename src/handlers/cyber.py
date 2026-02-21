# -*- coding: utf-8 -*-
"""
Cyber Security & Network Tools (Phase 12).
Инструменты для анализа сети и базового аудита безопасности.
"""

import os
import asyncio
import socket
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message

def register_cyber_handlers(app: Client, deps: dict):
    is_owner = lambda m: str(m.from_user.id) == deps["owner_id"] or m.from_user.username == deps["owner_username"].replace("@", "")

    @app.on_message(filters.command("ping", prefixes="!"))
    async def ping_command(client, message: Message):
        if not is_owner(message): return
        
        target = " ".join(message.command[1:]) or "google.com"
        notification = await message.reply_text(f"📡 **Ping {target}...**")
        
        try:
            proc = await asyncio.create_subprocess_exec(
                "ping", "-c", "4", target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            result = stdout.decode().strip()
            
            await notification.edit_text(f"📡 **Ping Results for {target}:**\n\n`{result}`")
        except Exception as e:
            await notification.edit_text(f"❌ Ошибка: {e}")

    @app.on_message(filters.command("headers", prefixes="!"))
    async def headers_command(client, message: Message):
        if not is_owner(message): return
        
        url = " ".join(message.command[1:])
        if not url:
            await message.reply_text("❌ Введи URL: `!headers https://google.com`")
            return
            
        if not url.startswith("http"):
            url = f"https://{url}"

        notification = await message.reply_text(f"🌐 **Fetching headers for {url}...**")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as response:
                    headers = dict(response.headers)
                    formatted = "\n".join([f"**{k}:** `{v}`" for k, v in headers.items()])
                    await notification.edit_text(f"🌐 **HTTP Headers ({url}):**\n\n{formatted}")
        except Exception as e:
            await notification.edit_text(f"❌ Ошибка: {e}")

    @app.on_message(filters.command("portscan", prefixes="!"))
    async def portscan_command(client, message: Message):
        """Быстрый сканер портов (Async)."""
        if not is_owner(message): return
        
        target = " ".join(message.command[1:])
        if not target:
            await message.reply_text("❌ Введи хост: `!portscan localhost`")
            return
            
        common_ports = [21, 22, 23, 25, 53, 80, 110, 143, 443, 3306, 3389, 5432, 8080, 8188, 11434]
        notification = await message.reply_text(f"🔍 **Scanning {target} (common ports)...**")
        
        open_ports = []
        
        async def check_port(p):
            try:
                # Используем asyncio.open_connection для проверки порта
                conn = asyncio.open_connection(target, p)
                await asyncio.wait_for(conn, timeout=1.0)
                open_ports.append(p)
            except:
                pass

        await asyncio.gather(*[check_port(p) for p in common_ports])
        
        res_text = f"🔍 **Port Scan Results for {target}**\n\n"
        if open_ports:
            res_text += "✅ **Open Ports:**\n"
            for p in sorted(open_ports):
                service = {80: "HTTP", 443: "HTTPS", 22: "SSH", 3306: "MySQL", 11434: "Ollama", 8188: "ComfyUI"}.get(p, "Unknown")
                res_text += f"- `{p}` ({service})\n"
        else:
            res_text += "❌ Все порты закрыты или хост недоступен."
            
        await notification.edit_text(res_text)
