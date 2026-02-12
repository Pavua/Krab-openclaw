# -*- coding: utf-8 -*-
"""
Web App Module (Phase 15).
Сервер для Telegram Mini App и Dashboard.
"""

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import os
import asyncio
import structlog

logger = structlog.get_logger("WebApp")

class WebApp:
    def __init__(self, deps: dict, port: int = 8000):
        self.app = FastAPI(title="Krab Mini App")
        self.deps = deps
        self.port = port
        self.host = "0.0.0.0"
        self._setup_routes()

    def _setup_routes(self):
        @self.app.get("/", response_class=HTMLResponse)
        async def index():
            return FileResponse("src/web/index.html")

        @self.app.get("/api/stats")
        async def get_stats():
            router = self.deps["router"]
            black_box = self.deps["black_box"]
            rag = router.rag
            
            return {
                "router": router.get_model_info(),
                "black_box": black_box.get_stats(),
                "rag": rag.get_stats()
            }

    async def start(self):
        """Запуск сервера в фоне."""
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="warning")
        server = uvicorn.Server(config)
        logger.info(f"🌐 Web App starting at http://{self.host}:{self.port}")
        asyncio.create_task(server.serve())

    async def stop(self):
        """Остановка (FastAPI/Uvicorn обычно закрываются при завершении loop)."""
        pass
