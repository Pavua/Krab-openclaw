"""Wave 47-A: тесты для extended fallback chain iteration в OpenClawClient.

Production incident (2026-05-10 00:12, msg 16863):
  codex-cli/gpt-5.5 hit weekly quota → switched к gemini-3-pro-preview ✓
  gemini-3-pro-preview returned HTTP 500 (provider_timeout) → BUG: показал
  generic "Облачный сервис недоступен" вместо advance к google-vertex/...
  в той же chain (7 моделей).

Эти тесты verify что `_pick_cloud_retry_model` теперь принимает `exclude`
set и что loop iteration tracks tried models.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.openclaw_client import OpenClawClient

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_model_manager(local_set: set[str] | None = None) -> MagicMock:
    """MagicMock для ModelManager: только методы, которые трогает _pick_cloud_retry_model."""
    local = local_set or set()
    mm = MagicMock()
    mm.is_local_model = MagicMock(side_effect=lambda m: str(m or "").strip() in local)
    mm.get_best_cloud_model = AsyncMock(return_value="")
    return mm


@pytest.fixture
def client() -> OpenClawClient:
    """Минимальный OpenClawClient — конструктор инициализирует много state,
    но для этих unit-тестов мы вызываем только pure async методы."""
    return OpenClawClient()


# ---------------------------------------------------------------------------
# _pick_cloud_retry_model: exclude param
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pick_returns_first_chain_candidate(client: OpenClawClient) -> None:
    """Базовый случай: exclude={current}, return первый non-current кандидат."""
    chain = ["google/gemini-3-pro-preview", "google-vertex/gemini-3-pro-preview"]
    with patch(
        "src.openclaw_client.get_runtime_primary_model", return_value="codex-cli/gpt-5.5"
    ), patch("src.openclaw_client.get_runtime_fallback_models", return_value=chain):
        mm = _make_model_manager()
        picked = await client._pick_cloud_retry_model(
            model_manager=mm,
            current_model="codex-cli/gpt-5.5",
            has_photo=False,
        )
        # Primary == current → skip; первый fallback должен победить
        assert picked == "google/gemini-3-pro-preview"


@pytest.mark.asyncio
async def test_pick_skips_excluded_models(client: OpenClawClient) -> None:
    """Wave 47-A core: exclude содержит уже-tried модели → return следующий в chain."""
    chain = [
        "google/gemini-3-pro-preview",
        "google-vertex/gemini-3-pro-preview",
        "google-vertex/gemini-flash-latest",
    ]
    with patch(
        "src.openclaw_client.get_runtime_primary_model", return_value="codex-cli/gpt-5.5"
    ), patch("src.openclaw_client.get_runtime_fallback_models", return_value=chain):
        mm = _make_model_manager()
        # codex (current) + gemini уже tried → expect vertex
        picked = await client._pick_cloud_retry_model(
            model_manager=mm,
            current_model="codex-cli/gpt-5.5",
            has_photo=False,
            exclude={"google/gemini-3-pro-preview"},
        )
        assert picked == "google-vertex/gemini-3-pro-preview"


@pytest.mark.asyncio
async def test_pick_returns_empty_when_chain_exhausted(client: OpenClawClient) -> None:
    """Все модели в chain в exclude → return ''."""
    chain = ["google/gemini-3-pro-preview", "google-vertex/gemini-3-pro-preview"]
    with patch(
        "src.openclaw_client.get_runtime_primary_model", return_value="codex-cli/gpt-5.5"
    ), patch("src.openclaw_client.get_runtime_fallback_models", return_value=chain):
        mm = _make_model_manager()
        picked = await client._pick_cloud_retry_model(
            model_manager=mm,
            current_model="codex-cli/gpt-5.5",
            has_photo=False,
            exclude=set(chain),
        )
        # get_best_cloud_model AsyncMock возвращает "" → итого ""
        assert picked == ""


@pytest.mark.asyncio
async def test_pick_advances_through_full_production_chain(client: OpenClawClient) -> None:
    """Симулируем production chain 7 моделей: каждая итерация добавляет в exclude
    и должна возвращать НОВЫЙ кандидат пока цепочка не выработана."""
    primary = "codex-cli/gpt-5.5"
    chain = [
        "google/gemini-3-pro-preview",
        "google-vertex/gemini-3-pro-preview",
        "google-vertex/gemini-flash-latest",
        "google-gemini-cli/gemini-2.5-pro",
        "google/gemini-3-flash-preview",
        "google/gemini-2.5-flash",
    ]
    with patch(
        "src.openclaw_client.get_runtime_primary_model", return_value=primary
    ), patch("src.openclaw_client.get_runtime_fallback_models", return_value=chain):
        mm = _make_model_manager()
        # В реальном loop primary никогда не возвращается из chain advance —
        # после первой неудачи его модель добавляется в excluded.
        excluded: set[str] = {primary}
        seen: list[str] = []
        current = primary
        for _ in range(len(chain) + 1):
            picked = await client._pick_cloud_retry_model(
                model_manager=mm,
                current_model=current,
                has_photo=False,
                exclude=excluded,
            )
            if not picked:
                break
            seen.append(picked)
            excluded.add(picked)
            current = picked
        # Должны увидеть все 6 моделей из chain (primary == current — skip).
        assert seen == chain
        # Финальная итерация — exhausted → ""
        final = await client._pick_cloud_retry_model(
            model_manager=mm,
            current_model=current,
            has_photo=False,
            exclude=excluded,
        )
        assert final == ""


@pytest.mark.asyncio
async def test_pick_skips_local_models_in_chain(client: OpenClawClient) -> None:
    """Local-модели в exclude pass; _is_cloud_candidate_usable отсекает локальные."""
    chain = ["lm-studio/local-model", "google/gemini-3-pro-preview"]
    with patch(
        "src.openclaw_client.get_runtime_primary_model", return_value="codex-cli/gpt-5.5"
    ), patch("src.openclaw_client.get_runtime_fallback_models", return_value=chain):
        mm = _make_model_manager(local_set={"lm-studio/local-model"})
        picked = await client._pick_cloud_retry_model(
            model_manager=mm,
            current_model="codex-cli/gpt-5.5",
            has_photo=False,
        )
        # local-model отфильтрован → gemini
        assert picked == "google/gemini-3-pro-preview"


@pytest.mark.asyncio
async def test_pick_exclude_default_none_works(client: OpenClawClient) -> None:
    """Default exclude=None не должен ломать legacy callers."""
    chain = ["google/gemini-3-pro-preview"]
    with patch(
        "src.openclaw_client.get_runtime_primary_model", return_value=""
    ), patch("src.openclaw_client.get_runtime_fallback_models", return_value=chain):
        mm = _make_model_manager()
        picked = await client._pick_cloud_retry_model(
            model_manager=mm,
            current_model="other",
            has_photo=False,
            # exclude omitted
        )
        assert picked == "google/gemini-3-pro-preview"


@pytest.mark.asyncio
async def test_pick_exclude_does_not_block_non_excluded(client: OpenClawClient) -> None:
    """exclude={"X"} не должен блокировать кандидата Y."""
    chain = ["A", "B", "C"]
    with patch(
        "src.openclaw_client.get_runtime_primary_model", return_value=""
    ), patch("src.openclaw_client.get_runtime_fallback_models", return_value=chain):
        mm = _make_model_manager()
        picked = await client._pick_cloud_retry_model(
            model_manager=mm,
            current_model="X",
            has_photo=False,
            exclude={"A"},
        )
        # A в exclude, X — current; B — первый свободный
        assert picked == "B"


# ---------------------------------------------------------------------------
# Smoke: chain_advance loop variables инициализированы
# ---------------------------------------------------------------------------


def test_loop_advance_constants_present() -> None:
    """Проверяем что в openclaw_client.py есть Wave 47-A константы — guard
    против случайного отката (unit для loop поведения требует mock streaming
    layer что слишком обширно для этого slice)."""
    from pathlib import Path

    src = Path("src/openclaw_client.py").read_text(encoding="utf-8")
    # Wave 47-A markers
    assert "chain_models_tried: set[str] = set()" in src
    assert "chain_advance_count = 0" in src
    assert "openclaw_chain_advancing" in src
    # Loop bumped с 4 до 8
    assert "for attempt in range(8):" in src


def test_chain_advance_message_includes_count() -> None:
    """Когда chain исчерпан, error message должен включать число попыток."""
    from pathlib import Path

    src = Path("src/openclaw_client.py").read_text(encoding="utf-8")
    assert "попробовал" in src and "моделей в fallback chain" in src
