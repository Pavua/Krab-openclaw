# -*- coding: utf-8 -*-
"""
Image Generation Manager.

Отвечает за генерацию изображений в двух каналах:
- локально через ComfyUI (FLUX workflow);
- облачно через Gemini Image API.

Менеджер также хранит каталог доступных моделей с ориентировочной стоимостью,
чтобы команды Telegram могли показывать пользователю цену и источник генерации.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp
import structlog

logger = structlog.get_logger("ImageManager")


class ImageManager:
    """Управляет локальной/облачной генерацией изображений."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.comfy_url = str(self._cfg(config, "COMFY_URL", "http://localhost:8188")).rstrip("/")
        self.gemini_key = self._cfg(config, "GEMINI_API_KEY", os.getenv("GEMINI_API_KEY"))

        self.output_dir = Path(self._cfg(config, "IMAGE_OUTPUT_DIR", "artifacts/downloads"))
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.comfy_timeout_sec = int(self._cfg(config, "COMFY_TIMEOUT_SECONDS", 180))
        self.comfy_poll_interval = float(self._cfg(config, "COMFY_POLL_INTERVAL_SECONDS", 1.0))
        self.prefer_local = str(self._cfg(config, "IMAGE_PREFER_LOCAL", "1")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        self.model_specs = self._build_model_specs(config)
        self.default_local_alias = str(
            self._cfg(config, "IMAGE_DEFAULT_LOCAL_MODEL", "local:flux-dev")
        ).strip()
        self.default_cloud_alias = str(
            self._cfg(config, "IMAGE_DEFAULT_CLOUD_MODEL", "cloud:imagen3")
        ).strip()
        self.last_result: Dict[str, Any] = {}

    @staticmethod
    def _cfg(config: Dict[str, Any], key: str, default: Any = None) -> Any:
        """Берет значение из config, иначе из env, иначе default."""
        if key in config and config.get(key) not in {None, ""}:
            return config.get(key)
        env_value = os.getenv(key)
        if env_value not in {None, ""}:
            return env_value
        return default

    def _build_model_specs(self, config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Собирает каталог моделей из конфигурации."""
        workflow_default = str(
            self._cfg(config, "COMFY_WORKFLOW_PATH", "ComfyUI/workflows/flux_dev_no_censorship.json")
        ).strip()
        workflow_alt = str(
            self._cfg(config, "COMFY_ALT_WORKFLOW_PATH", "ComfyUI/workflows/flux_uncensored_v2.json")
        ).strip()

        return {
            "local:flux-dev": {
                "alias": "local:flux-dev",
                "title": "FLUX Dev (ComfyUI)",
                "channel": "local",
                "provider": "comfyui",
                "model_id": "flux-dev-local",
                "workflow": workflow_default,
                "cost_per_image_usd": 0.0,
                "note": "Локальная генерация через ComfyUI",
            },
            "local:flux-uncensored": {
                "alias": "local:flux-uncensored",
                "title": "FLUX Uncensored (ComfyUI)",
                "channel": "local",
                "provider": "comfyui",
                "model_id": "flux-uncensored-local",
                "workflow": workflow_alt,
                "cost_per_image_usd": 0.0,
                "note": "Локальная генерация через ComfyUI",
            },
            "cloud:imagen3": {
                "alias": "cloud:imagen3",
                "title": "Imagen 3",
                "channel": "cloud",
                "provider": "gemini",
                "model_id": str(self._cfg(config, "IMAGE_MODEL_IMAGEN3", "imagen-3.0-generate-001")).strip(),
                "cost_per_image_usd": float(self._cfg(config, "IMAGE_COST_IMAGEN3_USD", 0.04)),
                "note": "Gemini Image API",
            },
            "cloud:nano-banana": {
                "alias": "cloud:nano-banana",
                "title": "Nano Banana",
                "channel": "cloud",
                "provider": "gemini",
                "model_id": str(self._cfg(config, "IMAGE_MODEL_NANO_BANANA", "")).strip(),
                "cost_per_image_usd": float(self._cfg(config, "IMAGE_COST_NANO_BANANA_USD", 0.03)),
                "note": "Требуется валидный model id в IMAGE_MODEL_NANO_BANANA",
            },
            "cloud:nano-banana-pro": {
                "alias": "cloud:nano-banana-pro",
                "title": "Nano Banana Pro",
                "channel": "cloud",
                "provider": "gemini",
                "model_id": str(self._cfg(config, "IMAGE_MODEL_NANO_BANANA_PRO", "")).strip(),
                "cost_per_image_usd": float(self._cfg(config, "IMAGE_COST_NANO_BANANA_PRO_USD", 0.06)),
                "note": "Требуется валидный model id в IMAGE_MODEL_NANO_BANANA_PRO",
            },
        }

    def get_defaults(self) -> dict[str, Any]:
        """Возвращает текущие дефолты image-генерации."""
        return {
            "default_local_alias": self.default_local_alias,
            "default_cloud_alias": self.default_cloud_alias,
            "prefer_local": bool(self.prefer_local),
        }

    def set_default_alias(self, channel: str, alias: str) -> dict[str, Any]:
        """Закрепляет дефолтную модель для local/cloud канала."""
        normalized_channel = str(channel or "").strip().lower()
        normalized_alias = str(alias or "").strip()
        spec = self.model_specs.get(normalized_alias)
        if not spec:
            return {"ok": False, "error": f"unknown_alias:{normalized_alias}"}
        if spec.get("channel") != normalized_channel:
            return {
                "ok": False,
                "error": f"alias_channel_mismatch:{normalized_alias}:{spec.get('channel')}",
            }
        if normalized_channel == "local":
            self.default_local_alias = normalized_alias
        elif normalized_channel == "cloud":
            self.default_cloud_alias = normalized_alias
        else:
            return {"ok": False, "error": f"unknown_channel:{normalized_channel}"}
        return {"ok": True, **self.get_defaults()}

    def set_prefer_mode(self, mode: str) -> dict[str, Any]:
        """Устанавливает приоритет канала генерации."""
        normalized = str(mode or "").strip().lower()
        if normalized in {"local", "prefer_local"}:
            self.prefer_local = True
        elif normalized in {"cloud", "prefer_cloud"}:
            self.prefer_local = False
        elif normalized == "auto":
            # В авто возвращаемся к дефолтной стратегии local-first.
            self.prefer_local = True
        else:
            return {"ok": False, "error": f"unknown_mode:{normalized}"}
        return {"ok": True, **self.get_defaults()}

    async def list_models(self) -> list[dict[str, Any]]:
        """Возвращает каталог моделей с флагами доступности и ориентировочной ценой."""
        comfy_online = await self._is_comfy_online()
        rows: list[dict[str, Any]] = []
        for alias, spec in self.model_specs.items():
            available = True
            reason = ""
            if spec["channel"] == "local":
                workflow_path = Path(spec.get("workflow") or "")
                if not comfy_online:
                    available = False
                    reason = "ComfyUI offline"
                elif not workflow_path.exists():
                    available = False
                    reason = f"workflow missing: {workflow_path}"
            else:
                if not self.gemini_key:
                    available = False
                    reason = "GEMINI_API_KEY missing"
                elif not spec.get("model_id"):
                    available = False
                    reason = "model_id not configured"

            rows.append(
                {
                    "alias": alias,
                    "title": spec.get("title", alias),
                    "provider": spec.get("provider", "unknown"),
                    "channel": spec.get("channel", "unknown"),
                    "model_id": spec.get("model_id", ""),
                    "cost_per_image_usd": spec.get("cost_per_image_usd"),
                    "available": available,
                    "reason": reason,
                    "note": spec.get("note", ""),
                }
            )
        return rows

    def estimate_cost(self, alias: str, images: int = 1) -> dict[str, Any]:
        """Возвращает ориентировочную стоимость для выбранной модели."""
        spec = self.model_specs.get(alias)
        if not spec:
            return {
                "ok": False,
                "error": f"unknown_model_alias:{alias}",
            }
        unit = spec.get("cost_per_image_usd")
        if unit is None:
            return {
                "ok": True,
                "alias": alias,
                "unit_cost_usd": None,
                "total_cost_usd": None,
                "images": images,
            }
        return {
            "ok": True,
            "alias": alias,
            "unit_cost_usd": round(float(unit), 6),
            "total_cost_usd": round(float(unit) * max(1, int(images)), 6),
            "images": max(1, int(images)),
        }

    async def generate(self, prompt: str, aspect_ratio: str = "1:1") -> Optional[str]:
        """Совместимость со старым интерфейсом: возвращает только путь к картинке."""
        result = await self.generate_with_meta(prompt=prompt, aspect_ratio=aspect_ratio)
        return str(result.get("path")) if result.get("ok") else None

    async def generate_with_meta(
        self,
        prompt: str,
        aspect_ratio: str = "1:1",
        model_alias: Optional[str] = None,
        prefer_local: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Генерирует изображение и возвращает расширенный результат."""
        prompt = (prompt or "").strip()
        if not prompt:
            return {"ok": False, "error": "prompt_empty"}

        effective_prefer_local = self.prefer_local if prefer_local is None else bool(prefer_local)

        if model_alias:
            aliases = [model_alias]
        else:
            if effective_prefer_local:
                aliases = [self.default_local_alias, self.default_cloud_alias]
            else:
                aliases = [self.default_cloud_alias, self.default_local_alias]

        errors: list[str] = []
        for alias in aliases:
            spec = self.model_specs.get(alias)
            if not spec:
                errors.append(f"unknown_alias:{alias}")
                continue
            result = await self._generate_by_spec(spec=spec, prompt=prompt, aspect_ratio=aspect_ratio)
            if result.get("ok"):
                self.last_result = result
                return result
            errors.append(f"{alias}:{result.get('error', 'failed')}")

        failure = {
            "ok": False,
            "error": "all_generators_failed",
            "details": errors,
        }
        self.last_result = failure
        return failure

    async def _generate_by_spec(self, spec: Dict[str, Any], prompt: str, aspect_ratio: str) -> dict[str, Any]:
        """Делегирует генерацию в нужный backend по спецификации модели."""
        alias = spec.get("alias", "unknown")
        if spec.get("channel") == "local":
            local = await self._generate_local_comfy(spec=spec, prompt=prompt)
            if local.get("ok"):
                local.update(
                    {
                        "model_alias": alias,
                        "provider": spec.get("provider"),
                        "channel": "local",
                        "model_id": spec.get("model_id", ""),
                        "cost_estimate_usd": 0.0,
                    }
                )
            return local

        cloud = await self._generate_cloud_gemini(spec=spec, prompt=prompt, aspect_ratio=aspect_ratio)
        if cloud.get("ok"):
            unit_cost = spec.get("cost_per_image_usd")
            cloud.update(
                {
                    "model_alias": alias,
                    "provider": spec.get("provider"),
                    "channel": "cloud",
                    "model_id": spec.get("model_id", ""),
                    "cost_estimate_usd": round(float(unit_cost), 6) if unit_cost is not None else None,
                }
            )
        return cloud

    async def _is_comfy_online(self) -> bool:
        """Проверяет доступность ComfyUI."""
        try:
            timeout = aiohttp.ClientTimeout(total=2)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.comfy_url}/system_stats") as response:
                    return response.status == 200
        except Exception:
            return False

    async def _generate_local_comfy(self, spec: Dict[str, Any], prompt: str) -> dict[str, Any]:
        """Генерация через ComfyUI API (/prompt -> /history -> /view)."""
        if not await self._is_comfy_online():
            return {"ok": False, "error": "comfyui_offline"}

        workflow_path = Path(spec.get("workflow") or "")
        if not workflow_path.exists():
            return {"ok": False, "error": f"workflow_not_found:{workflow_path}"}

        try:
            workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "error": f"workflow_parse_error:{exc}"}

        patched_prompt = self._patch_workflow_prompt(workflow, prompt)

        client_id = uuid.uuid4().hex
        timeout = aiohttp.ClientTimeout(total=self.comfy_timeout_sec)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self.comfy_url}/prompt",
                    json={"prompt": patched_prompt, "client_id": client_id},
                ) as resp:
                    if resp.status != 200:
                        return {"ok": False, "error": f"comfy_submit_failed:{resp.status}:{await resp.text()}"}
                    payload = await resp.json(content_type=None)
                    prompt_id = payload.get("prompt_id")
                    if not prompt_id:
                        return {"ok": False, "error": "comfy_prompt_id_missing"}

                started = time.monotonic()
                image_meta = None
                while (time.monotonic() - started) < self.comfy_timeout_sec:
                    async with session.get(f"{self.comfy_url}/history/{prompt_id}") as hist_resp:
                        if hist_resp.status == 200:
                            history_payload = await hist_resp.json(content_type=None)
                            image_meta = self._extract_image_meta_from_history(history_payload, prompt_id)
                            if image_meta:
                                break
                    await asyncio.sleep(self.comfy_poll_interval)

                if not image_meta:
                    return {"ok": False, "error": "comfy_timeout_waiting_image"}

                query = (
                    f"filename={image_meta['filename']}"
                    f"&subfolder={image_meta.get('subfolder', '')}"
                    f"&type={image_meta.get('type', 'output')}"
                )
                async with session.get(f"{self.comfy_url}/view?{query}") as view_resp:
                    if view_resp.status != 200:
                        return {"ok": False, "error": f"comfy_view_failed:{view_resp.status}"}
                    image_bytes = await view_resp.read()
        except Exception as exc:
            return {"ok": False, "error": f"comfy_exception:{exc}"}

        file_path = self.output_dir / f"comfy_{uuid.uuid4().hex[:10]}.png"
        file_path.write_bytes(image_bytes)
        logger.info("Локальная генерация через ComfyUI завершена", path=str(file_path), workflow=str(workflow_path))
        return {"ok": True, "path": str(file_path)}

    def _patch_workflow_prompt(self, workflow: Dict[str, Any], prompt: str) -> Dict[str, Any]:
        """Подставляет пользовательский prompt в CLIPTextEncode узлы workflow."""
        patched = copy.deepcopy(workflow)
        if not isinstance(patched, dict):
            return patched

        for _, node in patched.items():
            if not isinstance(node, dict):
                continue
            class_type = str(node.get("class_type", ""))
            if not class_type.lower().startswith("cliptextencode"):
                continue
            inputs = node.get("inputs")
            if not isinstance(inputs, dict) or "text" not in inputs:
                continue
            title = str(node.get("title", "")).lower()
            is_negative = "negative" in title or "neg" in title
            if not is_negative:
                inputs["text"] = prompt
        return patched

    def _extract_image_meta_from_history(self, history_payload: Dict[str, Any], prompt_id: str) -> Optional[Dict[str, str]]:
        """Извлекает метаданные итоговой картинки из /history ответа ComfyUI."""
        if not isinstance(history_payload, dict):
            return None
        prompt_entry = history_payload.get(prompt_id)
        if not isinstance(prompt_entry, dict):
            return None

        outputs = prompt_entry.get("outputs", {})
        if not isinstance(outputs, dict):
            return None

        for output in outputs.values():
            if not isinstance(output, dict):
                continue
            images = output.get("images", [])
            if not isinstance(images, list) or not images:
                continue
            first = images[0]
            if not isinstance(first, dict) or not first.get("filename"):
                continue
            return {
                "filename": str(first.get("filename")),
                "subfolder": str(first.get("subfolder", "")),
                "type": str(first.get("type", "output")),
            }
        return None

    async def _generate_cloud_gemini(self, spec: Dict[str, Any], prompt: str, aspect_ratio: str) -> dict[str, Any]:
        """Генерация через Gemini Image API."""
        model_id = str(spec.get("model_id") or "").strip()
        if not self.gemini_key:
            return {"ok": False, "error": "gemini_key_missing"}
        if not model_id:
            return {"ok": False, "error": "gemini_model_id_missing"}

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.gemini_key)
            cfg_kwargs: Dict[str, Any] = {
                "number_of_images": 1,
                "include_rai_reasoning": True,
            }
            if aspect_ratio:
                cfg_kwargs["aspect_ratio"] = aspect_ratio

            response = await asyncio.to_thread(
                client.models.generate_image,
                model=model_id,
                prompt=prompt,
                config=types.GenerateImageConfig(**cfg_kwargs),
            )
            if not response or not getattr(response, "generated_images", None):
                return {"ok": False, "error": "gemini_empty_response"}

            image_bytes = response.generated_images[0].image.image_bytes
            file_path = self.output_dir / f"img_{uuid.uuid4().hex[:10]}.png"
            file_path.write_bytes(image_bytes)
            logger.info("Облачная генерация изображения завершена", model=model_id, path=str(file_path))
            return {"ok": True, "path": str(file_path)}
        except Exception as exc:
            return {"ok": False, "error": f"gemini_generate_failed:{exc}"}
