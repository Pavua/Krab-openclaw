# -*- coding: utf-8 -*-
"""
Web App Module (Phase 15+).
Сервер для Dashboard и web-управления экосистемой Krab.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import time
import json
import shlex
import hashlib
import io
import mimetypes
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union

import structlog
import uvicorn
from fastapi import Body, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from src.core.ecosystem_health import EcosystemHealthService
from src.core.observability import get_observability_snapshot, metrics, timeline, build_ops_response

logger = structlog.get_logger("WebApp")


class WebApp:
    """Web-панель Krab с API статуса экосистемы."""

    def __init__(self, deps: dict, port: int = 8000, host: str = "0.0.0.0"):
        self.app = FastAPI(title="Krab Web Panel", version="v8")
        self.deps = deps
        self.port = int(port)
        self.host = host
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task | None = None
        self._web_root = Path(__file__).resolve().parents[1] / "web"
        self._index_path = self._web_root / "index.html"
        self._nano_theme_path = self._web_root / "prototypes" / "nano" / "nano_theme.css"
        self._assistant_rate_state: dict[str, list[float]] = {}
        self._idempotency_state: dict[str, tuple[float, dict]] = {}
        self._setup_routes()

    def _public_base_url(self) -> str:
        """Возвращает внешний base URL панели."""
        explicit = os.getenv("WEB_PUBLIC_BASE_URL", "").strip().rstrip("/")
        if explicit:
            return explicit
        display_host = os.getenv("WEB_HOST", "127.0.0.1").strip() or "127.0.0.1"
        return f"http://{display_host}:{self.port}"

    def _web_api_key(self) -> str:
        """Возвращает API-ключ web write-endpoints (может быть пустым)."""
        return os.getenv("WEB_API_KEY", "").strip()

    def _assert_write_access(self, header_key: str, token: str) -> None:
        """Проверяет доступ к write-эндпоинтам web API."""
        expected = self._web_api_key()
        if not expected:
            return

        provided = (header_key or "").strip() or (token or "").strip()
        if provided != expected:
            raise HTTPException(status_code=403, detail="forbidden: invalid WEB_API_KEY")

    @staticmethod
    def _project_root() -> Path:
        """Возвращает корень проекта Krab."""
        return Path(__file__).resolve().parents[2]

    @staticmethod
    def _tail_text(text: str, max_chars: int = 2000) -> str:
        """Возвращает хвост текста с ограничением длины."""
        payload = str(text or "")
        if len(payload) <= max_chars:
            return payload
        return payload[-max_chars:]

    @staticmethod
    def _mask_secret(value: str) -> str:
        """Маскирует секрет для UI/логов: видны только префикс и суффикс."""
        text = str(value or "").strip()
        if not text:
            return ""
        if len(text) <= 6:
            return "*" * len(text)
        return f"{text[:3]}...{text[-3:]}"

    def _run_local_script(
        self,
        script_path: Path,
        *,
        timeout_seconds: int = 90,
        args: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Единый раннер локальных .command-скриптов для web API.

        Возвращает нормализованный payload без выброса исключений наружу:
        {
          ok: bool,
          exit_code: int,
          stdout_tail: str,
          error: str
        }
        """
        target = Path(script_path).resolve()
        if not target.exists() or not target.is_file():
            return {
                "ok": False,
                "exit_code": 127,
                "stdout_tail": "",
                "error": f"script_not_found:{target}",
            }

        cmd = [str(target)] + [str(item) for item in (args or [])]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self._project_root()),
                capture_output=True,
                text=True,
                check=False,
                timeout=int(max(5, timeout_seconds)),
            )
            merged = "\n".join(
                item for item in [(proc.stdout or "").strip(), (proc.stderr or "").strip()] if item
            )
            return {
                "ok": proc.returncode == 0,
                "exit_code": int(proc.returncode),
                "stdout_tail": self._tail_text(merged, max_chars=2000),
                "error": "",
            }
        except subprocess.TimeoutExpired as exc:
            timeout_tail = self._tail_text(
                "\n".join(
                    item
                    for item in [
                        (exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")),
                        (exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")),
                    ]
                    if item
                ),
                max_chars=2000,
            )
            return {
                "ok": False,
                "exit_code": 124,
                "stdout_tail": timeout_tail,
                "error": "script_timeout",
            }
        except Exception as exc:
            return {
                "ok": False,
                "exit_code": 1,
                "stdout_tail": "",
                "error": f"script_run_error:{exc}",
            }

    def _latest_path_by_glob(self, pattern: str) -> Path | None:
        """Возвращает самый свежий путь по glob-паттерну внутри проекта."""
        root = self._project_root()
        items = sorted(
            root.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return items[0] if items else None

    def _assistant_rate_limit_per_min(self) -> int:
        """Возвращает лимит запросов assistant API в минуту на одного клиента."""
        raw = os.getenv("WEB_ASSISTANT_RATE_LIMIT_PER_MIN", "30").strip()
        try:
            value = int(raw)
        except Exception:
            value = 30
        return max(1, value)

    def _enforce_assistant_rate_limit(self, client_key: str) -> None:
        """Простой in-memory rate-limit для web-native assistant."""
        now = time.time()
        window_sec = 60.0
        limit = self._assistant_rate_limit_per_min()
        key = client_key or "anonymous"
        bucket = self._assistant_rate_state.setdefault(key, [])
        # Оставляем только события за последнюю минуту.
        bucket[:] = [ts for ts in bucket if (now - ts) <= window_sec]
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"assistant_rate_limited: limit={limit}/min for client={key}",
            )
        bucket.append(now)

    def _idempotency_ttl_sec(self) -> int:
        """TTL кэша idempotency в секундах."""
        raw = os.getenv("WEB_IDEMPOTENCY_TTL_SEC", "300").strip()
        try:
            value = int(raw)
        except Exception:
            value = 300
        return max(30, value)

    def _idempotency_get(self, namespace: str, key: str) -> dict | None:
        """Возвращает кэшированный ответ по idempotency key, если не истек TTL."""
        if not key:
            return None
        now = time.time()
        ttl = self._idempotency_ttl_sec()
        lookup_key = f"{namespace}:{key}"
        entry = self._idempotency_state.get(lookup_key)
        if not entry:
            return None
        ts, payload = entry
        if (now - ts) > ttl:
            self._idempotency_state.pop(lookup_key, None)
            return None
        data = dict(payload)
        data["idempotent_replay"] = True
        return data

    def _idempotency_set(self, namespace: str, key: str, payload: dict) -> None:
        """Сохраняет ответ по idempotency key."""
        if not key:
            return
        lookup_key = f"{namespace}:{key}"
        self._idempotency_state[lookup_key] = (time.time(), dict(payload))

    def _web_attachment_max_bytes(self) -> int:
        """Максимальный размер вложения web-панели в байтах."""
        raw = os.getenv("WEB_ATTACHMENT_MAX_MB", "12").strip()
        try:
            value_mb = float(raw)
        except Exception:
            value_mb = 12.0
        value_mb = max(1.0, min(value_mb, 200.0))
        return int(value_mb * 1024 * 1024)

    @staticmethod
    def _sanitize_attachment_name(name: str) -> str:
        """Очищает имя файла до безопасного ASCII-вида."""
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "").strip())
        safe = safe.strip("._")
        return safe or "attachment.bin"

    @staticmethod
    def _trim_prompt_text(text: str, max_chars: int = 24000) -> tuple[str, bool]:
        """Обрезает текст для prompt-контекста, чтобы не перегружать запрос."""
        content = str(text or "")
        if len(content) <= max_chars:
            return content, False
        return content[:max_chars], True

    def _extract_pdf_text(self, raw_bytes: bytes) -> str:
        """Извлекает текст из PDF (если установлен pypdf)."""
        try:
            import pypdf  # type: ignore
        except Exception:
            return ""
        try:
            reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
            parts: list[str] = []
            for page in reader.pages[:20]:
                try:
                    page_text = page.extract_text() or ""
                except Exception:
                    page_text = ""
                if page_text:
                    parts.append(page_text)
            return "\n\n".join(parts).strip()
        except Exception:
            return ""

    def _extract_docx_text(self, raw_bytes: bytes) -> str:
        """Извлекает текст из DOCX (если установлен python-docx)."""
        try:
            from docx import Document  # type: ignore
        except Exception:
            return ""
        try:
            document = Document(io.BytesIO(raw_bytes))
            lines = [str(p.text).strip() for p in document.paragraphs if str(p.text).strip()]
            return "\n".join(lines).strip()
        except Exception:
            return ""

    def _build_attachment_prompt(self, *, file_name: str, content_type: str, raw_bytes: bytes, stored_path: Path) -> dict:
        """
        Преобразует загруженный файл в prompt-совместимый контекст.
        Поддержка:
        - text/* и популярные текстовые расширения;
        - PDF / DOCX -> извлечение текста (best effort);
        - image/video/archive -> метаданные + путь к сохранённому файлу.
        """
        ext = Path(file_name).suffix.lower()
        size_bytes = int(len(raw_bytes))
        size_kb = round(size_bytes / 1024.0, 2)
        fingerprint = hashlib.sha256(raw_bytes).hexdigest()[:16]

        text_extensions = {
            ".txt", ".md", ".json", ".csv", ".tsv", ".py", ".js", ".ts", ".tsx",
            ".yaml", ".yml", ".xml", ".html", ".htm", ".log", ".ini", ".toml", ".env",
        }
        is_text_like = content_type.startswith("text/") or ext in text_extensions

        extracted = ""
        kind = "metadata"
        if is_text_like:
            extracted = raw_bytes.decode("utf-8", errors="replace")
            kind = "text"
        elif ext == ".pdf":
            extracted = self._extract_pdf_text(raw_bytes)
            kind = "pdf_text" if extracted else "pdf_metadata"
        elif ext == ".docx":
            extracted = self._extract_docx_text(raw_bytes)
            kind = "docx_text" if extracted else "docx_metadata"
        elif content_type.startswith("image/"):
            kind = "image_metadata"
        elif content_type.startswith("video/"):
            kind = "video_metadata"
        elif ext in {".zip", ".rar", ".7z", ".tar", ".gz"}:
            kind = "archive_metadata"

        if extracted:
            trimmed, was_trimmed = self._trim_prompt_text(extracted, max_chars=24000)
            suffix = (
                "\n\n[...контент обрезан для стабильности web-prompt]"
                if was_trimmed else ""
            )
            prompt_snippet = (
                f"Контекст из файла `{file_name}`:\n"
                f"```text\n{trimmed}{suffix}\n```"
            )
        else:
            prompt_snippet = (
                f"Вложение `{file_name}` ({content_type}, {size_kb} KB, sha256:{fingerprint}) "
                f"сохранено локально по пути `{stored_path}`.\n"
                "Если нужно, сначала попроси извлечь/проанализировать содержимое этого типа файла."
            )

        return {
            "kind": kind,
            "file_name": file_name,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "sha256_short": fingerprint,
            "stored_path": str(stored_path),
            "prompt_snippet": prompt_snippet,
            "has_extracted_text": bool(extracted),
        }

    def _setup_routes(self):
        @self.app.get("/", response_class=HTMLResponse)
        async def index():
            if self._index_path.exists():
                return FileResponse(self._index_path)
            return HTMLResponse("<h1>Krab Web Panel</h1><p>index.html не найден</p>")

        @self.app.get("/nano_theme.css")
        @self.app.get("/prototypes/nano/nano_theme.css")
        async def nano_theme_css():
            """
            Отдает основной CSS web-панели.

            Дублируем оба URL, чтобы панель стабильно работала и при открытии
            через локальный HTTP, и при старых ссылках после обновлений.
            """
            if self._nano_theme_path.exists():
                return FileResponse(self._nano_theme_path, media_type="text/css")
            raise HTTPException(status_code=404, detail="nano_theme_css_not_found")

        @self.app.get("/api/stats")
        async def get_stats():
            router = self.deps["router"]
            black_box = self.deps["black_box"]
            rag = router.rag
            return {
                "router": router.get_model_info(),
                "black_box": black_box.get_stats(),
                "rag": rag.get_stats() if rag else {"enabled": False, "count": 0},
            }

        @self.app.get("/api/health")
        async def get_health():
            """Единый health статусов для web-панели."""
            router = self.deps["router"]
            openclaw = self.deps.get("openclaw_client")
            voice_gateway = self.deps.get("voice_gateway_client")
            krab_ear = self.deps.get("krab_ear_client")
            ecosystem = EcosystemHealthService(
                router=router,
                openclaw_client=openclaw,
                voice_gateway_client=voice_gateway,
                krab_ear_client=krab_ear,
            )
            report = await ecosystem.collect()
            return {
                "status": "ok",
                "checks": {
                    "openclaw": bool(report["checks"]["openclaw"]["ok"]),
                    "local_lm": bool(report["checks"]["local_lm"]["ok"]),
                    "voice_gateway": bool(report["checks"]["voice_gateway"]["ok"]),
                    "krab_ear": bool(report["checks"]["krab_ear"]["ok"]),
                },
                "degradation": str(report["degradation"]),
                "risk_level": str(report["risk_level"]),
                "chain": report["chain"],
            }

        @self.app.get("/api/health/lite")
        async def get_health_lite():
            """
            Быстрый liveness-check web-панели.

            Важно:
            - не тянет deep ecosystem probes;
            - используется daemon-скриптами и uptime-watch для проверки
              «жив ли HTTP-процесс», а не «все ли внешние зависимости сейчас быстрые».
            """
            return {
                "ok": True,
                "status": "up",
            }

        @self.app.get("/api/transcriber/status")
        async def transcriber_status():
            """
            Операционный статус транскрибатора.
            Нужен для быстрого понимания: жив ли voice-контур и включена ли crash-защита STT.
            """
            openclaw = self.deps.get("openclaw_client")
            voice_gateway = self.deps.get("voice_gateway_client")
            krab_ear = self.deps.get("krab_ear_client")
            perceptor = self.deps.get("perceptor")

            openclaw_ok = False
            voice_gateway_ok = False
            krab_ear_ok = False
            try:
                openclaw_ok = bool(await openclaw.health_check()) if openclaw else False
            except Exception:
                openclaw_ok = False
            try:
                voice_gateway_ok = bool(await voice_gateway.health_check()) if voice_gateway else False
            except Exception:
                voice_gateway_ok = False
            try:
                krab_ear_ok = bool(await krab_ear.health_check()) if krab_ear else False
            except Exception:
                krab_ear_ok = False

            def _env_on(key: str, default: str = "0") -> bool:
                return str(os.getenv(key, default)).strip().lower() in {"1", "true", "yes", "on"}

            stt_isolated_worker = _env_on("STT_ISOLATED_WORKER", "1")
            perceptor_isolated_worker = bool(getattr(perceptor, "stt_isolated_worker", stt_isolated_worker))
            stt_worker_timeout = int(str(os.getenv("STT_WORKER_TIMEOUT_SECONDS", "240")).strip() or "240")

            readiness = "ready" if (voice_gateway_ok and perceptor_isolated_worker) else (
                "degraded" if voice_gateway_ok else "down"
            )
            recommendations: list[str] = []
            if not voice_gateway_ok:
                recommendations.append("Запусти ./transcriber_doctor.command --heal")
            if not perceptor_isolated_worker:
                recommendations.append("Включи STT_ISOLATED_WORKER=1 и перезапусти Krab")
            if not recommendations:
                recommendations.append("Система транскрибации в рабочем режиме")

            return {
                "ok": True,
                "status": {
                    "readiness": readiness,
                    "openclaw_ok": openclaw_ok,
                    "voice_gateway_ok": voice_gateway_ok,
                    "krab_ear_ok": krab_ear_ok,
                    "stt_isolated_worker": perceptor_isolated_worker,
                    "stt_worker_timeout_seconds": stt_worker_timeout,
                    "voice_gateway_url": os.getenv("VOICE_GATEWAY_URL", "http://127.0.0.1:8090"),
                    "whisper_model": str(getattr(perceptor, "whisper_model", "")),
                    "audio_warmup_enabled": _env_on("PERCEPTOR_AUDIO_WARMUP", "0"),
                    "recommendations": recommendations,
                },
            }

        @self.app.get("/api/policy")
        async def get_policy():
            """Возвращает runtime-политику AI (queue/guardrails/reactions)."""
            ai_runtime = self.deps.get("ai_runtime")
            if not ai_runtime:
                return {"ok": False, "error": "ai_runtime_not_configured"}
            return {"ok": True, "policy": ai_runtime.get_policy_snapshot()}

        @self.app.get("/api/queue")
        async def get_queue():
            """Возвращает состояние per-chat очередей автообработки."""
            ai_runtime = self.deps.get("ai_runtime")
            if not ai_runtime or not hasattr(ai_runtime, "queue_manager"):
                return {"ok": False, "error": "queue_not_configured"}
            return {"ok": True, "queue": ai_runtime.queue_manager.get_stats()}

        @self.app.get("/api/ctx")
        async def get_ctx(chat_id: int | None = Query(default=None)):
            """Snapshot контекста последнего запроса (по чату или все чаты)."""
            ai_runtime = self.deps.get("ai_runtime")
            if not ai_runtime:
                return {"ok": False, "error": "ai_runtime_not_configured"}
            if chat_id is None:
                if not hasattr(ai_runtime, "get_context_snapshots"):
                    return {"ok": False, "error": "ctx_not_supported"}
                return {"ok": True, "contexts": ai_runtime.get_context_snapshots()}
            return {"ok": True, "context": ai_runtime.get_context_snapshot(int(chat_id))}

        @self.app.get("/api/reactions/stats")
        async def get_reactions_stats(chat_id: int | None = Query(default=None)):
            """Сводка по реакциям (общая или по чату)."""
            reaction_engine = self.deps.get("reaction_engine")
            if not reaction_engine:
                return {"ok": False, "error": "reaction_engine_not_configured"}
            return {"ok": True, "stats": reaction_engine.get_reaction_stats(chat_id=chat_id)}

        @self.app.get("/api/mood/{chat_id}")
        async def get_chat_mood(chat_id: int):
            """Возвращает mood-профиль конкретного чата."""
            reaction_engine = self.deps.get("reaction_engine")
            if not reaction_engine:
                return {"ok": False, "error": "reaction_engine_not_configured"}
            return {"ok": True, "mood": reaction_engine.get_chat_mood(chat_id)}

        @self.app.get("/api/links")
        async def get_links():
            """Ссылки по экосистеме в одном месте."""
            base = self._public_base_url()
            return {
                "dashboard": base,
                "stats_api": f"{base}/api/stats",
                "health_api": f"{base}/api/health",
                "health_lite_api": f"{base}/api/health/lite",
                "ecosystem_health_api": f"{base}/api/ecosystem/health",
                "links_api": f"{base}/api/links",
                "openclaw_cloud_api": f"{base}/api/openclaw/cloud",
                "context_checkpoint_api": f"{base}/api/context/checkpoint",
                "context_transition_pack_api": f"{base}/api/context/transition-pack",
                "context_latest_api": f"{base}/api/context/latest",
                "voice_gateway": os.getenv("VOICE_GATEWAY_URL", "http://127.0.0.1:8090"),
                "openclaw": os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18789"),
            }

        @self.app.get("/api/openclaw/runtime-config")
        async def openclaw_runtime_config():
            """
            Runtime-конфиг OpenClaw для UI.
            Важно: секрет не отдаём целиком, только masked + флаг присутствия.
            """
            base_url = os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18789").strip().rstrip("/")
            raw_key = str(os.getenv("OPENCLAW_API_KEY", "") or "").strip()
            key_present = False
            key_masked = ""
            key_kind = "missing"
            if raw_key:
                key_present = True
                if raw_key.startswith("{"):
                    key_kind = "tiered_json"
                    key_masked = "tiered-json-configured"
                else:
                    key_kind = "plain"
                    key_masked = self._mask_secret(raw_key)

            return {
                "ok": True,
                "openclaw_base_url": base_url,
                "gateway_token_present": key_present,
                "gateway_token_masked": key_masked,
                "gateway_token_kind": key_kind,
            }

        @self.app.post("/api/context/checkpoint")
        async def context_checkpoint(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            Создает checkpoint для перехода в новый чат (anti-413).
            Вызывает one-click скрипт и возвращает путь к свежему артефакту.
            """
            self._assert_write_access(x_krab_web_key, token)
            script_path = self._project_root() / "new_chat_checkpoint.command"
            run = self._run_local_script(script_path, timeout_seconds=120)
            if not bool(run.get("ok")):
                detail = str(run.get("error") or f"exit_code={run.get('exit_code', 1)}")
                raise HTTPException(status_code=500, detail=f"context_checkpoint_failed:{detail}")

            artifact = self._latest_path_by_glob("artifacts/context_checkpoints/checkpoint_*.md")
            if artifact is None:
                raise HTTPException(status_code=500, detail="context_checkpoint_failed:no_artifact")

            return {
                "ok": True,
                "artifact_type": "checkpoint",
                "artifact_path": str(artifact),
                "stdout_tail": str(run.get("stdout_tail") or ""),
                "exit_code": int(run.get("exit_code", 0)),
            }

        @self.app.post("/api/context/transition-pack")
        async def context_transition_pack(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            Собирает transition-pack для восстановления состояния в новом чате.
            """
            self._assert_write_access(x_krab_web_key, token)
            script_path = self._project_root() / "build_transition_pack.command"
            run = self._run_local_script(script_path, timeout_seconds=180)
            if not bool(run.get("ok")):
                detail = str(run.get("error") or f"exit_code={run.get('exit_code', 1)}")
                raise HTTPException(status_code=500, detail=f"context_transition_pack_failed:{detail}")

            pack_dir = self._latest_path_by_glob("artifacts/context_transition/pack_*")
            if pack_dir is None:
                raise HTTPException(status_code=500, detail="context_transition_pack_failed:no_pack_dir")

            transfer_prompt = pack_dir / "TRANSFER_PROMPT_RU.md"
            files_to_attach = pack_dir / "FILES_TO_ATTACH.txt"
            return {
                "ok": True,
                "artifact_type": "transition_pack",
                "pack_dir": str(pack_dir),
                "transfer_prompt_path": str(transfer_prompt) if transfer_prompt.exists() else None,
                "files_to_attach_path": str(files_to_attach) if files_to_attach.exists() else None,
                "stdout_tail": str(run.get("stdout_tail") or ""),
                "exit_code": int(run.get("exit_code", 0)),
            }

        @self.app.get("/api/context/latest")
        async def context_latest():
            """
            Возвращает ссылки на последние anti-413 артефакты.
            """
            checkpoint = self._latest_path_by_glob("artifacts/context_checkpoints/checkpoint_*.md")
            pack_dir = self._latest_path_by_glob("artifacts/context_transition/pack_*")
            transfer_prompt = (pack_dir / "TRANSFER_PROMPT_RU.md") if pack_dir else None
            files_to_attach = (pack_dir / "FILES_TO_ATTACH.txt") if pack_dir else None
            return {
                "ok": True,
                "latest_checkpoint_path": str(checkpoint) if checkpoint else None,
                "latest_pack_dir": str(pack_dir) if pack_dir else None,
                "latest_transfer_prompt_path": str(transfer_prompt) if transfer_prompt and transfer_prompt.exists() else None,
                "latest_files_to_attach_path": str(files_to_attach) if files_to_attach and files_to_attach.exists() else None,
            }

        @self.app.get("/api/openclaw/channels/status")
        async def openclaw_channels_status():
            """
            Выполняет 'openclaw channels status --probe' и возвращает
            сырой вывод + распарсенные предупреждения.
            """
            try:
                # [R9] Безопасный запуск через asyncio subprocess с таймаутом.
                proc = await asyncio.create_subprocess_exec(
                    "openclaw", "channels", "status", "--probe",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                try:
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=45.0)
                except asyncio.TimeoutError:
                    if proc.returncode is None:
                        try:
                            proc.terminate()
                        except ProcessLookupError:
                            pass
                    return {
                        "ok": False,
                        "error": "openclaw_timeout",
                        "detail": "Запрос статуса каналов превысил 45 сек.",
                    }

                raw_output = stdout.decode("utf-8", errors="replace")
                
                # Поиск варнингов в выводе (обычно в блоке 'Warnings:' или строки с 'WARN')
                warnings = []
                capture_warnings = False
                for line in raw_output.splitlines():
                    clean_line = line.strip()
                    if not clean_line:
                        continue
                    if "Warnings:" in clean_line:
                        capture_warnings = True
                        continue
                    if capture_warnings and clean_line.startswith("-"):
                        warnings.append(clean_line.lstrip("- ").strip())
                    elif capture_warnings and clean_line and not clean_line.startswith("-"):
                        # Если пошел другой блок, прекращаем захват (упрощенно)
                        if ":" in clean_line and not clean_line.startswith("http"):
                           capture_warnings = False

                # Дополнительно ищем строки с WARN вне блока Warnings
                if not warnings:
                    for line in raw_output.splitlines():
                        if "WARN" in line.upper():
                            warnings.append(line.strip())

                return {
                    "ok": proc.returncode == 0,
                    "raw": raw_output,
                    "warnings": warnings,
                    "exit_code": proc.returncode,
                }
            except Exception as exc:
                logger.error("openclaw_status_failed", error=str(exc))
                return {
                    "ok": False,
                    "error": "system_error",
                    "detail": f"Не удалось выполнить openclaw: {exc}",
                }

        @self.app.post("/api/openclaw/channels/runtime-repair")
        async def openclaw_runtime_repair(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            Запуск скрипта восстановления рантайма OpenClaw.
            Требует WEB_API_KEY.
            """
            self._assert_write_access(x_krab_web_key, token)
            script_path = "/Users/pablito/Antigravity_AGENTS/Краб/openclaw_runtime_repair.command"
            
            try:
                proc = await asyncio.create_subprocess_exec(
                    script_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
                output = stdout.decode("utf-8", errors="replace")
                return {
                    "ok": proc.returncode == 0,
                    "output": output,
                    "exit_code": proc.returncode,
                }
            except asyncio.TimeoutError:
                return {"ok": False, "error": "timeout", "detail": "Скрипт выполнялся слишком долго (60с)"}
            except Exception as exc:
                return {"ok": False, "error": "system_error", "detail": str(exc)}

        @self.app.post("/api/openclaw/channels/signal-guard-run")
        async def openclaw_signal_guard_run(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            Однократный запуск Ops Guard для проверки сигналов.
            Требует WEB_API_KEY.
            """
            self._assert_write_access(x_krab_web_key, token)
            script_path = "/Users/pablito/Antigravity_AGENTS/Краб/scripts/signal_ops_guard.command"
            
            try:
                # Запускаем с флагом --once для разовой проверки
                proc = await asyncio.create_subprocess_exec(
                    script_path, "--once",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
                output = stdout.decode("utf-8", errors="replace")
                return {
                    "ok": proc.returncode == 0,
                    "output": output,
                    "exit_code": proc.returncode,
                }
            except asyncio.TimeoutError:
                return {"ok": False, "error": "timeout", "detail": "Signal Guard выполнялся слишком долго (60с)"}
            except Exception as exc:
                return {"ok": False, "error": "system_error", "detail": str(exc)}

        @self.app.get("/api/ecosystem/health")
        async def ecosystem_health():
            """[R11] Расширенный health-отчет 3-проектной экосистемы с метриками ресурсов."""
            health_service = self.deps.get("health_service")
            if not health_service:
                # Fallback для совместимости, если сервис не в депсах
                router = self.deps["router"]
                openclaw = self.deps.get("openclaw_client")
                voice_gateway = self.deps.get("voice_gateway_client")
                krab_ear = self.deps.get("krab_ear_client")
                health_service = EcosystemHealthService(
                    router=router,
                    openclaw_client=openclaw,
                    voice_gateway_client=voice_gateway,
                    krab_ear_client=krab_ear,
                )
            report = await health_service.collect()
            return {"ok": True, "report": report}

        @self.app.get("/api/system/diagnostics")
        async def system_diagnostics():
            """[R11] Глубокая диагностика сервера (RAM, CPU, Бюджет, Локальные LLM)."""
            router = self.deps.get("router")
            if not router:
                 return {"ok": False, "error": "router_not_found"}
            
            # Получаем свежие данные через health_service
            health_service = self.deps.get("health_service")
            if not health_service:
                health_service = EcosystemHealthService(router=router)
            
            health_data = await health_service.collect()
            
            status = "ok"
            if not router.is_local_available:
                status = "degraded"
                if getattr(router, "active_tier", "") == "default":
                    status = "failed"
            elif getattr(router, "active_tier", "") == "paid":
                status = "degraded"
            
            return {
                "ok": True,
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "resources": health_data.get("resources", {}),
                "budget": health_data.get("budget", {}),
                "local_ai": {
                    "engine": router.local_engine,
                    "model": router.active_local_model,
                    "available": router.is_local_available
                },
                "watchdog": {
                    "last_recoveries": getattr(self.deps.get("watchdog"), "last_recovery_attempt", {})
                }
            }

        @self.app.get("/api/ops/diagnostics")
        async def ops_diagnostics():
            """[R12] Унифицированный операционный отчет (алиас system/diagnostics с расширением)."""
            return await system_diagnostics()

        @self.app.get("/api/ops/metrics")
        async def ops_metrics():
            """Export internal metrics."""
            return {"ok": True, "metrics": metrics.get_snapshot()}

        @self.app.get("/api/ops/timeline")
        @self.app.get("/api/timeline")
        async def ops_timeline(limit: int = 200, min_severity: Optional[str] = None, channel: Optional[str] = None):
            """Export recent event timeline."""
            return {"ok": True, "events": timeline.get_events(limit=limit, min_severity=min_severity, channel=channel)}

        @self.app.get("/api/sla")
        async def get_sla_metrics():
            """Returns dynamic SLA metrics for the NOC-lite UI (Latency p50/p95, Success Rate)."""
            snap = metrics.get_snapshot()
            counters = snap.get("counters", {})
            latencies = snap.get("latencies", {"p50_ms": 0.0, "p95_ms": 0.0})

            # Calculate basic success rate based on counters (this is a simplified sliding window approximation).
            total_success = counters.get("local_success", 0) + counters.get("cloud_success", 0)
            total_fail = counters.get("local_failures", 0) + counters.get("cloud_failures", 0)
            total = total_success + total_fail
            success_rate = (total_success / total * 100.0) if total > 0 else 100.0

            fail_fast_count = counters.get("force_cloud_failfast_total", 0)

            return {
                "ok": True,
                "latency_p50_ms": latencies.get("p50_ms", 0.0),
                "latency_p95_ms": latencies.get("p95_ms", 0.0),
                "success_rate_pct": round(success_rate, 2),
                "fail_fast_count": fail_fast_count,
            }

        @self.app.get("/api/ops/runtime_snapshot")
        async def ops_runtime_snapshot():
            """Deep observability snapshot linking all states."""
            router = self.deps.get("router")
            if not router:
                return {"ok": False, "error": "router_not_found"}
                
            task_queue = self.deps.get("queue")
            queue_stats = task_queue.get_metrics() if getattr(task_queue, "get_metrics", None) else {}
            
            openclaw = router.openclaw_client
            tier_state = openclaw.get_tier_state_export() if getattr(openclaw, "get_tier_state_export", None) else {}
            
            return {
                "ok": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "router_state": {
                    "is_local_available": router.is_local_available,
                    "active_tier": getattr(router, "active_tier", "default"),
                    "local_failures": router._stats.get("local_failures", 0),
                    "cloud_failures": router._stats.get("cloud_failures", 0)
                },
                "tier_state": tier_state,
                "breaker_state": {
                    "preflight_cache": {k: {"expires_in": v[0] - time.time(), "error": v[1]} for k, v in getattr(router, "_preflight_cache", {}).items() if v[0] > time.time()}
                },
                "queue_depth": queue_stats.get("active_tasks", 0),
                "queue_stats": queue_stats,
                "observability": get_observability_snapshot()
            }

        @self.app.post("/api/ops/models")
        async def ops_models_control(
            payload: Dict[str, Any] = Body(...),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            [R12] Управление жизненным циклом локальных моделей.
            Payload: {"action": "load"|"unload"|"unload_all", "model": "model_name"}
            """
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps.get("router")
            if not router:
                return {"ok": False, "error": "router_not_found"}
            
            action = payload.get("action")
            model_name = payload.get("model")
            
            try:
                if action == "load":
                    if not model_name:
                        return {"ok": False, "error": "model_name_required"}
                    success = await router.load_local_model(model_name)
                    return {"ok": success, "action": action, "model": model_name}
                
                elif action == "unload":
                    if not model_name:
                        return {"ok": False, "error": "model_name_required"}
                    success = await router.unload_model_manual(model_name)
                    return {"ok": success, "action": action, "model": model_name}
                
                elif action == "unload_all":
                    await router.unload_models_manual()
                    return {"ok": True, "action": action}
                
                else:
                    return {"ok": False, "error": "invalid_action", "supported": ["load", "unload", "unload_all"]}
            except Exception as e:
                logger.error("ops_models_control_failed", error=str(e))
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}

        @self.app.get("/api/ecosystem/health/export")
        async def ecosystem_health_export():
            """Экспортирует расширенный ecosystem health report в JSON-файл."""
            router = self.deps["router"]
            openclaw = self.deps.get("openclaw_client")
            voice_gateway = self.deps.get("voice_gateway_client")
            krab_ear = self.deps.get("krab_ear_client")
            payload = await EcosystemHealthService(
                router=router,
                openclaw_client=openclaw,
                voice_gateway_client=voice_gateway,
                krab_ear_client=krab_ear,
            ).collect()
            ops_dir = Path("artifacts/ops")
            ops_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
            out_path = ops_dir / f"ecosystem_health_web_{stamp}.json"
            with out_path.open("w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
            return FileResponse(
                str(out_path),
                media_type="application/json",
                filename=out_path.name,
            )

        @self.app.get("/api/model/recommend")
        async def model_recommend(profile: str = Query(default="chat", description="Профиль задачи")):
            router = self.deps["router"]
            return router.get_profile_recommendation(profile)

        @self.app.post("/api/model/preflight")
        async def model_preflight(payload: dict = Body(...)):
            """
            Возвращает preflight-план задачи до выполнения:
            профиль, канал/модель, confirm-step, риски и cost hint.
            """
            router = self.deps["router"]
            if not hasattr(router, "get_task_preflight"):
                return {"ok": False, "error": "task_preflight_not_supported"}

            prompt = str(payload.get("prompt", "")).strip()
            if not prompt:
                raise HTTPException(status_code=400, detail="prompt_required")

            task_type = str(payload.get("task_type", "chat")).strip().lower() or "chat"
            preferred_model = payload.get("preferred_model")
            preferred_model_str = str(preferred_model).strip() if preferred_model else None
            confirm_expensive = bool(payload.get("confirm_expensive", False))

            preflight = router.get_task_preflight(
                prompt=prompt,
                task_type=task_type,
                preferred_model=preferred_model_str,
                confirm_expensive=confirm_expensive,
            )
            return {"ok": True, "preflight": preflight}

        @self.app.get("/api/model/local/status")
        async def model_local_status():
            """Возвращает статус локального рантайма LLM."""
            router = self.deps["router"]
            is_available = bool(getattr(router, "is_local_available", False))
            active_model = str(getattr(router, "active_local_model", "") or "").strip()
            engine_raw = str(getattr(router, "local_engine", "unknown") or "unknown").strip()
            engine_norm = engine_raw.lower().replace("-", "").replace("_", "")

            if engine_norm == "lmstudio":
                runtime_url = str(getattr(router, "lm_studio_url", "") or "").strip()
            elif engine_norm == "ollama":
                runtime_url = str(getattr(router, "ollama_url", "") or "").strip()
            else:
                runtime_url = ""

            lifecycle_status = "loaded" if (is_available and bool(active_model)) else "not_loaded"

            return {
                "ok": True,
                # Каноничный формат для frontend R10.
                "status": lifecycle_status,
                "model_name": active_model or "",
                "engine": engine_raw,
                "url": runtime_url or "n/a",
                # Backward compatibility для существующих клиентов.
                "details": {
                    "available": is_available,
                    "engine": engine_raw,
                    "active_model": active_model,
                    "is_loaded": lifecycle_status == "loaded",
                    "url": runtime_url or "n/a",
                },
                # Старый вложенный формат оставляем на переходный период.
                "status_legacy": {
                    "available": is_available,
                    "engine": engine_raw,
                    "active_model": active_model,
                    "is_loaded": lifecycle_status == "loaded",
                    "url": runtime_url or "n/a",
                },
            }

        @self.app.post("/api/model/local/load-default")
        async def model_local_load_default(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Загружает предпочтительную локальную модель (write endpoint)."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]
            preferred = getattr(router, "local_preferred_model", None)
            if not preferred:
                return {"ok": False, "error": "no_preferred_model_configured"}
            
            # Используем существующий механизм smart_load
            success = await router._smart_load(preferred, reason="web_forced")
            return {"ok": success, "model": preferred}

        @self.app.post("/api/model/local/unload")
        async def model_local_unload(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Выгружает все локальные модели для освобождения памяти (write endpoint)."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]
            
            freed_gb = 0.0
            if hasattr(router, "_evict_idle_models"):
                # Вызываем с огромным нужным объемом или просто через unload_local_model
                # Но проще через unload_local_model если мы знаем active_model
                active = getattr(router, "active_local_model", None)
                if active:
                    success = await router.unload_local_model(active)
                    if success:
                        router.active_local_model = None
                        return {"ok": True, "unloaded": active}
                
                # Если активной нет, но есть загруженные (по данным _evict_idle_models)
                freed_gb = await router._evict_idle_models(needed_gb=100.0) # Попытаемся выгрузить всё
            
            return {"ok": True, "freed_gb_estimate": round(freed_gb, 1)}

        @self.app.get("/api/model/explain")
        async def model_explain(
            task_type: str = Query(default="chat", description="Тип задачи для preflight"),
            prompt: str = Query(default="", description="Опциональный prompt для preflight explain"),
            preferred_model: str = Query(default="", description="Опциональная предпочтительная модель"),
            confirm_expensive: bool = Query(default=False, description="Флаг подтверждения дорогого cloud пути"),
        ):
            """
            Explainability endpoint: почему выбран канал/модель.

            Возвращает:
            - last route (route_reason/route_detail);
            - policy snapshot;
            - preflight (если передан prompt).
            """
            router = self.deps["router"]
            normalized_prompt = str(prompt or "").strip()
            normalized_task_type = str(task_type or "chat").strip().lower() or "chat"
            preferred_model_str = str(preferred_model or "").strip() or None

            if hasattr(router, "get_route_explain"):
                explain = router.get_route_explain(
                    prompt=normalized_prompt,
                    task_type=normalized_task_type,
                    preferred_model=preferred_model_str,
                    confirm_expensive=bool(confirm_expensive),
                )
                return {"ok": True, "explain": explain}

            # Fallback для старого роутера без get_route_explain.
            last_route = router.get_last_route() if hasattr(router, "get_last_route") else {}
            preflight = None
            if normalized_prompt and hasattr(router, "get_task_preflight"):
                preflight = router.get_task_preflight(
                    prompt=normalized_prompt,
                    task_type=normalized_task_type,
                    preferred_model=preferred_model_str,
                    confirm_expensive=bool(confirm_expensive),
                )
            return {
                "ok": True,
                "explain": {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "last_route": last_route if isinstance(last_route, dict) else {},
                    "reason": {
                        "code": str(last_route.get("route_reason", "")).strip() or "unknown",
                        "detail": str(last_route.get("route_detail", "")).strip(),
                        "human": "Роутер не поддерживает расширенный explain; показан базовый срез.",
                    },
                    "policy": {
                        "force_mode": str(getattr(router, "force_mode", "auto")),
                        "routing_policy": str(getattr(router, "routing_policy", "unknown")),
                        "cloud_soft_cap_reached": bool(getattr(router, "cloud_soft_cap_reached", False)),
                        "local_available": bool(getattr(router, "is_local_available", False)),
                    },
                    "preflight": preflight,
                    "explainability_score": 40 if last_route else 0,
                    "transparency_level": "low" if not last_route else "medium",
                },
            }

        def _normalize_force_mode(force_mode: str) -> str:
            """Нормализует внутренние force_* режимы в UI-вид: auto/local/cloud."""
            normalized = str(force_mode or "").strip().lower()
            if normalized in {"force_local", "local"}:
                return "local"
            if normalized in {"force_cloud", "cloud"}:
                return "cloud"
            return "auto"

        async def _build_model_catalog(router_obj) -> dict:
            """
            Собирает каталог моделей и текущих настроек для web-панели.
            Нужен для кнопочного UX без ручных `!model` команд.
            """
            cloud_slots_raw = getattr(router_obj, "models", {}) or {}
            cloud_slots = (
                {str(k): str(v) for k, v in cloud_slots_raw.items()}
                if isinstance(cloud_slots_raw, dict)
                else {}
            )
            slot_list = sorted(cloud_slots.keys()) if cloud_slots else ["chat", "thinking", "pro", "coding"]
            force_mode = _normalize_force_mode(getattr(router_obj, "force_mode", "auto"))
            local_engine = str(getattr(router_obj, "local_engine", "") or "")
            local_active_model = str(getattr(router_obj, "active_local_model", "") or "")
            local_available = bool(getattr(router_obj, "is_local_available", False))

            local_models: list[dict] = []
            local_models_error = ""
            if hasattr(router_obj, "list_local_models_verbose"):
                try:
                    raw_local_models = await router_obj.list_local_models_verbose()
                    if isinstance(raw_local_models, list):
                        for item in raw_local_models:
                            if not isinstance(item, dict):
                                continue
                            model_id = str(item.get("id", "")).strip()
                            if not model_id:
                                continue
                            local_models.append(
                                {
                                    "id": model_id,
                                    "loaded": bool(item.get("loaded", False)),
                                    "type": str(item.get("type", "llm")),
                                    "size_human": str(item.get("size_human", "n/a")),
                                }
                            )
                except Exception as exc:  # noqa: BLE001
                    local_models_error = str(exc)

            if local_active_model and not any(str(item.get("id")) == local_active_model for item in local_models):
                local_models.insert(
                    0,
                    {
                        "id": local_active_model,
                        "loaded": True,
                        "type": "llm",
                        "size_human": "n/a",
                    },
                )

            cloud_presets: list[dict[str, str]] = []
            alias_items: list[dict[str, str]] = []
            try:
                from src.handlers.commands import MODEL_FRIENDLY_ALIASES, normalize_model_alias

                canonical_cloud_models = sorted(
                    {
                        str(model_id).strip()
                        for model_id in MODEL_FRIENDLY_ALIASES.values()
                        if str(model_id).strip()
                    }
                )
                for model_id in canonical_cloud_models:
                    provider = "openai" if model_id.startswith("openai/") else (
                        "google" if model_id.startswith("google/") else "other"
                    )
                    cloud_presets.append(
                        {
                            "id": model_id,
                            "provider": provider,
                            "label": model_id.replace("google/", "Gemini • ").replace("openai/", "OpenAI • "),
                        }
                    )

                for alias_key in sorted(MODEL_FRIENDLY_ALIASES.keys()):
                    resolved_id, _ = normalize_model_alias(alias_key)
                    alias_items.append(
                        {
                            "alias": alias_key,
                            "model": resolved_id,
                        }
                    )
            except Exception:
                cloud_presets = []
                alias_items = []

            if not cloud_presets:
                cloud_presets = [
                    {"id": "google/gemini-2.5-flash", "provider": "google", "label": "Gemini • gemini-2.5-flash"},
                    {"id": "google/gemini-3-pro-preview", "provider": "google", "label": "Gemini • gemini-3-pro-preview"},
                    {"id": "openai/gpt-5-mini", "provider": "openai", "label": "OpenAI • gpt-5-mini"},
                    {"id": "openai/gpt-5-codex", "provider": "openai", "label": "OpenAI • gpt-5-codex"},
                ]

            quick_presets = [
                {
                    "id": "balanced_auto",
                    "title": "Balanced Auto",
                    "description": "Авто-режим: local-first с сильными cloud-слотами.",
                },
                {
                    "id": "local_focus",
                    "title": "Local Focus",
                    "description": "Force local + локальная модель в ключевых слотах.",
                },
                {
                    "id": "cloud_reasoning",
                    "title": "Cloud Reasoning",
                    "description": "Force cloud + усиленный reasoning/coding профиль.",
                },
            ]

            return {
                "force_mode": force_mode,
                "slots": slot_list,
                "cloud_slots": cloud_slots,
                "local_engine": local_engine,
                "local_available": local_available,
                "local_active_model": local_active_model,
                "local_models": local_models,
                "local_models_error": local_models_error,
                "cloud_presets": cloud_presets,
                "aliases": alias_items,
                "quick_presets": quick_presets,
            }

        @self.app.get("/api/model/catalog")
        async def model_catalog():
            """Каталог моделей/режимов для web-панели с кнопочным управлением."""
            router = self.deps["router"]
            return {"ok": True, "catalog": await _build_model_catalog(router)}

        @self.app.post("/api/model/apply")
        async def model_apply(
            payload: dict = Body(...),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Применяет изменения модели/режима из web UI без ручных команд."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]
            black_box = self.deps.get("black_box")

            action = str(payload.get("action", "")).strip().lower()
            if not action:
                raise HTTPException(status_code=400, detail="model_apply_action_required")

            try:
                from src.handlers.commands import normalize_model_alias
            except Exception:
                def normalize_model_alias(raw_model_name: str) -> tuple[str, str]:
                    text = str(raw_model_name or "").strip()
                    return text, ""

            result_payload: dict[str, object] = {}
            message_text = "✅ Изменения применены."

            if action == "set_mode":
                mode = str(payload.get("mode", "auto")).strip().lower() or "auto"
                if mode not in {"auto", "local", "cloud"}:
                    raise HTTPException(status_code=400, detail="model_apply_invalid_mode")
                if not hasattr(router, "set_force_mode"):
                    raise HTTPException(status_code=400, detail="model_apply_set_mode_not_supported")
                update_result = router.set_force_mode(mode)
                result_payload = {
                    "mode": _normalize_force_mode(getattr(router, "force_mode", "auto")),
                    "router_response": str(update_result),
                }
                message_text = f"✅ Режим обновлен: {result_payload['mode']}"

            elif action == "set_slot_model":
                slot = str(payload.get("slot", "")).strip().lower()
                raw_model = str(payload.get("model", "")).strip()
                if not slot or not raw_model:
                    raise HTTPException(status_code=400, detail="model_apply_slot_and_model_required")
                if not hasattr(router, "models") or not isinstance(getattr(router, "models"), dict):
                    raise HTTPException(status_code=400, detail="model_apply_slots_not_supported")
                if slot not in router.models:
                    available = ", ".join(sorted(router.models.keys()))
                    raise HTTPException(
                        status_code=400,
                        detail=f"model_apply_unknown_slot: {slot}; available={available}",
                    )
                resolved_model, alias_note = normalize_model_alias(raw_model)
                old_model = str(router.models.get(slot, ""))
                router.models[slot] = resolved_model
                result_payload = {
                    "slot": slot,
                    "old_model": old_model,
                    "new_model": resolved_model,
                    "alias_note": alias_note,
                }
                message_text = f"✅ Слот `{slot}`: `{old_model}` → `{resolved_model}`"

            elif action == "apply_preset":
                preset_id = str(payload.get("preset", "")).strip().lower()
                if not preset_id:
                    raise HTTPException(status_code=400, detail="model_apply_preset_required")
                if not hasattr(router, "models") or not isinstance(getattr(router, "models"), dict):
                    raise HTTPException(status_code=400, detail="model_apply_slots_not_supported")

                local_override = str(payload.get("local_model", "")).strip() or str(
                    getattr(router, "active_local_model", "") or ""
                )
                if not local_override:
                    local_override = "zai-org/glm-4.6v-flash"

                presets: dict[str, dict[str, object]] = {
                    "balanced_auto": {
                        "mode": "auto",
                        "slots": {
                            "chat": "google/gemini-2.5-flash",
                            "thinking": "google/gemini-2.5-pro",
                            "pro": "google/gemini-3-pro-preview",
                            "coding": "openai/gpt-5-codex",
                        },
                    },
                    "local_focus": {
                        "mode": "local",
                        "slots": {
                            "chat": local_override,
                            "thinking": local_override,
                            "pro": local_override,
                            "coding": local_override,
                        },
                    },
                    "cloud_reasoning": {
                        "mode": "cloud",
                        "slots": {
                            "chat": "google/gemini-2.5-flash",
                            "thinking": "google/gemini-2.5-pro",
                            "pro": "google/gemini-3-pro-preview",
                            "coding": "openai/gpt-5-codex",
                        },
                    },
                }
                chosen = presets.get(preset_id)
                if not chosen:
                    raise HTTPException(status_code=400, detail=f"model_apply_unknown_preset: {preset_id}")

                applied_changes: list[dict[str, str]] = []
                for slot, model_id in dict(chosen.get("slots", {})).items():
                    if slot not in router.models:
                        continue
                    resolved_model, _ = normalize_model_alias(str(model_id))
                    previous = str(router.models.get(slot, ""))
                    router.models[slot] = resolved_model
                    applied_changes.append(
                        {
                            "slot": str(slot),
                            "old_model": previous,
                            "new_model": resolved_model,
                        }
                    )

                target_mode = str(chosen.get("mode", "auto")).strip().lower() or "auto"
                if hasattr(router, "set_force_mode"):
                    router.set_force_mode(target_mode)

                result_payload = {
                    "preset": preset_id,
                    "mode": _normalize_force_mode(getattr(router, "force_mode", "auto")),
                    "changes": applied_changes,
                }
                message_text = f"✅ Пресет `{preset_id}` применён ({len(applied_changes)} слотов)."

            else:
                raise HTTPException(status_code=400, detail=f"model_apply_unknown_action: {action}")

            if black_box and hasattr(black_box, "log_event"):
                black_box.log_event("web_model_apply", f"action={action} result={message_text}")

            return {
                "ok": True,
                "action": action,
                "message": message_text,
                "result": result_payload,
                "catalog": await _build_model_catalog(router),
            }

        @self.app.get("/api/model/feedback")
        async def model_feedback_summary(
            profile: str | None = Query(default=None),
            top: int = Query(default=5, ge=1, le=20),
        ):
            """Сводка оценок качества роутинга моделей."""
            router = self.deps["router"]
            if not hasattr(router, "get_feedback_summary"):
                return {"ok": False, "error": "feedback_summary_not_supported"}
            normalized_profile = str(profile).strip().lower() if profile is not None else None
            return {
                "ok": True,
                "feedback": router.get_feedback_summary(profile=normalized_profile, top=top),
            }

        @self.app.post("/api/model/feedback")
        async def model_feedback_submit(
            payload: dict = Body(...),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            x_idempotency_key: str = Header(default="", alias="X-Idempotency-Key"),
            token: str = Query(default=""),
        ):
            """Принимает оценку качества ответа (1-5) для самообучающегося роутинга."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]
            if not hasattr(router, "submit_feedback"):
                return {"ok": False, "error": "feedback_submit_not_supported"}

            idem_key = (x_idempotency_key or "").strip()
            cached = self._idempotency_get("model_feedback_submit", idem_key)
            if cached:
                return cached

            score = payload.get("score")
            profile = payload.get("profile")
            model_name = payload.get("model")
            channel = payload.get("channel")
            note = payload.get("note", "")

            try:
                result = router.submit_feedback(
                    score=int(score),
                    profile=str(profile).strip().lower() if profile is not None else None,
                    model_name=str(model_name).strip() if model_name is not None else None,
                    channel=str(channel).strip().lower() if channel is not None else None,
                    note=str(note).strip(),
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"feedback_submit_failed: {exc}") from exc

            response_payload = {"ok": True, "result": result}
            self._idempotency_set("model_feedback_submit", idem_key, response_payload)
            return response_payload

        @self.app.get("/api/ops/usage")
        async def ops_usage():
            """Агрегированный usage-срез роутера моделей."""
            router = self.deps["router"]
            if hasattr(router, "get_usage_summary"):
                return {"ok": True, "usage": router.get_usage_summary()}
            return {"ok": False, "error": "usage_summary_not_supported"}

        @self.app.get("/api/ops/cost-report")
        async def ops_cost_report(monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000)):
            """Оценочный отчет по затратам local/cloud маршрутизации."""
            router = self.deps["router"]
            if hasattr(router, "get_cost_report"):
                return {"ok": True, "report": router.get_cost_report(monthly_calls_forecast=monthly_calls_forecast)}
            return {"ok": False, "error": "cost_report_not_supported"}

        @self.app.get("/api/ops/runway")
        async def ops_runway(
            credits_usd: float = Query(default=300.0, ge=0.0, le=1000000.0),
            horizon_days: int = Query(default=80, ge=1, le=3650),
            reserve_ratio: float = Query(default=0.1, ge=0.0, le=0.95),
            monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
        ):
            """План расхода кредитов: burn-rate, runway и safe calls/day."""
            router = self.deps["router"]
            if hasattr(router, "get_credit_runway_report"):
                return {
                    "ok": True,
                    "runway": router.get_credit_runway_report(
                        credits_usd=credits_usd,
                        horizon_days=horizon_days,
                        reserve_ratio=reserve_ratio,
                        monthly_calls_forecast=monthly_calls_forecast,
                    ),
                }
            return {"ok": False, "error": "ops_runway_not_supported"}

        @self.app.get("/api/ops/executive-summary")
        async def ops_executive_summary(monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000)):
            """Компактный ops executive summary: KPI + риски + рекомендации."""
            router = self.deps["router"]
            if hasattr(router, "get_ops_executive_summary"):
                return {"ok": True, "summary": router.get_ops_executive_summary(monthly_calls_forecast=monthly_calls_forecast)}
            return {"ok": False, "error": "ops_executive_summary_not_supported"}

        @self.app.get("/api/ops/report")
        async def ops_report(
            history_limit: int = Query(default=20, ge=1, le=200),
            monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
        ):
            """Единый ops отчет: usage + alerts + costs + history."""
            router = self.deps["router"]
            if hasattr(router, "get_ops_report"):
                return {
                    "ok": True,
                    "report": router.get_ops_report(
                        history_limit=history_limit,
                        monthly_calls_forecast=monthly_calls_forecast,
                    ),
                }
            return {"ok": False, "error": "ops_report_not_supported"}

        @self.app.get("/api/ops/report/export")
        async def ops_report_export(
            history_limit: int = Query(default=50, ge=1, le=200),
            monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
        ):
            """Экспортирует полный ops report в JSON-файл."""
            router = self.deps["router"]
            if not hasattr(router, "get_ops_report"):
                return {"ok": False, "error": "ops_report_not_supported"}
            report = router.get_ops_report(
                history_limit=history_limit,
                monthly_calls_forecast=monthly_calls_forecast,
            )
            ops_dir = Path("artifacts/ops")
            ops_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
            out_path = ops_dir / f"ops_report_web_{stamp}.json"
            with out_path.open("w", encoding="utf-8") as fp:
                json.dump(report, fp, ensure_ascii=False, indent=2)
            return FileResponse(
                str(out_path),
                media_type="application/json",
                filename=out_path.name,
            )

        @self.app.get("/api/ops/bundle")
        async def ops_bundle(
            history_limit: int = Query(default=50, ge=1, le=200),
            monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
        ):
            """Единый bundle: ops report + health snapshot."""
            router = self.deps["router"]
            if not hasattr(router, "get_ops_report"):
                return {"ok": False, "error": "ops_report_not_supported"}
            openclaw = self.deps.get("openclaw_client")
            voice_gateway = self.deps.get("voice_gateway_client")
            local_ok = await router.check_local_health()
            openclaw_ok = await openclaw.health_check() if openclaw else False
            voice_ok = await voice_gateway.health_check() if voice_gateway else False
            return {
                "ok": True,
                "bundle": {
                    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "ops_report": router.get_ops_report(
                        history_limit=history_limit,
                        monthly_calls_forecast=monthly_calls_forecast,
                    ),
                    "health": {
                        "openclaw": openclaw_ok,
                        "local_lm": local_ok,
                        "voice_gateway": voice_ok,
                    },
                },
            }

        @self.app.get("/api/ops/bundle/export")
        async def ops_bundle_export(
            history_limit: int = Query(default=50, ge=1, le=200),
            monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
        ):
            """Экспортирует единый ops bundle в JSON-файл."""
            router = self.deps["router"]
            if not hasattr(router, "get_ops_report"):
                return {"ok": False, "error": "ops_report_not_supported"}
            openclaw = self.deps.get("openclaw_client")
            voice_gateway = self.deps.get("voice_gateway_client")
            local_ok = await router.check_local_health()
            openclaw_ok = await openclaw.health_check() if openclaw else False
            voice_ok = await voice_gateway.health_check() if voice_gateway else False

            payload = {
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "ops_report": router.get_ops_report(
                    history_limit=history_limit,
                    monthly_calls_forecast=monthly_calls_forecast,
                ),
                "health": {
                    "openclaw": openclaw_ok,
                    "local_lm": local_ok,
                    "voice_gateway": voice_ok,
                },
            }
            ops_dir = Path("artifacts/ops")
            ops_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
            out_path = ops_dir / f"ops_bundle_web_{stamp}.json"
            with out_path.open("w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
            return FileResponse(
                str(out_path),
                media_type="application/json",
                filename=out_path.name,
            )

        @self.app.get("/api/ops/alerts")
        async def ops_alerts():
            """Операционные алерты по расходам и маршрутизации."""
            router = self.deps["router"]
            if hasattr(router, "get_ops_alerts"):
                return {"ok": True, "alerts": router.get_ops_alerts()}
            return {"ok": False, "error": "ops_alerts_not_supported"}

        @self.app.get("/api/ops/history")
        async def ops_history(limit: int = Query(default=30, ge=1, le=200)):
            """История ops snapshot-ов (alerts/status over time)."""
            router = self.deps["router"]
            if hasattr(router, "get_ops_history"):
                return {"ok": True, "history": router.get_ops_history(limit=limit)}
            return {"ok": False, "error": "ops_history_not_supported"}

        @self.app.post("/api/ops/maintenance/prune")
        async def ops_prune(
            payload: dict = Body(default={}),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Очищает ops history по retention-параметрам."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]
            if not hasattr(router, "prune_ops_history"):
                return {"ok": False, "error": "ops_prune_not_supported"}
            max_age_days = int(payload.get("max_age_days", 30))
            keep_last = int(payload.get("keep_last", 100))
            try:
                result = router.prune_ops_history(max_age_days=max_age_days, keep_last=keep_last)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "result": result}

        @self.app.post("/api/ops/ack/{code}")
        async def ops_ack(
            code: str,
            payload: dict = Body(default={}),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Подтверждает alert код оператором."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]
            if not hasattr(router, "acknowledge_ops_alert"):
                return {"ok": False, "error": "ops_ack_not_supported"}
            actor = str(payload.get("actor", "web_api")).strip() or "web_api"
            note = str(payload.get("note", "")).strip()
            try:
                result = router.acknowledge_ops_alert(code=code, actor=actor, note=note)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "result": result}

        @self.app.delete("/api/ops/ack/{code}")
        async def ops_unack(
            code: str,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Снимает подтверждение alert кода."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]
            if not hasattr(router, "clear_ops_alert_ack"):
                return {"ok": False, "error": "ops_unack_not_supported"}
            try:
                result = router.clear_ops_alert_ack(code=code)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "result": result}

        @self.app.post("/api/assistant/attachment")
        async def assistant_attachment_upload(
            file: UploadFile = File(...),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            Загружает вложение для web-assistant и возвращает prompt-snippet.
            Поддерживает текст/PDF/DOCX (извлечение текста best effort),
            а также изображения/видео/архивы (метаданные + локальный путь).
            """
            self._assert_write_access(x_krab_web_key, token)
            black_box = self.deps.get("black_box")

            if not file:
                raise HTTPException(status_code=400, detail="assistant_attachment_file_required")
            original_name = str(file.filename or "").strip()
            if not original_name:
                raise HTTPException(status_code=400, detail="assistant_attachment_filename_required")

            raw = await file.read()
            if not raw:
                raise HTTPException(status_code=400, detail="assistant_attachment_empty_file")

            max_bytes = self._web_attachment_max_bytes()
            if len(raw) > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"assistant_attachment_too_large: max={max_bytes} bytes",
                )

            safe_name = self._sanitize_attachment_name(original_name)
            guessed_type = mimetypes.guess_type(safe_name)[0] or ""
            content_type = str(file.content_type or guessed_type or "application/octet-stream")

            uploads_dir = Path("artifacts/web_uploads")
            uploads_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            short_hash = hashlib.sha256(raw).hexdigest()[:10]
            stored_name = f"{ts}_{short_hash}_{safe_name}"
            stored_path = uploads_dir / stored_name
            stored_path.write_bytes(raw)

            attachment = self._build_attachment_prompt(
                file_name=safe_name,
                content_type=content_type,
                raw_bytes=raw,
                stored_path=stored_path,
            )

            if black_box and hasattr(black_box, "log_event"):
                black_box.log_event(
                    "web_assistant_attachment",
                    f"name={safe_name} type={content_type} size={len(raw)} kind={attachment.get('kind')}",
                )

            return {"ok": True, "attachment": attachment}

        @self.app.get("/api/assistant/capabilities")
        async def assistant_capabilities():
            """Возвращает возможности web-native assistant режима."""
            return {
                "mode": "web_native",
                "endpoint": "/api/assistant/query",
                "preflight_endpoint": "/api/model/preflight",
                "feedback_endpoint": "/api/model/feedback",
                "model_catalog_endpoint": "/api/model/catalog",
                "model_apply_endpoint": "/api/model/apply",
                "attachment_endpoint": "/api/assistant/attachment",
                "auth": "X-Krab-Web-Key header or token query (if WEB_API_KEY configured)",
                "task_types": ["chat", "coding", "reasoning", "creative", "moderation", "security", "infra", "review"],
                "notes": [
                    "Работает без Telegram-интерфейса.",
                    "Использует тот же роутер моделей и policy, что и Telegram-бот.",
                    "Для критичных задач можно передать `confirm_expensive=true`.",
                    "Оценки качества 1-5 можно отправлять через /api/model/feedback.",
                    "Модельные слоты и режимы можно менять через /api/model/apply.",
                    "Файлы можно загружать через /api/assistant/attachment.",
                ],
            }

        @self.app.post("/api/assistant/query")
        async def assistant_query(
            request: Request,
            payload: dict = Body(...),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            x_krab_client: str = Header(default="", alias="X-Krab-Client"),
            x_idempotency_key: str = Header(default="", alias="X-Idempotency-Key"),
            token: str = Query(default=""),
        ):
            """
            Выполняет AI-запрос напрямую через web-панель (без Telegram чата).
            Это must-have для web-first сценариев управления Крабом.
            """
            self._assert_write_access(x_krab_web_key, token)
            client_ip = request.client.host if request.client else "unknown"
            client_key = (x_krab_client or "").strip() or client_ip
            idem_key = (x_idempotency_key or "").strip()
            cached = self._idempotency_get("assistant_query", idem_key)
            if cached:
                return cached
            self._enforce_assistant_rate_limit(client_key)
            router = self.deps.get("router")
            if not router:
                raise HTTPException(status_code=503, detail="router_not_configured")

            prompt = str(payload.get("prompt", "")).strip()
            if not prompt:
                raise HTTPException(status_code=400, detail="prompt_required")

            task_type = str(payload.get("task_type", "chat")).strip().lower() or "chat"
            use_rag = bool(payload.get("use_rag", False))
            preferred_model = payload.get("preferred_model")
            preferred_model_str = str(preferred_model).strip() if preferred_model else None
            confirm_expensive = bool(payload.get("confirm_expensive", False))
            requested_force_mode_raw = str(payload.get("force_mode", "")).strip().lower()
            requested_force_mode = requested_force_mode_raw if requested_force_mode_raw in {"auto", "local", "cloud"} else ""

            # Web UX-хелпер: поддержка команд вида `.model ...` и `!model ...`
            # прямо из web-assistant input. Иначе команда уходила в LLM как обычный prompt.
            command_prompt = prompt
            if command_prompt.startswith(".model"):
                command_prompt = f"!{command_prompt[1:]}"

            if command_prompt.startswith("!model"):
                try:
                    from src.handlers.commands import (
                        parse_model_set_request,
                        normalize_model_alias,
                        render_model_presets_text,
                    )
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(
                        status_code=500,
                        detail=f"assistant_command_import_failed: {exc}",
                    ) from exc

                try:
                    tokens = shlex.split(command_prompt[1:])
                except Exception:
                    tokens = command_prompt[1:].split()

                if not tokens or tokens[0].lower() != "model":
                    raise HTTPException(status_code=400, detail="assistant_model_command_invalid")

                subcommand = tokens[1].strip().lower() if len(tokens) >= 2 else ""
                if subcommand in {"presets", "catalog", "quick"}:
                    response_payload = {
                        "ok": True,
                        "mode": "web_native",
                        "task_type": task_type,
                        "profile": "chat",
                        "command_mode": True,
                        "last_route": router.get_last_route() if hasattr(router, "get_last_route") else {},
                        "reply": render_model_presets_text(),
                    }
                    self._idempotency_set("assistant_query", idem_key, response_payload)
                    return response_payload

                if subcommand in {"local", "cloud", "auto"} and hasattr(router, "set_force_mode"):
                    result = router.set_force_mode(subcommand)
                    response_payload = {
                        "ok": True,
                        "mode": "web_native",
                        "task_type": task_type,
                        "profile": "chat",
                        "command_mode": True,
                        "last_route": router.get_last_route() if hasattr(router, "get_last_route") else {},
                        "reply": f"✅ Режим обновлен: {result}",
                    }
                    self._idempotency_set("assistant_query", idem_key, response_payload)
                    return response_payload

                if subcommand == "set":
                    parsed = parse_model_set_request(tokens, list(router.models.keys()))
                    if not parsed.get("ok"):
                        response_payload = {
                            "ok": True,
                            "mode": "web_native",
                            "task_type": task_type,
                            "profile": "chat",
                            "command_mode": True,
                            "last_route": router.get_last_route() if hasattr(router, "get_last_route") else {},
                            "reply": str(parsed.get("error") or "❌ Некорректная команда"),
                        }
                        self._idempotency_set("assistant_query", idem_key, response_payload)
                        return response_payload

                    slot = str(parsed["slot"])
                    model_raw = str(parsed["model_name"])
                    model_resolved, alias_note = normalize_model_alias(model_raw)
                    old_value = str(router.models.get(slot, "—"))
                    router.models[slot] = model_resolved

                    reply_lines = []
                    if parsed.get("warning"):
                        reply_lines.append(str(parsed["warning"]))
                    if alias_note:
                        reply_lines.append(alias_note)
                    reply_lines.append(
                        f"✅ Slot `{slot}` обновлен: `{old_value}` → `{model_resolved}`"
                    )
                    reply_lines.append("Подсказка: `!model` или `!model preflight chat Тест`")

                    response_payload = {
                        "ok": True,
                        "mode": "web_native",
                        "task_type": task_type,
                        "profile": "chat",
                        "command_mode": True,
                        "last_route": router.get_last_route() if hasattr(router, "get_last_route") else {},
                        "reply": "\n".join(reply_lines),
                    }
                    self._idempotency_set("assistant_query", idem_key, response_payload)
                    return response_payload

            try:
                # Если UI передал force_mode, синхронизируем режим до выполнения запроса.
                if requested_force_mode and hasattr(router, "set_force_mode"):
                    router.set_force_mode(requested_force_mode)
                effective_force_mode = _normalize_force_mode(
                    getattr(router, "force_mode", "auto")
                )

                reply = await router.route_query(
                    prompt=prompt,
                    task_type=task_type,
                    context=[],
                    chat_type="private",
                    is_owner=True,
                    use_rag=use_rag,
                    preferred_model=preferred_model_str,
                    confirm_expensive=confirm_expensive,
                )

                # Local-first аварийная деградация:
                # если cloud-ключ скомпрометирован/отклонён, пробуем принудительный local.
                # В force_cloud это запрещено: режим должен быть строго cloud-only.
                leaked_key_marker = "reported as leaked"
                if (
                    isinstance(reply, str)
                    and leaked_key_marker in reply.lower()
                    and effective_force_mode != "cloud"
                    and hasattr(router, "check_local_health")
                ):
                    local_ok = bool(await router.check_local_health(force=True))
                    if local_ok:
                        previous_mode = str(getattr(router, "force_mode", "auto"))
                        try:
                            router.force_mode = "force_local"
                            local_reply = await router.route_query(
                                prompt=prompt,
                                task_type=task_type,
                                context=[],
                                chat_type="private",
                                is_owner=True,
                                use_rag=use_rag,
                                preferred_model=None,
                                confirm_expensive=confirm_expensive,
                            )
                            if isinstance(local_reply, str) and local_reply.strip():
                                reply = (
                                    "⚠️ Cloud API key отклонён (`reported as leaked`). "
                                    "Переключился на local-first ответ.\n\n"
                                    f"{local_reply}"
                                )
                        finally:
                            router.force_mode = previous_mode
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"assistant_query_failed: {exc}") from exc

            profile = router.classify_task_profile(prompt, task_type) if hasattr(router, "classify_task_profile") else task_type
            recommendation = (
                router.get_profile_recommendation(profile)
                if hasattr(router, "get_profile_recommendation")
                else {"profile": profile}
            )
            last_route = (
                router.get_last_route()
                if hasattr(router, "get_last_route")
                else {}
            )
            black_box = self.deps.get("black_box")
            if black_box and hasattr(black_box, "log_event"):
                black_box.log_event(
                    "web_assistant_query",
                    f"task_type={task_type} profile={profile} prompt_len={len(prompt)} client={client_key}",
                )
            response_payload = {
                "ok": True,
                "mode": "web_native",
                "task_type": task_type,
                "profile": profile,
                "effective_force_mode": str(getattr(router, "force_mode", "auto")),
                "recommendation": recommendation,
                "last_route": last_route,
                "reply": reply,
            }
            self._idempotency_set("assistant_query", idem_key, response_payload)
            return response_payload

        @self.app.get("/api/openclaw/report")
        async def openclaw_report():
            """Агрегированный health-report OpenClaw."""
            openclaw = self.deps.get("openclaw_client")
            if not openclaw:
                return {"available": False, "error": "openclaw_client_not_configured"}
            report = await openclaw.get_health_report()
            return {"available": True, "report": report}

        @self.app.get("/api/openclaw/deep-check")
        async def openclaw_deep_check():
            """Расширенная проверка OpenClaw (включая tool smoke и remediation)."""
            openclaw = self.deps.get("openclaw_client")
            if not openclaw:
                return {"available": False, "error": "openclaw_client_not_configured"}
            report = await openclaw.get_deep_health_report()
            return {"available": True, "report": report}

        @self.app.get("/api/openclaw/remediation-plan")
        async def openclaw_remediation_plan():
            """Пошаговый план исправления OpenClaw контуров."""
            openclaw = self.deps.get("openclaw_client")
            if not openclaw:
                return {"available": False, "error": "openclaw_client_not_configured"}
            report = await openclaw.get_remediation_plan()
            return {"available": True, "report": report}

        @self.app.get("/api/openclaw/browser-smoke")
        async def openclaw_browser_smoke(url: str = Query(default="https://example.com")):
            """Browser smoke check OpenClaw (endpoint/tool fallback)."""
            openclaw = self.deps.get("openclaw_client")
            if not openclaw:
                return {"available": False, "error": "openclaw_client_not_configured"}
            report = await openclaw.get_browser_smoke_report(url=url)
            return {"available": True, "report": report}

        async def _openclaw_cloud_diagnostics_impl(providers: str = ""):
            """Проверка cloud-провайдеров OpenClaw с классификацией ошибок ключей/API."""
            openclaw = self.deps.get("openclaw_client")
            if not openclaw:
                return {"available": False, "error": "openclaw_client_not_configured"}
            if not hasattr(openclaw, "get_cloud_provider_diagnostics"):
                return {"available": False, "error": "cloud_diagnostics_not_supported"}

            providers_list: list[str] | None = None
            raw = (providers or "").strip()
            if raw:
                providers_list = [item.strip().lower() for item in raw.split(",") if item.strip()]
                if not providers_list:
                    providers_list = None
            report = await openclaw.get_cloud_provider_diagnostics(providers=providers_list)
            return {"available": True, "report": report}

        @self.app.get("/api/openclaw/cloud")
        async def openclaw_cloud_diagnostics(providers: str = Query(default="")):
            """Канонический endpoint cloud-диагностики."""
            return await _openclaw_cloud_diagnostics_impl(providers=providers)

        @self.app.get("/api/openclaw/cloud/diagnostics")
        async def openclaw_cloud_diagnostics_legacy(providers: str = Query(default="")):
            """Совместимость со старым UI-клиентом (legacy alias)."""
            return await _openclaw_cloud_diagnostics_impl(providers=providers)

        @self.app.get("/api/openclaw/cloud/tier/state")
        async def openclaw_cloud_tier_state():
            """
            [R23/R25] Диагностика Cloud Tier State.

            Возвращает текущий активный tier (free/paid/default), статистику
            переключений, метрики (cloud_attempts_total и др.) и конфигурацию.
            Не содержит секретов — только счётчики событий.
            """
            try:
                openclaw = self.deps.get("openclaw_client")
                if not openclaw:
                    return build_ops_response(status="failed", error_code="openclaw_client_not_configured", summary="Openclaw client not configured")
                if not hasattr(openclaw, "get_tier_state_export"):
                    return build_ops_response(status="failed", error_code="tier_state_not_supported", summary="Tier state not supported")
                tier_state = openclaw.get_tier_state_export()
                return build_ops_response(status="ok", data={"tier_state": tier_state})
            except Exception as exc:
                return build_ops_response(status="failed", error_code="system_error", summary=str(exc))

        @self.app.post("/api/openclaw/cloud/tier/reset")
        async def openclaw_cloud_tier_reset(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            [R23/R25] Ручной сброс Cloud Tier на free.

            Требует X-Krab-Web-Key или token (WEB_API_KEY).
            Снимает sticky_paid флаг, не требует перезапуска бота.
            Возвращает: {ok, previous_tier, new_tier, reset_at}.
            """
            try:
                self._assert_write_access(x_krab_web_key, token)
            except HTTPException as exc:
                return build_ops_response(status="failed", error_code="forbidden", summary=exc.detail)

            try:
                openclaw = self.deps.get("openclaw_client")
                if not openclaw:
                    return build_ops_response(status="failed", error_code="openclaw_client_not_configured", summary="Openclaw client not configured")
                if not hasattr(openclaw, "reset_cloud_tier"):
                    return build_ops_response(status="failed", error_code="tier_reset_not_supported", summary="Tier reset not supported")
                
                result = await openclaw.reset_cloud_tier()
                return build_ops_response(status="ok", data={"result": result})
            except Exception as exc:
                return build_ops_response(status="failed", error_code="tier_reset_error", summary=str(exc))

        @self.app.get("/api/ops/runtime_snapshot")
        async def ops_runtime_snapshot():
            """[R25] Снапшот рантайма с маскировкой секретов."""
            try:
                data = get_observability_snapshot()
                return build_ops_response(status="ok", data=data)
            except Exception as exc:
                return build_ops_response(status="failed", error_code="snapshot_error", summary=str(exc))

        @self.app.get("/api/ops/metrics")
        async def ops_metrics():
            """[R25] Метрики в унифицированном формате."""
            try:
                return build_ops_response(status="ok", data=metrics.get_snapshot())
            except Exception as exc:
                return build_ops_response(status="failed", error_code="metrics_error", summary=str(exc))

        @self.app.get("/api/ops/timeline")
        async def ops_timeline():
            """[R25] Таймлайн событий с маскировкой."""
            try:
                data = {"events": get_observability_snapshot().get("timeline_tail", [])}
                return build_ops_response(status="ok", data=data)
            except Exception as exc:
                return build_ops_response(status="failed", error_code="timeline_error", summary=str(exc))

        def _run_openclaw_model_autoswitch(*, dry_run: bool) -> dict:
            """
            Запускает autoswitch-утилиту OpenClaw.
            dry_run=True: только диагностика, без изменения конфигурации.
            """
            project_root = Path(__file__).resolve().parents[2]
            script_path = project_root / "scripts" / "openclaw_model_autoswitch.py"
            if not script_path.exists():
                raise HTTPException(status_code=500, detail="openclaw_model_autoswitch_script_missing")

            python_bin = project_root / ".venv" / "bin" / "python"
            if not python_bin.exists():
                python_bin = Path(sys.executable or "python3")

            cmd = [str(python_bin), str(script_path)]
            if dry_run:
                cmd.append("--dry-run")

            proc = subprocess.run(
                cmd,
                cwd=str(project_root),
                capture_output=True,
                text=True,
                check=False,
            )
            stdout = (proc.stdout or "").strip()
            stderr = (proc.stderr or "").strip()
            if proc.returncode != 0:
                raise HTTPException(
                    status_code=500,
                    detail=f"openclaw_model_autoswitch_failed: {stderr or stdout or proc.returncode}",
                )

            lines = [line.strip() for line in stdout.splitlines() if line.strip()]
            if not lines:
                raise HTTPException(status_code=500, detail="openclaw_model_autoswitch_empty_output")
            try:
                payload = json.loads(lines[-1])
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"openclaw_model_autoswitch_invalid_json: {exc}",
                ) from exc
            if not isinstance(payload, dict):
                raise HTTPException(status_code=500, detail="openclaw_model_autoswitch_invalid_payload")
            return payload

        @self.app.get("/api/openclaw/model-autoswitch/status")
        async def openclaw_model_autoswitch_status():
            """Статус autoswitch без изменения runtime-конфига."""
            payload = _run_openclaw_model_autoswitch(dry_run=True)
            return {"ok": True, "autoswitch": payload}

        @self.app.post("/api/openclaw/model-autoswitch/apply")
        async def openclaw_model_autoswitch_apply(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Применяет autoswitch runtime-конфига OpenClaw (write endpoint)."""
            self._assert_write_access(x_krab_web_key, token)
            payload = _run_openclaw_model_autoswitch(dry_run=False)
            return {"ok": True, "autoswitch": payload}

        @self.app.get("/api/openclaw/control-compat/status")
        async def openclaw_control_compat_status():
            """
            [R22] Control Compatibility Diagnostics.

            Дает прозрачный ответ на вопрос: предупреждения OpenClaw Control UI
            (`Unsupported schema node`) — это UI-артефакт или реальный runtime-риск?

            Источники:
            - `openclaw channels status --probe` → runtime_channels_ok
            - `openclaw logs --tail 200` → control_schema_warnings (фильтрация по маркерам)

            Логика impact_level:
            - runtime ok + warnings → "ui_only"   (каналы работают, предупреждение косметическое)
            - runtime fail + warnings → "runtime_risk"  (нужна диагностика)
            - runtime ok, warnings нет → "none"
            """
            # --- Шаг 1: проверяем runtime каналов ---
            runtime_ok = False
            try:
                proc_channels = await asyncio.create_subprocess_exec(
                    "openclaw", "channels", "status", "--probe",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                try:
                    stdout_ch, _ = await asyncio.wait_for(proc_channels.communicate(), timeout=30.0)
                    runtime_ok = proc_channels.returncode == 0
                except asyncio.TimeoutError:
                    try:
                        proc_channels.terminate()
                    except ProcessLookupError:
                        pass
                    runtime_ok = False
            except Exception:
                runtime_ok = False

            # --- Шаг 2: получаем последние логи OpenClaw для поиска schema-маркеров ---
            schema_markers = {"unsupported schema node", "schema", "validation"}
            control_schema_warnings: list[str] = []
            try:
                proc_logs = await asyncio.create_subprocess_exec(
                    "openclaw", "logs", "--tail", "200",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                try:
                    stdout_logs, _ = await asyncio.wait_for(proc_logs.communicate(), timeout=10.0)
                    raw_logs = stdout_logs.decode("utf-8", errors="replace")
                    for line in raw_logs.splitlines():
                        line_lower = line.lower()
                        # Ищем строки, содержащие хотя бы один из маркеров схемы
                        if any(marker in line_lower for marker in schema_markers):
                            stripped = line.strip()
                            if stripped:
                                control_schema_warnings.append(stripped)
                except asyncio.TimeoutError:
                    try:
                        proc_logs.terminate()
                    except ProcessLookupError:
                        pass
                    # При таймауте логов — не считаем это runtime-риском
            except Exception:
                # CLI openclaw logs недоступен — просто нет данных для schema-анализа
                pass

            # --- Шаг 3: определяем impact_level и рекомендацию ---
            has_warnings = bool(control_schema_warnings)
            if runtime_ok and has_warnings:
                impact_level = "ui_only"
                recommended_action = (
                    "Предупреждения ограничены UI Control. Runtime каналов работает нормально. "
                    "Для редактирования затронутых полей используй Raw-режим в Control Dashboard."
                )
            elif not runtime_ok and has_warnings:
                impact_level = "runtime_risk"
                recommended_action = (
                    "Обнаружены schema-предупреждения И проблемы runtime. "
                    "Запусти: openclaw doctor --fix  или  ./openclaw_runtime_repair.command"
                )
            elif not runtime_ok:
                impact_level = "runtime_risk"
                recommended_action = (
                    "Runtime каналов недоступен. Schema-предупреждения не обнаружены. "
                    "Запусти: openclaw doctor --fix"
                )
            else:
                impact_level = "none"
                recommended_action = "Все каналы работают нормально. Предупреждений нет."

            return {
                "ok": runtime_ok or not has_warnings,
                "runtime_channels_ok": runtime_ok,
                "control_schema_warnings": control_schema_warnings,
                "impact_level": impact_level,
                "recommended_action": recommended_action,
            }

        @self.app.get("/api/openclaw/routing/effective")
        async def openclaw_routing_effective():
            """
            [R22] Routing Effective Source of Truth.

            Единый источник истины о текущем routing-решении Krab:
            откуда оно взялось, какой force_mode активен, почему идём в local или cloud.

            Читает только существующие атрибуты роутера — без внешних вызовов.
            Это позволяет: дебаггинг без отправки запросов в LM Studio/cloud,
            проверку конфигурации, понимание причин route-решений.
            """
            router = self.deps["router"]

            # --- Normalize force_mode ---
            force_mode_raw = str(getattr(router, "force_mode", "auto") or "auto")
            force_mode_eff = _normalize_force_mode(force_mode_raw)

            # --- Определяем default slot и модель ---
            cloud_slots: dict = {}
            raw_models = getattr(router, "models", {}) or {}
            if isinstance(raw_models, dict):
                cloud_slots = {str(k): str(v) for k, v in raw_models.items()}
            # Приоритет: "chat" → первый ключ → пусто
            default_slot = "chat" if "chat" in cloud_slots else (next(iter(cloud_slots), None) or "")
            default_model = cloud_slots.get(default_slot, "")

            # --- Cloud fallback включен если НЕ принудительный local ---
            cloud_fallback_enabled = force_mode_eff != "local"

            # --- Строим decision_notes из состояния роутера ---
            local_engine = str(getattr(router, "local_engine", "") or "")
            local_available = bool(getattr(router, "is_local_available", False))
            active_local_model = str(getattr(router, "active_local_model", "") or "")
            routing_policy = str(getattr(router, "routing_policy", "free_first_hybrid") or "free_first_hybrid")
            cloud_cap_reached = bool(getattr(router, "cloud_soft_cap_reached", False))

            decision_notes: list[str] = []
            if force_mode_raw in {"force_local", "local"}:
                decision_notes.append(
                    f"Принудительный local-режим активен — все запросы идут через {local_engine or 'local'}."
                )
            elif force_mode_raw in {"force_cloud", "cloud"}:
                decision_notes.append(
                    "Принудительный cloud-режим активен — локальный движок пропускается."
                )
            else:
                decision_notes.append(
                    f"Routing policy: {routing_policy} — auto-routing включен."
                )

            if local_available:
                decision_notes.append(
                    f"Локальный движок '{local_engine}' доступен."
                    + (f" Активная модель: '{active_local_model}'." if active_local_model else "")
                )
            else:
                decision_notes.append(
                    "Локальный движок недоступен — fallback только на cloud."
                )

            if cloud_cap_reached:
                decision_notes.append(
                    "Cloud soft-cap достигнут: приоритет переключен на локальный движок."
                )

            if not cloud_fallback_enabled:
                decision_notes.append(
                    "Cloud fallback ОТКЛЮЧЕН: force_local режим запрещает обращение к cloud."
                )

            return {
                "ok": True,
                "force_mode_requested": force_mode_raw,
                "force_mode_effective": force_mode_eff,
                "assistant_default_slot": default_slot,
                "assistant_default_model": default_model,
                "cloud_fallback_enabled": cloud_fallback_enabled,
                "decision_notes": decision_notes,
            }

        @self.app.get("/api/provisioning/templates")
        async def provisioning_templates(entity: str = Query(default="agent")):
            """Возвращает шаблоны для provisioning UI/API."""
            provisioning = self.deps.get("provisioning_service")
            if not provisioning:
                raise HTTPException(status_code=503, detail="provisioning_service_not_configured")
            return {"entity": entity, "templates": provisioning.list_templates(entity)}

        @self.app.get("/api/provisioning/drafts")
        async def provisioning_drafts(
            status: str | None = Query(default=None),
            limit: int = Query(default=20, ge=1, le=200),
        ):
            """Список provisioning draft'ов."""
            provisioning = self.deps.get("provisioning_service")
            if not provisioning:
                raise HTTPException(status_code=503, detail="provisioning_service_not_configured")
            return {"drafts": provisioning.list_drafts(limit=limit, status=status)}

        @self.app.post("/api/provisioning/drafts")
        async def provisioning_create_draft(
            payload: dict = Body(...),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            x_idempotency_key: str = Header(default="", alias="X-Idempotency-Key"),
            token: str = Query(default=""),
        ):
            """Создает provisioning draft (write endpoint)."""
            self._assert_write_access(x_krab_web_key, token)
            idem_key = (x_idempotency_key or "").strip()
            cached = self._idempotency_get("provisioning_create_draft", idem_key)
            if cached:
                return cached
            provisioning = self.deps.get("provisioning_service")
            if not provisioning:
                raise HTTPException(status_code=503, detail="provisioning_service_not_configured")

            try:
                draft = provisioning.create_draft(
                    entity_type=payload.get("entity_type", "agent"),
                    name=payload.get("name", ""),
                    role=payload.get("role", ""),
                    description=payload.get("description", ""),
                    requested_by=payload.get("requested_by", "web_api"),
                    settings=payload.get("settings", {}),
                )
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            black_box = self.deps.get("black_box")
            if black_box and hasattr(black_box, "log_event"):
                black_box.log_event(
                    "web_provisioning_draft_create",
                    f"entity={payload.get('entity_type', 'agent')} name={payload.get('name', '')}",
                )
            response_payload = {"ok": True, "draft": draft}
            self._idempotency_set("provisioning_create_draft", idem_key, response_payload)
            return response_payload

        @self.app.get("/api/provisioning/preview/{draft_id}")
        async def provisioning_preview(draft_id: str):
            """Показывает diff для draft перед apply."""
            provisioning = self.deps.get("provisioning_service")
            if not provisioning:
                raise HTTPException(status_code=503, detail="provisioning_service_not_configured")
            try:
                preview = provisioning.preview_diff(draft_id)
            except Exception as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return {"ok": True, "preview": preview}

        @self.app.post("/api/provisioning/apply/{draft_id}")
        async def provisioning_apply(
            draft_id: str,
            confirm: bool = Query(default=False),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            x_idempotency_key: str = Header(default="", alias="X-Idempotency-Key"),
            token: str = Query(default=""),
        ):
            """Применяет draft в catalog (write endpoint)."""
            self._assert_write_access(x_krab_web_key, token)
            idem_key = (x_idempotency_key or "").strip()
            cached = self._idempotency_get("provisioning_apply", f"{draft_id}:{idem_key}")
            if cached:
                return cached
            provisioning = self.deps.get("provisioning_service")
            if not provisioning:
                raise HTTPException(status_code=503, detail="provisioning_service_not_configured")
            try:
                result = provisioning.apply_draft(draft_id, confirmed=confirm)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            black_box = self.deps.get("black_box")
            if black_box and hasattr(black_box, "log_event"):
                black_box.log_event(
                    "web_provisioning_apply",
                    f"draft_id={draft_id} confirmed={confirm}",
                )
            response_payload = {"ok": True, "result": result}
            self._idempotency_set("provisioning_apply", f"{draft_id}:{idem_key}", response_payload)
            return response_payload

    async def start(self):
        """Запуск сервера в фоне."""
        if self._server_task and not self._server_task.done():
            return

        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="warning", loop="asyncio")
        # Prevent uvicorn from overriding signal handlers (managed by Pyrogram/Main)
        # Note: "server.serve()" will invoke "config.setup_event_loop()" which might still interfere unless configured correctly.
        # But setting explicit loop above helps.
        # Ideally we pass install_signal_handlers=False if supported by Config (it is not a direct arg usually, but passed to Server).
        # Actually Config() has no install_signal_handlers arg. It's on Server.run() usually?
        # No, it IS an argument to Config __init__ in newer versions, or handled via setup.
        # Let's check typical usage.
        # Standard Uvicorn Config has NO install_signal_handlers arg.
        # But uvicorn.Server(config).serve() installs them unless overridden.
        # We can try to prevent it by subclassing or checking if we can pass a flag.
        # Actually Config DOES have it in recent versions? Let's assume standard 0.20+ has it?
        # Let's try passing it. If it fails, we catch TypeError.
        try:
            config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="warning", loop="asyncio")
            # We must monkeypatch to prevent signal install? Or just hope it works?
            # Actually simplest way is to NOT use Server.serve() directly if we can avoid signal handlers?
            # But serve() calls install_signal_handlers().
            # Let's override the install_signal_handlers method of the server instance!
            self._server = uvicorn.Server(config)
            self._server.install_signal_handlers = lambda: None
        except Exception as e:
            logger.warning(f"Could not disable uvicorn signal handlers: {e}")
            self._server = uvicorn.Server(config)

        logger.info(f"🌐 Web App starting at {self._public_base_url()}")
        self._server_task = asyncio.create_task(self._server.serve())

    async def stop(self):
        """Аккуратно останавливает uvicorn сервер."""
        if self._server:
            self._server.should_exit = True
        if self._server_task:
            await asyncio.wait([self._server_task], timeout=3)
