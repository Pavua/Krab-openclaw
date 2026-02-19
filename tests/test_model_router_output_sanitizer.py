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
