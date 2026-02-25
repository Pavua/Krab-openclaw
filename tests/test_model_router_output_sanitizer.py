# -*- coding: utf-8 -*-
"""Тесты очистки служебных маркеров и cloud-candidate фильтра."""

from pathlib import Path

from src.core.model_manager import ModelRouter


def _router(tmp_path: Path) -> ModelRouter:
    return ModelRouter(
        config={
            "MODEL_ROUTING_MEMORY_PATH": str(tmp_path / "routing_memory.json"),
            "MODEL_USAGE_REPORT_PATH": str(tmp_path / "usage_report.json"),
            "MODEL_OPS_STATE_PATH": str(tmp_path / "ops_state.json"),
            "MODEL_FEEDBACK_PATH": str(tmp_path / "feedback.json"),
        }
    )


def test_sanitize_model_text_strips_service_tokens(tmp_path: Path) -> None:
    router = _router(tmp_path)
    raw = "<|begin_of_box|>  Привет, мир!  <|end_of_box|>\n\n"
    assert router._sanitize_model_text(raw) == "Привет, мир!"


def test_build_cloud_candidates_skips_local_only_identifiers(tmp_path: Path) -> None:
    router = _router(tmp_path)
    candidates = router._build_cloud_candidates(
        task_type="chat",
        profile="chat",
        preferred_model="qwen2.5-coder-7b-instruct-mlx",
    )

    assert all("mlx" not in item.lower() for item in candidates)
    assert any("gemini" in item.lower() for item in candidates)


def test_sanitize_model_text_strips_internal_box_artifacts(tmp_path: Path) -> None:
    router = _router(tmp_path)
    raw = """
[[reply_to:69366]] Я здесь и готов помочь.
<|begin_of_box|>NO_REPLY<|end_of_box|>
<|begin_of_box|>{"action":"sessions_send","parameters":{"sessionKey":{"type":"string"}}}<|end_of_box|>
"""
    cleaned = router._sanitize_model_text(raw)

    assert "begin_of_box" not in cleaned.lower()
    assert "no_reply" not in cleaned.lower()
    assert "sessions_send" not in cleaned.lower()
    assert "я здесь и готов помочь" in cleaned.lower()


def test_sanitize_model_text_strips_generic_action_json_artifacts(tmp_path: Path) -> None:
    router = _router(tmp_path)
    raw = """
<|begin_of_box|>{"action":"执行","parameters":{"target":"telegram","mode":"notify"}}<|end_of_box|>
<|begin_of_box|>{"action":"reply_to_user","parameters":{"text":"Черновик"}}<|end_of_box|>
Ответ пользователю: связь стабильна.
"""
    cleaned = router._sanitize_model_text(raw)

    assert "\"action\"" not in cleaned.lower()
    assert "\"parameters\"" not in cleaned.lower()
    assert "begin_of_box" not in cleaned.lower()
    assert "связь стабильна" in cleaned.lower()


def test_sanitize_model_text_strips_agents_and_default_channel_dump(tmp_path: Path) -> None:
    router = _router(tmp_path)
    raw = """
## /Users/pablito/.openclaw/workspace/AGENTS.md
# AGENTS.md - Workspace Agents
## Agent List
### Default Agents
"Name": "whatsapp"
- "Default Channel"
"Description": "whatsapp"
Обычный человеческий ответ без служебного мусора.
"""
    cleaned = router._sanitize_model_text(raw)

    assert "agents.md - workspace agents" not in cleaned.lower()
    assert "default channel" not in cleaned.lower()
    assert "обычный человеческий ответ" in cleaned.lower()


def test_sanitize_model_text_strips_tools_schema_and_log_spam(tmp_path: Path) -> None:
    router = _router(tmp_path)
    raw = """
Включено логирования действий. Пауза 5 минут.
<tools>
{"name":"heartbeat_check","parameters":{"type":"string"},"required":["action"]}}
</tools>
You may call one or more functions to assist with the user query.
You can also use the session_status function to get information about the current session.
Нормальный ответ пользователю: связь проверена.
"""
    cleaned = router._sanitize_model_text(raw)

    lowered = cleaned.lower()
    assert "<tools>" not in lowered
    assert "session_status function" not in lowered
    assert "you may call one or more functions" not in lowered
    assert "включено логирован" not in lowered
    assert "связь проверена" in lowered
