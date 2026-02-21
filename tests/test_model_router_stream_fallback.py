# -*- coding: utf-8 -*-
"""
Тесты route_stream fallback-политики (Phase 17.8).

Покрытие:
1. При ошибке local stream роутер отдает cloud fallback.
2. При отключенном fallback возвращает понятную локальную ошибку.
3. При успешном local stream fallback в облако не вызывается.
"""

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
    assert "gemini-2.5-flash" in candidates
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
