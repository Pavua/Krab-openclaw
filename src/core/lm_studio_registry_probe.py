# -*- coding: utf-8 -*-
"""Wave 133: LM Studio registry probe.

Фоновый asyncio loop опрашивает LM Studio REST `/v1/models`, парсит
ответ и записывает gauges в `src.core.metrics.lm_registry`:

* `krab_lm_models_loaded_count` — число моделей в ответе;
* `krab_lm_estimated_ram_gb` — оценка суммарного RAM-footprint.

Wave 65-G выгружает idle модели, Wave 86 переключает fallback при
high pressure — но не было ответа на вопрос "что прямо сейчас
резидентно в RAM?". Probe закрывает этот gap.

Env:
    KRAB_LM_REGISTRY_PROBE_ENABLED       (default ON)
    KRAB_LM_REGISTRY_PROBE_INTERVAL_SEC  (default 60)
    LM_STUDIO_URL                        (default http://127.0.0.1:1234)

Если LM Studio API возвращает поле `loaded_context_length` или явный
`size_bytes` (зависит от версии) — используем его. Иначе оценка по
имени модели: суффиксы `4b`, `8b`, `13b`, `70b` → 4 / 8 / 13 / 40 ГБ
(70b в Q4 quantization ≈ 40 ГБ unified memory).
"""

from __future__ import annotations

import asyncio
import os
import re
import traceback
from typing import Any, Callable, Iterable

import structlog

from src.core.metrics.lm_registry import set_lm_registry_state

logger = structlog.get_logger(__name__)

_DEFAULT_INTERVAL_SEC = 60
_DEFAULT_URL = "http://127.0.0.1:1234"

# Эвристика size-by-name: матчим первый суффикс вида `<num>b` (case-insensitive).
# 70B Q4 ≈ 40 ГБ; FP16 — другая история, но для LM Studio default Q4_K_M.
_SIZE_BY_BILLIONS_GB: dict[int, float] = {
    1: 1.0,
    2: 2.0,
    3: 2.5,
    4: 4.0,
    7: 5.0,
    8: 8.0,
    13: 9.0,
    20: 14.0,
    27: 18.0,
    30: 20.0,
    34: 22.0,
    40: 26.0,
    70: 40.0,
    120: 70.0,
}

_BILLIONS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[bB](?!yte)")


def _estimate_model_ram_gb(model: dict[str, Any]) -> float:
    """Оценить RAM-footprint одной модели (ГБ).

    1) Прямые поля API: ``size``, ``size_bytes`` — конвертируем в ГБ.
    2) Fallback: парсим billions из ``id``/``model_id``/``name``.
    3) Default 4.0 — консервативная оценка для unknown.
    """
    # 1) Прямой размер от API
    raw_size = model.get("size_bytes") or model.get("size")
    if isinstance(raw_size, (int, float)) and raw_size > 0:
        # Если значение похоже на байты (>= 10MB) — конвертируем; иначе считаем что
        # это уже ГБ (некоторые версии LM Studio отдают ГБ как float).
        if raw_size >= 10_000_000:
            return float(raw_size) / (1024**3)
        return float(raw_size)

    # 2) Парсинг по имени
    name = str(model.get("id") or model.get("model_id") or model.get("name") or "")
    match = _BILLIONS_RE.search(name)
    if match:
        try:
            billions = float(match.group(1))
        except ValueError:
            billions = 0.0
        if billions > 0:
            # Ищем ближайший known bucket
            rounded = int(round(billions))
            if rounded in _SIZE_BY_BILLIONS_GB:
                return _SIZE_BY_BILLIONS_GB[rounded]
            # Линейная экстраполяция: ~0.6 ГБ на 1B params (Q4)
            return max(1.0, billions * 0.6)

    # 3) Default
    return 4.0


def parse_models_payload(payload: Any) -> list[dict[str, Any]]:
    """Извлечь список моделей из payload `/v1/models`.

    OpenAI-совместимый формат: ``{"data": [{"id": "..."}], "object": "list"}``.
    Защищаемся от nested / отсутствующего ``data``.
    """
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def compute_registry_snapshot(models: Iterable[dict[str, Any]]) -> tuple[int, float]:
    """Свести модели к (count, sum_estimated_ram_gb)."""
    total_gb = 0.0
    count = 0
    for model in models:
        count += 1
        total_gb += _estimate_model_ram_gb(model)
    return count, round(total_gb, 2)


def _is_enabled() -> bool:
    raw = os.environ.get("KRAB_LM_REGISTRY_PROBE_ENABLED", "1").strip().lower()
    return raw in ("1", "true", "yes")


def _get_interval_sec() -> int:
    raw = os.environ.get("KRAB_LM_REGISTRY_PROBE_INTERVAL_SEC", str(_DEFAULT_INTERVAL_SEC))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_INTERVAL_SEC
    return max(5, value)


def _get_base_url() -> str:
    return os.environ.get("LM_STUDIO_URL", _DEFAULT_URL).rstrip("/")


async def _fetch_models(base_url: str) -> list[dict[str, Any]]:
    """HTTP GET `/v1/models`. Возвращает [] при любой ошибке."""
    try:
        import httpx  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    url = f"{base_url}/v1/models"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return []
            return parse_models_payload(resp.json())
    except Exception:  # noqa: BLE001
        return []


class LmStudioRegistryProbe:
    """Фоновый probe: каждые N секунд опрашивает LM Studio и пишет gauges."""

    def __init__(
        self,
        *,
        fetch_fn: Callable[[str], Any] | None = None,
        interval_fn: Callable[[], int] | None = None,
        url_fn: Callable[[], str] | None = None,
    ) -> None:
        # Инъекции для тестов
        self._fetch_fn = fetch_fn or _fetch_models
        self._interval_fn = interval_fn or _get_interval_sec
        self._url_fn = url_fn or _get_base_url
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="lm_studio_registry_probe")

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def probe_once(self) -> tuple[int, float]:
        """Одна итерация probe — вынесена для тестируемости."""
        url = self._url_fn()
        models = await self._fetch_fn(url)
        count, total_gb = compute_registry_snapshot(models)
        set_lm_registry_state(loaded_count=count, estimated_ram_gb=total_gb)
        return count, total_gb

    async def _loop(self) -> None:
        interval = self._interval_fn()
        logger.info("lm_studio_registry_probe_started", interval_sec=interval)
        while True:
            try:
                if _is_enabled():
                    count, total_gb = await self.probe_once()
                    logger.debug(
                        "lm_studio_registry_probe_tick",
                        loaded_count=count,
                        estimated_ram_gb=total_gb,
                    )
                await asyncio.sleep(self._interval_fn())
            except asyncio.CancelledError:
                logger.info("lm_studio_registry_probe_stopped")
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "lm_studio_registry_probe_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    traceback=traceback.format_exc(),
                )
                # Не уходим в tight-loop при перманентной ошибке
                await asyncio.sleep(self._interval_fn())


# Singleton — bootstrap из userbot_bridge
_probe: LmStudioRegistryProbe | None = None


def configure() -> LmStudioRegistryProbe:
    """Инициализирует и запускает singleton probe."""
    global _probe
    if _probe is not None:
        _probe.stop()
    _probe = LmStudioRegistryProbe()
    _probe.start()
    return _probe


def get_probe() -> LmStudioRegistryProbe | None:
    return _probe
