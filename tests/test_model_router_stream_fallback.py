# -*- coding: utf-8 -*-
"""
Тесты route_stream fallback-политики (Phase 17.8).

Покрытие:
1. При ошибке local stream роутер отдает cloud fallback.
2. При отключенном fallback возвращает понятную локальную ошибку.
3. При успешном local stream fallback в облако не вызывается.
"""

import asyncio
from pathlib import Path

import pytest

from src.core.model_manager import ModelRouter
from src.core.stream_client import StreamFailure


def _router(tmp_path: Path, fallback_enabled: bool = True) -> ModelRouter:
    return ModelRouter(
        config={
            "MODEL_ROUTING_MEMORY_PATH": str(tmp_path / "routing_memory.json"),
            "MODEL_USAGE_REPORT_PATH": str(tmp_path / "usage_report.json"),
            "MODEL_OPS_STATE_PATH": str(tmp_path / "ops_state.json"),
            "MODEL_FEEDBACK_PATH": str(tmp_path / "feedback.json"),
            "LOCAL_STREAM_FALLBACK_TO_CLOUD": "1" if fallback_enabled else "0",
        }
    )


def test_cloud_error_message_detection_handles_llm_error_payload(tmp_path: Path) -> None:
    router = _router(tmp_path, fallback_enabled=True)
    payload = (
        'LLM error: {"error":{"code":404,"message":"models/gemini-2.0-flash-exp is not found",'
        '"status":"NOT_FOUND"}}'
    )
    assert router._is_cloud_error_message(payload) is True


def test_cloud_candidate_normalization_rewrites_obsolete_exp(tmp_path: Path) -> None:
    router = _router(tmp_path, fallback_enabled=True)
    router.models["chat"] = "gemini-2.5-flash"

    candidates = router._build_cloud_candidates(
        task_type="chat",
        profile="communication",
        preferred_model="models/gemini-2.0-flash-exp",
    )
    assert "google/gemini-2.5-flash" in candidates
    assert "models/gemini-2.0-flash-exp" not in candidates


def test_cloud_candidate_list_is_truncated_by_max_candidates(tmp_path: Path) -> None:
    router = ModelRouter(
        config={
            "MODEL_ROUTING_MEMORY_PATH": str(tmp_path / "routing_memory.json"),
            "MODEL_USAGE_REPORT_PATH": str(tmp_path / "usage_report.json"),
            "MODEL_OPS_STATE_PATH": str(tmp_path / "ops_state.json"),
            "MODEL_FEEDBACK_PATH": str(tmp_path / "feedback.json"),
            "MODEL_CLOUD_MAX_CANDIDATES_PER_REQUEST": "2",
            "MODEL_CLOUD_PRIORITY_LIST": ",".join(
                [
                    "google/gemini-2.5-flash",
                    "google/gemini-3-pro-preview",
                    "google/gemini-2.5-pro",
                    "openai/gpt-4o-mini",
                ]
            ),
        }
    )

    candidates = router._build_cloud_candidates(
        task_type="chat",
        profile="communication",
        preferred_model="google/gemini-2.5-flash",
    )
    assert len(candidates) == 2
    assert candidates[0] == "google/gemini-2.5-flash"


def test_cloud_candidate_list_uses_force_cloud_cap(tmp_path: Path) -> None:
    router = ModelRouter(
        config={
            "MODEL_ROUTING_MEMORY_PATH": str(tmp_path / "routing_memory.json"),
            "MODEL_USAGE_REPORT_PATH": str(tmp_path / "usage_report.json"),
            "MODEL_OPS_STATE_PATH": str(tmp_path / "ops_state.json"),
            "MODEL_FEEDBACK_PATH": str(tmp_path / "feedback.json"),
            "MODEL_CLOUD_MAX_CANDIDATES_PER_REQUEST": "5",
            "MODEL_CLOUD_MAX_CANDIDATES_FORCE_CLOUD": "1",
            "MODEL_CLOUD_PRIORITY_LIST": "google/gemini-2.5-flash,google/gemini-2.5-pro,openai/gpt-4o-mini",
        }
    )
    router.force_mode = "force_cloud"

    candidates = router._build_cloud_candidates(
        task_type="chat",
        profile="communication",
        preferred_model="google/gemini-2.5-flash",
    )
    assert candidates == ["google/gemini-2.5-flash"]


def test_force_cloud_candidates_prioritize_priority_list_before_base(tmp_path: Path) -> None:
    router = ModelRouter(
        config={
            "MODEL_ROUTING_MEMORY_PATH": str(tmp_path / "routing_memory.json"),
            "MODEL_USAGE_REPORT_PATH": str(tmp_path / "usage_report.json"),
            "MODEL_OPS_STATE_PATH": str(tmp_path / "ops_state.json"),
            "MODEL_FEEDBACK_PATH": str(tmp_path / "feedback.json"),
            "MODEL_CLOUD_MAX_CANDIDATES_FORCE_CLOUD": "3",
            "MODEL_CLOUD_PRIORITY_LIST": "openai/gpt-4o-mini,openai/gpt-5-mini,google/gemini-2.5-flash",
            "GEMINI_CHAT_MODEL": "google/gemini-2.5-flash",
        }
    )
    router.force_mode = "force_cloud"

    candidates = router._build_cloud_candidates(
        task_type="chat",
        profile="chat",
        preferred_model=None,
        chat_type="private",
        is_owner=True,
        prompt="Короткий тест",
    )

    assert candidates[:3] == ["openai/gpt-4o-mini", "openai/gpt-5-mini", "google/gemini-2.5-flash"]


def test_resolve_cloud_model_uses_group_override_for_group_chat(tmp_path: Path) -> None:
    router = ModelRouter(
        config={
            "MODEL_ROUTING_MEMORY_PATH": str(tmp_path / "routing_memory.json"),
            "MODEL_USAGE_REPORT_PATH": str(tmp_path / "usage_report.json"),
            "MODEL_OPS_STATE_PATH": str(tmp_path / "ops_state.json"),
            "MODEL_FEEDBACK_PATH": str(tmp_path / "feedback.json"),
            "GEMINI_CHAT_MODEL": "gemini-2.5-flash",
            "GEMINI_CHAT_MODEL_GROUP": "gemini-2.5-flash-lite",
        }
    )

    selected = router._resolve_cloud_model(
        task_type="chat",
        profile="chat",
        chat_type="supergroup",
        is_owner=False,
        prompt="обычный ответ в группе",
    )
    assert selected == "gemini-2.5-flash-lite"


def test_resolve_cloud_model_owner_private_important_uses_pro(tmp_path: Path) -> None:
    router = ModelRouter(
        config={
            "MODEL_ROUTING_MEMORY_PATH": str(tmp_path / "routing_memory.json"),
            "MODEL_USAGE_REPORT_PATH": str(tmp_path / "usage_report.json"),
            "MODEL_OPS_STATE_PATH": str(tmp_path / "ops_state.json"),
            "MODEL_FEEDBACK_PATH": str(tmp_path / "feedback.json"),
            "GEMINI_CHAT_MODEL": "gemini-2.5-flash-lite",
            "GEMINI_PRO_MODEL": "gemini-3-pro-preview",
            "GEMINI_CHAT_MODEL_OWNER_PRIVATE_IMPORTANT": "gemini-3-pro-preview",
        }
    )

    selected = router._resolve_cloud_model(
        task_type="chat",
        profile="chat",
        chat_type="private",
        is_owner=True,
        prompt="Давай обсудим план проекта и roadmap",
    )
    assert selected == "gemini-3-pro-preview"


def test_resolve_cloud_model_owner_private_normal_uses_owner_private_model(tmp_path: Path) -> None:
    router = ModelRouter(
        config={
            "MODEL_ROUTING_MEMORY_PATH": str(tmp_path / "routing_memory.json"),
            "MODEL_USAGE_REPORT_PATH": str(tmp_path / "usage_report.json"),
            "MODEL_OPS_STATE_PATH": str(tmp_path / "ops_state.json"),
            "MODEL_FEEDBACK_PATH": str(tmp_path / "feedback.json"),
            "GEMINI_CHAT_MODEL": "gemini-2.5-flash-lite",
            "GEMINI_CHAT_MODEL_OWNER_PRIVATE": "gemini-2.5-flash",
            "MODEL_OWNER_PRIVATE_ALWAYS_PRO": "0",
        }
    )

    selected = router._resolve_cloud_model(
        task_type="chat",
        profile="chat",
        chat_type="private",
        is_owner=True,
        prompt="привет, как дела",
    )
    assert selected == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_route_stream_fallbacks_to_cloud_on_local_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    router = _router(tmp_path, fallback_enabled=True)
    router.is_local_available = True
    router.active_local_model = "zai-org/glm-4.6v-flash"

    async def fake_check_local_health():
        router.is_local_available = True
        return True

    async def failing_stream_chat(payload):
        if False:
            yield ""
        raise StreamFailure("connection_error", "socket reset by peer")

    async def fake_call_gemini(prompt, model_name, context=None, chat_type="private", is_owner=False, max_retries=2):
        return "Облачный fallback ответ"

    monkeypatch.setattr(router, "check_local_health", fake_check_local_health)
    monkeypatch.setattr(router.stream_client, "stream_chat", failing_stream_chat)
    monkeypatch.setattr(router, "_build_cloud_candidates", lambda *args, **kwargs: ["gemini-2.5-flash"])
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)

    chunks = [
        chunk
        async for chunk in router.route_stream(
            prompt="проверь связь",
            task_type="chat",
            context=[],
            chat_type="private",
            is_owner=True,
        )
    ]
    assert chunks == ["Облачный fallback ответ"]
    assert router._usage_report["channels"]["cloud"] >= 1


@pytest.mark.asyncio
async def test_route_stream_without_fallback_returns_local_failure_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = _router(tmp_path, fallback_enabled=False)
    router.is_local_available = True
    router.active_local_model = "zai-org/glm-4.6v-flash"

    async def fake_check_local_health():
        router.is_local_available = True
        return True

    async def failing_stream_chat(payload):
        if False:
            yield ""
        raise StreamFailure("reasoning_loop", "repetitive reasoning chunks")

    monkeypatch.setattr(router, "check_local_health", fake_check_local_health)
    monkeypatch.setattr(router.stream_client, "stream_chat", failing_stream_chat)

    chunks = [
        chunk
        async for chunk in router.route_stream(
            prompt="дай ответ",
            task_type="chat",
            context=[],
            chat_type="private",
            is_owner=True,
        )
    ]
    assert len(chunks) == 1
    assert "Cloud fallback отключён" in chunks[0]


@pytest.mark.asyncio
async def test_route_stream_local_success_does_not_call_cloud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    router = _router(tmp_path, fallback_enabled=True)
    router.is_local_available = True
    router.active_local_model = "zai-org/glm-4.6v-flash"

    async def fake_check_local_health():
        router.is_local_available = True
        return True

    async def ok_stream_chat(payload):
        yield "Привет, "
        yield "мир!"

    async def cloud_must_not_be_called(*args, **kwargs):
        raise AssertionError("Cloud fallback не должен вызываться при успешном local stream")

    monkeypatch.setattr(router, "check_local_health", fake_check_local_health)
    monkeypatch.setattr(router.stream_client, "stream_chat", ok_stream_chat)
    monkeypatch.setattr(router, "_call_gemini", cloud_must_not_be_called)

    chunks = [
        chunk
        async for chunk in router.route_stream(
            prompt="привет",
            task_type="chat",
            context=[],
            chat_type="private",
            is_owner=True,
        )
    ]
    assert "".join(chunks) == "Привет, мир!"
    last_stream = router.get_last_stream_route()
    assert last_stream.get("channel") == "local"


@pytest.mark.asyncio
async def test_route_stream_placeholder_local_model_skips_local_and_uses_cloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Регрессия: при active_local_model=local/local-model нельзя отправлять payload в LM Studio.
    Должен сработать cloud fallback без локального stream-вызова.
    """
    router = _router(tmp_path, fallback_enabled=True)
    router.force_mode = "auto"
    router.is_local_available = True
    router.active_local_model = "local"

    async def fake_check_local_health(force: bool = False):
        router.is_local_available = True
        return True

    async def must_not_use_local_stream(payload):
        raise AssertionError("Локальный stream не должен вызываться для placeholder model_id")
        if False:
            yield ""

    async def fake_call_gemini(*args, **kwargs):
        return "Cloud fallback for placeholder local model"

    monkeypatch.setattr(router, "check_local_health", fake_check_local_health)
    monkeypatch.setattr(router.stream_client, "stream_chat", must_not_use_local_stream)
    monkeypatch.setattr(router, "_build_cloud_candidates", lambda *args, **kwargs: ["google/gemini-2.5-flash"])
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)

    chunks = [
        chunk
        async for chunk in router.route_stream(
            prompt="Проверка placeholder local model",
            task_type="chat",
            context=[],
            chat_type="private",
            is_owner=True,
        )
    ]
    assert chunks == ["Cloud fallback for placeholder local model"]


@pytest.mark.asyncio
async def test_route_stream_provider_model_error_uses_local_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    router = _router(tmp_path, fallback_enabled=True)
    router.is_local_available = True
    router.active_local_model = "zai-org/glm-4.6v-flash"

    async def fake_check_local_health():
        router.is_local_available = True
        return True

    async def failing_stream_chat(payload):
        if False:
            yield ""
        raise StreamFailure("reasoning_loop", "detected repetitive reasoning chunks")

    async def fake_call_gemini(*args, **kwargs):
        return (
            'LLM error: {"error":{"code":404,'
            '"message":"models/gemini-2.0-flash-exp is not found for API version v1beta",'
            '"status":"NOT_FOUND"}}'
        )

    async def fake_local_recovery(*args, **kwargs):
        return "Локальный recovery-ответ без reasoning"

    monkeypatch.setattr(router, "check_local_health", fake_check_local_health)
    monkeypatch.setattr(router.stream_client, "stream_chat", failing_stream_chat)
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)
    monkeypatch.setattr(router, "_call_local_llm", fake_local_recovery)

    chunks = [
        chunk
        async for chunk in router.route_stream(
            prompt="проверка связи",
            task_type="chat",
            context=[],
            chat_type="private",
            is_owner=True,
        )
    ]
    assert chunks == ["Локальный recovery-ответ без reasoning"]


@pytest.mark.asyncio
async def test_route_stream_force_cloud_bypasses_local_stream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    router = _router(tmp_path, fallback_enabled=True)
    router.force_mode = "force_cloud"
    router.is_local_available = True
    router.active_local_model = "zai-org/glm-4.6v-flash"

    async def fake_check_local_health():
        router.is_local_available = True
        return True

    async def must_not_use_local_stream(payload):
        raise AssertionError("Local stream не должен вызываться в force_cloud")
        if False:
            yield ""

    async def fake_call_gemini(*args, **kwargs):
        return "Облачный ответ (force_cloud)"

    monkeypatch.setattr(router, "check_local_health", fake_check_local_health)
    monkeypatch.setattr(router.stream_client, "stream_chat", must_not_use_local_stream)
    monkeypatch.setattr(router, "_build_cloud_candidates", lambda *args, **kwargs: ["gemini-2.5-flash-lite"])
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)

    chunks = [
        chunk
        async for chunk in router.route_stream(
            prompt="проверка force cloud",
            task_type="chat",
            context=[],
            chat_type="private",
            is_owner=True,
        )
    ]
    assert chunks == ["Облачный ответ (force_cloud)"]
    last_stream = router.get_last_stream_route()
    assert last_stream.get("channel") == "cloud"


@pytest.mark.asyncio
async def test_route_stream_force_cloud_does_not_use_local_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    router = _router(tmp_path, fallback_enabled=True)
    router.force_mode = "force_cloud"
    router.is_local_available = True
    router.active_local_model = "zai-org/glm-4.6v-flash"

    async def fake_check_local_health():
        router.is_local_available = True
        return True

    async def fake_call_gemini(*args, **kwargs):
        return (
            'LLM error: {"error":{"code":404,'
            '"message":"models/gemini-2.0-flash-exp is not found for API version v1beta",'
            '"status":"NOT_FOUND"}}'
        )

    async def must_not_use_local_recovery(*args, **kwargs):
        raise AssertionError("Local recovery не должен вызываться в force_cloud")

    monkeypatch.setattr(router, "check_local_health", fake_check_local_health)
    monkeypatch.setattr(router, "_build_cloud_candidates", lambda *args, **kwargs: ["gemini-3-pro-preview"])
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)
    monkeypatch.setattr(router, "_call_local_llm", must_not_use_local_recovery)

    chunks = [
        chunk
        async for chunk in router.route_stream(
            prompt="проверка force cloud not found",
            task_type="chat",
            context=[],
            chat_type="private",
            is_owner=True,
        )
    ]
    assert len(chunks) == 1
    lower_chunk = chunks[0].lower()
    assert (
        "cloud fallback недоступен" in lower_chunk
        or "ошибка cloud (force_cloud)" in lower_chunk
    )


@pytest.mark.asyncio
async def test_route_stream_force_cloud_skips_connection_error_and_uses_next_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Проверяет регрессию: строка "Connection error." не должна считаться валидным
    ответом модели. Роутер обязан перейти к следующему cloud-кандидату.
    """
    router = _router(tmp_path, fallback_enabled=True)
    router.force_mode = "force_cloud"
    router.is_local_available = False

    async def fake_check_local_health():
        router.is_local_available = False
        return False

    attempts = {"count": 0}

    async def fake_call_gemini(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return "Connection error."
        return "Стабильный ответ от второго cloud-кандидата"

    monkeypatch.setattr(router, "check_local_health", fake_check_local_health)
    monkeypatch.setattr(
        router,
        "_build_cloud_candidates",
        lambda *args, **kwargs: ["google/gemini-2.5-flash", "google/gemini-2.5-pro"],
    )
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)

    chunks = [
        chunk
        async for chunk in router.route_stream(
            prompt="Проверка соединения",
            task_type="chat",
            context=[],
            chat_type="private",
            is_owner=True,
        )
    ]

    assert attempts["count"] == 2
    assert chunks == ["Стабильный ответ от второго cloud-кандидата"]


@pytest.mark.asyncio
async def test_route_stream_detects_runtime_error_text_chunk_and_fallbacks_to_cloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Регрессия: LM Studio может вернуть runtime-ошибку как текстовый chunk
    (`400 No models loaded...`) вместо StreamFailure. В этом случае роутер
    обязан НЕ отдавать этот chunk пользователю и переключиться в cloud.
    """
    router = _router(tmp_path, fallback_enabled=True)
    router.force_mode = "auto"
    router.is_local_available = True
    router.active_local_model = "zai-org/glm-4.6v-flash"

    async def fake_check_local_health():
        router.is_local_available = True
        return True

    async def runtime_error_chunk_stream(payload):
        yield "400 No models loaded. Please load a model in the developer page."

    async def fake_call_gemini(*args, **kwargs):
        return "Cloud fallback после runtime-ошибки local stream"

    monkeypatch.setattr(router, "check_local_health", fake_check_local_health)
    monkeypatch.setattr(router.stream_client, "stream_chat", runtime_error_chunk_stream)
    monkeypatch.setattr(router, "_build_cloud_candidates", lambda *args, **kwargs: ["google/gemini-2.5-flash"])
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)

    chunks = [
        chunk
        async for chunk in router.route_stream(
            prompt="Проверка связи",
            task_type="chat",
            context=[],
            chat_type="private",
            is_owner=True,
        )
    ]

    assert chunks == ["Cloud fallback после runtime-ошибки local stream"]


@pytest.mark.asyncio
async def test_route_query_force_cloud_does_not_trigger_local_smart_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    В force_cloud роутер не должен пытаться загружать локальную модель через _smart_load,
    даже если local health сообщает is_local_available=True.
    """
    router = _router(tmp_path, fallback_enabled=True)
    router.force_mode = "force_cloud"
    router.is_local_available = True
    router.active_local_model = "zai-org/glm-4.6v-flash"

    async def must_not_call_smart_load(*args, **kwargs):
        raise AssertionError("_smart_load не должен вызываться в force_cloud")

    async def fake_check_local_health():
        router.is_local_available = True
        return True

    async def fake_call_gemini(*args, **kwargs):
        return "Cloud response without local preload"

    monkeypatch.setattr(router, "_smart_load", must_not_call_smart_load)
    monkeypatch.setattr(router, "check_local_health", fake_check_local_health)
    monkeypatch.setattr(router, "_build_cloud_candidates", lambda *args, **kwargs: ["google/gemini-2.5-flash"])
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)

    response = await router.route_query(
        prompt="Проверка force cloud",
        task_type="chat",
        context=[],
        chat_type="private",
        is_owner=True,
    )
    assert response == "Cloud response without local preload"


@pytest.mark.asyncio
async def test_route_query_force_cloud_skips_local_health_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    В force_cloud route_query не должен вызывать даже check_local_health:
    это предотвращает любые касания LM Studio/Ollama в cloud-only режиме.
    """
    router = _router(tmp_path, fallback_enabled=True)
    router.force_mode = "force_cloud"

    async def must_not_call_check_local_health(*args, **kwargs):
        raise AssertionError("check_local_health не должен вызываться в force_cloud")

    async def fake_call_gemini(*args, **kwargs):
        return "Cloud-only response"

    monkeypatch.setattr(router, "check_local_health", must_not_call_check_local_health)
    monkeypatch.setattr(
        router,
        "_build_cloud_candidates",
        lambda *args, **kwargs: ["google/gemini-2.5-flash"],
    )
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)

    response = await router.route_query(
        prompt="strict cloud mode",
        task_type="chat",
        context=[],
        chat_type="private",
        is_owner=True,
    )
    assert response == "Cloud-only response"


@pytest.mark.asyncio
async def test_route_query_auto_cloud_primary_skips_local_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    В auto-режиме для cloud-primary задач (reasoning/critical) роутер
    не должен заранее трогать локальный runtime (health/autoload/smart-load).
    """
    router = _router(tmp_path, fallback_enabled=True)
    router.force_mode = "auto"
    router.is_local_available = False

    async def must_not_call_check_local_health(*args, **kwargs):
        raise AssertionError("check_local_health не должен вызываться в cloud-primary auto")

    async def must_not_call_autoload(*args, **kwargs):
        raise AssertionError("_maybe_autoload_local_model не должен вызываться в cloud-primary auto")

    async def must_not_call_smart_load(*args, **kwargs):
        raise AssertionError("_smart_load не должен вызываться в cloud-primary auto")

    async def fake_call_gemini(*args, **kwargs):
        return "Cloud primary response without local touching"

    monkeypatch.setattr(router, "check_local_health", must_not_call_check_local_health)
    monkeypatch.setattr(router, "_maybe_autoload_local_model", must_not_call_autoload)
    monkeypatch.setattr(router, "_smart_load", must_not_call_smart_load)
    monkeypatch.setattr(
        router,
        "_build_cloud_candidates",
        lambda *args, **kwargs: ["google/gemini-2.5-flash"],
    )
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)

    response = await router.route_query(
        prompt="Сделай глубокий reasoning анализ плана",
        task_type="reasoning",
        context=[],
        chat_type="private",
        is_owner=True,
    )
    assert response == "Cloud primary response without local touching"


@pytest.mark.asyncio
async def test_route_query_force_cloud_skips_preflight_blocked_provider_and_uses_next_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Регрессия: в force_cloud preflight-блок первого провайдера
    не должен останавливать весь cloud-маршрут.
    """
    router = _router(tmp_path, fallback_enabled=True)
    router.force_mode = "force_cloud"
    router.is_local_available = False

    monkeypatch.setattr(
        router,
        "_build_cloud_candidates",
        lambda *args, **kwargs: ["google/gemini-2.5-flash", "openai/gpt-4o-mini"],
    )

    def fake_check_cloud_preflight(provider: str):
        if provider == "google":
            return "Preflight: провайдер 'google' заблокирован (R15 Gate)"
        return None

    calls: list[str] = []

    async def fake_call_gemini(prompt, model_name, context, chat_type, is_owner, max_retries=0):
        calls.append(str(model_name))
        return "Cloud fallback via OpenAI OK"

    monkeypatch.setattr(router, "_check_cloud_preflight", fake_check_cloud_preflight)
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)

    response = await router.route_query(
        prompt="Проверка preflight skip",
        task_type="chat",
        context=[],
        chat_type="private",
        is_owner=True,
    )

    assert response == "Cloud fallback via OpenAI OK"
    assert calls == ["openai/gpt-4o-mini"]


@pytest.mark.asyncio
async def test_route_query_force_cloud_failure_updates_last_route_as_cloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Даже при cloud-ошибке в force_cloud last_route должен обновляться как cloud,
    чтобы UI не показывал устаревший local-маршрут.
    """
    router = _router(tmp_path, fallback_enabled=True)
    router.force_mode = "force_cloud"

    async def fake_call_gemini(*args, **kwargs):
        return "❌ OpenClaw Error (0): TimeoutError"

    monkeypatch.setattr(
        router,
        "_build_cloud_candidates",
        lambda *args, **kwargs: ["google/gemini-2.5-flash"],
    )
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)

    response = await router.route_query(
        prompt="cloud fail route mark",
        task_type="chat",
        context=[],
        chat_type="private",
        is_owner=True,
    )
    last_route = router.get_last_route()

    assert "Ошибка Cloud (force_cloud)" in response
    assert last_route.get("channel") == "cloud"
    assert last_route.get("route_reason") == "force_cloud_failed"


@pytest.mark.asyncio
async def test_route_query_stream_force_cloud_does_not_use_local_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    В route_query_stream режим force_cloud не должен уходить в локальную ветку
    даже при доступной локалке.
    """
    router = _router(tmp_path, fallback_enabled=True)
    router.force_mode = "force_cloud"
    router.is_local_available = True
    router.active_local_model = "zai-org/glm-4.6v-flash"

    async def must_not_call_route_query(*args, **kwargs):
        raise AssertionError("route_query (локальная ветка) не должен вызываться в force_cloud")

    async def fake_call_gemini(*args, **kwargs):
        return "Cloud stream response"

    monkeypatch.setattr(router, "route_query", must_not_call_route_query)
    monkeypatch.setattr(router, "_build_cloud_candidates", lambda *args, **kwargs: ["google/gemini-2.5-flash"])
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)

    chunks = [
        chunk
        async for chunk in router.route_query_stream(
            prompt="Проверка force cloud stream",
            task_type="chat",
            context=[],
            chat_type="private",
            is_owner=True,
            use_rag=False,
            skip_swarm=True,
        )
    ]
    assert chunks == ["Cloud stream response"]


@pytest.mark.asyncio
async def test_route_stream_force_cloud_skips_local_health_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    В route_stream режим force_cloud также не должен запускать local health-check.
    """
    router = _router(tmp_path, fallback_enabled=True)
    router.force_mode = "force_cloud"

    async def must_not_call_check_local_health(*args, **kwargs):
        raise AssertionError("check_local_health не должен вызываться в force_cloud stream")

    async def fake_call_gemini(*args, **kwargs):
        return "Cloud stream response without local probe"

    monkeypatch.setattr(router, "check_local_health", must_not_call_check_local_health)
    monkeypatch.setattr(
        router,
        "_build_cloud_candidates",
        lambda *args, **kwargs: ["google/gemini-2.5-flash"],
    )
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)

    chunks = [
        chunk
        async for chunk in router.route_stream(
            prompt="force cloud stream no local probe",
            task_type="chat",
            context=[],
            chat_type="private",
            is_owner=True,
        )
    ]
    assert chunks == ["Cloud stream response without local probe"]


@pytest.mark.asyncio
async def test_route_query_stops_cloud_rotation_on_fatal_auth_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    При фатальной cloud-ошибке (invalid/leaked key) роутер должен
    остановить перебор кандидатов и вернуть ошибку сразу.
    """
    router = _router(tmp_path, fallback_enabled=True)
    router.force_mode = "force_cloud"
    router.is_local_available = False

    attempts = {"count": 0}

    async def fake_call_gemini(*args, **kwargs):
        attempts["count"] += 1
        return "❌ OpenClaw Error (0): Connection error. | Google API 403: Your API key was reported as leaked."

    monkeypatch.setattr(
        router,
        "_build_cloud_candidates",
        lambda *args, **kwargs: ["google/gemini-2.5-flash", "openai/gpt-4o-mini"],
    )
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)

    response = await router.route_query(
        prompt="Проверка fatal cloud auth",
        task_type="chat",
        context=[],
        chat_type="private",
        is_owner=True,
    )

    assert attempts["count"] == 1
    assert "скомпрометированный" in response.lower() or "leaked" in response.lower()


@pytest.mark.asyncio
async def test_route_query_stops_cloud_rotation_on_api_disabled_403(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Ошибка вида «API not used/disabled» должна считаться фатальной для
    текущего запроса и останавливать перебор cloud-кандидатов.
    """
    router = _router(tmp_path, fallback_enabled=True)
    router.force_mode = "force_cloud"
    router.is_local_available = False

    attempts = {"count": 0}

    async def fake_call_gemini(*args, **kwargs):
        attempts["count"] += 1
        return (
            "❌ OpenClaw Error (0): Connection error. | Google API 403: "
            "Generative Language API has not been used in project 123 or it is disabled. "
            "Enable it by visiting console.developers.google.com"
        )

    monkeypatch.setattr(
        router,
        "_build_cloud_candidates",
        lambda *args, **kwargs: ["google/gemini-2.5-flash", "openai/gpt-4o-mini"],
    )
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)

    response = await router.route_query(
        prompt="Проверка fatal cloud api-disabled",
        task_type="chat",
        context=[],
        chat_type="private",
        is_owner=True,
    )

    assert attempts["count"] == 1
    assert "generative language api" in response.lower() or "google cloud" in response.lower()


@pytest.mark.asyncio
async def test_route_query_autoloads_local_when_no_model_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Если локалка «жива», но модель не загружена (no_model_loaded),
    route_query должен попытаться auto-load и отдать локальный ответ.
    """
    router = _router(tmp_path, fallback_enabled=True)
    router.force_mode = "auto"
    router.is_local_available = False
    router.last_local_load_error = "no_model_loaded"

    async def fake_check_local_health():
        router.is_local_available = False
        return False

    async def fake_autoload(reason: str = "") -> bool:
        router.is_local_available = True
        router.active_local_model = "zai-org/glm-4.6v-flash"
        return True

    async def fake_call_local_llm(prompt: str, context=None, chat_type: str = "private", is_owner: bool = False):
        return "Локальный ответ после auto-load"

    monkeypatch.setattr(router, "check_local_health", fake_check_local_health)
    monkeypatch.setattr(router, "_maybe_autoload_local_model", fake_autoload)
    monkeypatch.setattr(router, "_call_local_llm", fake_call_local_llm)

    response = await router.route_query(
        prompt="Проверка автозагрузки локальной модели",
        task_type="chat",
        context=[],
        chat_type="private",
        is_owner=True,
    )
    assert response == "Локальный ответ после auto-load"


@pytest.mark.asyncio
async def test_route_stream_autoloads_local_when_no_model_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Для stream-ветки должна работать та же логика авто-загрузки локальной модели
    при no_model_loaded.
    """
    router = _router(tmp_path, fallback_enabled=True)
    router.force_mode = "auto"
    router.is_local_available = False
    router.last_local_load_error = "no_model_loaded"

    async def fake_check_local_health():
        router.is_local_available = False
        return False

    async def fake_autoload(reason: str = "") -> bool:
        router.is_local_available = True
        router.active_local_model = "zai-org/glm-4.6v-flash"
        return True

    async def local_stream_ok(payload):
        yield "stream "
        yield "ok"

    monkeypatch.setattr(router, "check_local_health", fake_check_local_health)
    monkeypatch.setattr(router, "_maybe_autoload_local_model", fake_autoload)
    monkeypatch.setattr(router.stream_client, "stream_chat", local_stream_ok)

    chunks = [
        chunk
        async for chunk in router.route_stream(
            prompt="Проверка stream auto-load",
            task_type="chat",
            context=[],
            chat_type="private",
            is_owner=True,
        )
    ]
    assert "".join(chunks) == "stream ok"


@pytest.mark.asyncio
async def test_route_query_force_cloud_uses_zero_retry_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    В force_cloud режиме не делаем внутренние retry на кандидате, чтобы
    не зависать в очереди при деградации cloud-канала.
    """
    router = _router(tmp_path, fallback_enabled=True)
    router.force_mode = "force_cloud"
    router.is_local_available = False

    observed = {"max_retries": None}

    async def fake_call_gemini(*args, **kwargs):
        observed["max_retries"] = kwargs.get("max_retries")
        return "Cloud ok"

    monkeypatch.setattr(router, "_build_cloud_candidates", lambda *args, **kwargs: ["google/gemini-2.5-flash"])
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)

    response = await router.route_query(
        prompt="force cloud no retry",
        task_type="chat",
        context=[],
        chat_type="private",
        is_owner=True,
    )
    assert response == "Cloud ok"
    assert observed["max_retries"] == 0


@pytest.mark.asyncio
async def test_route_query_force_cloud_stops_on_fail_fast_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    При исчерпании fail-fast бюджета force_cloud не должен перебирать
    весь список cloud-кандидатов.
    """
    router = ModelRouter(
        config={
            "MODEL_ROUTING_MEMORY_PATH": str(tmp_path / "routing_memory.json"),
            "MODEL_USAGE_REPORT_PATH": str(tmp_path / "usage_report.json"),
            "MODEL_OPS_STATE_PATH": str(tmp_path / "ops_state.json"),
            "MODEL_FEEDBACK_PATH": str(tmp_path / "feedback.json"),
            "CLOUD_FAIL_FAST_BUDGET_SECONDS": "1",
            "MODEL_CLOUD_MAX_CANDIDATES_FORCE_CLOUD": "3",
        }
    )
    router.force_mode = "force_cloud"
    router.is_local_available = False

    attempts = {"count": 0}

    async def fake_call_gemini(*args, **kwargs):
        attempts["count"] += 1
        await asyncio.sleep(1.1)
        return "❌ OpenClaw Error (0): Connection error."

    monkeypatch.setattr(
        router,
        "_build_cloud_candidates",
        lambda *args, **kwargs: [
            "google/gemini-2.5-flash",
            "google/gemini-2.5-pro",
            "openai/gpt-4o-mini",
        ],
    )
    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)

    response = await router.route_query(
        prompt="force cloud fail-fast budget",
        task_type="chat",
        context=[],
        chat_type="private",
        is_owner=True,
    )

    assert attempts["count"] == 1
    assert "превышено время ожидания" in response.lower()
