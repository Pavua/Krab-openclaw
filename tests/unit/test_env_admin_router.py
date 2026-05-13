# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.env_admin_router`` — Wave 189 (Session 48).

Покрытие:
- factory + endpoints (HTML page + JSON list)
- secret detection regex (token/secret/key/password/api_key/hash/dsn)
- masking логика (•••• + last 4 chars)
- категория-группировка
- .env parser (KEY=VAL, surrounding quotes, comments)
- cache TTL 30s
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers import env_admin_router as ear
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.env_admin_router import build_env_admin_router

_EXPECTED_CATEGORIES = {
    "ai_models",
    "telegram",
    "memory",
    "voice",
    "sentry",
    "routing",
    "api_keys",
    "agent_gates",
}


def _make_client() -> TestClient:
    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    app = FastAPI()
    app.include_router(build_env_admin_router(ctx))
    return TestClient(app)


# ── Metadata structure ──────────────────────────────────────────────────────


def test_env_metadata_has_at_least_30_vars() -> None:
    """Минимум 30 переменных зарегистрировано."""
    assert len(ear._ENV_METADATA) >= 30


def test_env_metadata_all_8_categories_present() -> None:
    """Все 8 категорий имеют хотя бы одну переменную."""
    cats = {entry[1] for entry in ear._ENV_METADATA}
    assert cats == _EXPECTED_CATEGORIES


def test_env_metadata_tuple_shape() -> None:
    """Каждая запись — кортеж (key, category, desc, default) длины 4."""
    for entry in ear._ENV_METADATA:
        assert len(entry) == 4
        key, cat, desc, default = entry
        assert isinstance(key, str) and key
        assert isinstance(cat, str) and cat in _EXPECTED_CATEGORIES
        assert isinstance(desc, str) and desc
        assert isinstance(default, str)  # "" допустимо


def test_category_order_matches_labels() -> None:
    """_CATEGORY_ORDER и _CATEGORY_LABELS синхронизированы."""
    assert set(ear._CATEGORY_ORDER) == set(ear._CATEGORY_LABELS.keys())
    assert len(ear._CATEGORY_ORDER) == 8


# ── Secret detection ────────────────────────────────────────────────────────


def test_is_secret_name_positive() -> None:
    """Регекс ловит token/secret/key/password/api_key/hash/dsn."""
    positives = [
        "SENTRY_AUTH_TOKEN",
        "GEMINI_API_KEY_PAID",
        "TELEGRAM_API_HASH",
        "SENTRY_DSN",
        "SOME_PASSWORD",
        "MY_SECRET",
        "GITHUB_TOKEN",
        "OPENAI_API_KEY",
    ]
    for name in positives:
        assert ear._is_secret_name(name), f"{name} должен матчиться"


def test_is_secret_name_negative() -> None:
    """Невинные имена не матчатся."""
    negatives = [
        "KRAB_RAG_PHASE2_ENABLED",
        "TELEGRAM_API_ID",
        "KRAB_LLM_IDLE_TIMEOUT_SEC",
        "KRAB_MODEL_FOOTER_ENABLED",
    ]
    for name in negatives:
        assert not ear._is_secret_name(name), f"{name} НЕ должен матчиться"


# ── Masking ─────────────────────────────────────────────────────────────────


def test_mask_value_long_key() -> None:
    """Длинный ключ: показываем последние 4 символа."""
    assert ear._mask_value("sk-ant-api03-very-long-key-here-6411") == "••••••••6411"


def test_mask_value_short_4_chars() -> None:
    """4 символа и меньше — полная маскировка."""
    assert ear._mask_value("abcd") == "••••"
    assert ear._mask_value("ab") == "••"


def test_mask_value_empty() -> None:
    """Пустая строка → пустая строка (без маски)."""
    assert ear._mask_value("") == ""


def test_mask_value_5_chars_returns_padded() -> None:
    """5 символов: 8 точек + last 4."""
    assert ear._mask_value("abcde") == "••••••••bcde"


# ── .env parser ─────────────────────────────────────────────────────────────


def test_parse_dotenv_basic(tmp_path: Path) -> None:
    """Парсер вытаскивает KEY=VALUE строки."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment line\n"
        "KEY1=value1\n"
        "KEY2=\"quoted value\"\n"
        "KEY3='single quoted'\n"
        "\n"
        "# another comment\n"
        "EMPTY_KEY=\n",
        encoding="utf-8",
    )
    parsed = ear._parse_dotenv(env_file)
    assert parsed["KEY1"] == "value1"
    assert parsed["KEY2"] == "quoted value"
    assert parsed["KEY3"] == "single quoted"
    assert parsed["EMPTY_KEY"] == ""
    assert "# comment line" not in parsed


def test_parse_dotenv_missing_file(tmp_path: Path) -> None:
    """Несуществующий файл → пустой dict."""
    assert ear._parse_dotenv(tmp_path / "nope.env") == {}


# ── Snapshot building ────────────────────────────────────────────────────────


def test_build_snapshot_categories_present() -> None:
    """Все 8 категорий присутствуют в snapshot."""
    ear._invalidate_cache()
    snap = ear._build_env_snapshot()
    assert snap["ok"] is True
    assert set(snap["categories"].keys()) == _EXPECTED_CATEGORIES
    for _cat_key, cat_data in snap["categories"].items():
        assert "label" in cat_data
        assert "emoji" in cat_data
        assert "vars" in cat_data


def test_snapshot_set_env_var_appears() -> None:
    """Установленная env-переменная попадает в snapshot со значением."""
    ear._invalidate_cache()
    with patch.dict("os.environ", {"KRAB_RAG_PHASE2_ENABLED": "1"}, clear=False):
        snap = ear._build_env_snapshot()
    memory_vars = snap["categories"]["memory"]["vars"]
    rec = next((v for v in memory_vars if v["key"] == "KRAB_RAG_PHASE2_ENABLED"), None)
    assert rec is not None
    assert rec["set"] is True
    assert rec["value"] == "1"
    assert rec["masked"] is False
    assert rec["secret"] is False


def test_snapshot_secret_is_masked() -> None:
    """SENTRY_AUTH_TOKEN маскируется."""
    ear._invalidate_cache()
    secret_value = "sntrys_xxxxxxxxxx_abcd1234"
    with patch.dict("os.environ", {"SENTRY_AUTH_TOKEN": secret_value}, clear=False):
        snap = ear._build_env_snapshot()
    sentry_vars = snap["categories"]["sentry"]["vars"]
    rec = next((v for v in sentry_vars if v["key"] == "SENTRY_AUTH_TOKEN"), None)
    assert rec is not None
    assert rec["set"] is True
    assert rec["masked"] is True
    assert rec["secret"] is True
    # Last 4 chars видны.
    assert rec["value"].endswith("1234")
    # Не показываем полное значение.
    assert secret_value not in rec["value"]


def test_snapshot_unset_var() -> None:
    """Незаданная переменная: set=False, value=''."""
    ear._invalidate_cache()
    with patch.dict("os.environ", {}, clear=True):
        # Заодно — пустой .env (patch путь чтобы парсер не нашёл).
        with patch.object(ear, "_DOTENV_PATH", Path("/nonexistent/.env")):
            snap = ear._build_env_snapshot()
    # Возьмём первый ключ.
    all_vars: list[dict] = []
    for cat in snap["categories"].values():
        all_vars.extend(cat["vars"])
    unset_rec = next((v for v in all_vars if not v["set"]), None)
    assert unset_rec is not None
    assert unset_rec["value"] == ""
    assert unset_rec["masked"] is False


def test_snapshot_counters() -> None:
    """total/set/secret counts согласованы."""
    ear._invalidate_cache()
    snap = ear._build_env_snapshot()
    assert snap["total_count"] == len(ear._ENV_METADATA)
    assert snap["set_count"] >= 0
    assert snap["set_count"] <= snap["total_count"]
    # Хотя бы один secret в metadata зарегистрирован.
    assert snap["secret_count"] >= 1


# ── Cache ───────────────────────────────────────────────────────────────────


def test_cache_returns_same_snapshot_within_ttl() -> None:
    """В течение TTL — возвращается тот же объект."""
    ear._invalidate_cache()
    snap1 = ear._get_cached_snapshot()
    snap2 = ear._get_cached_snapshot()
    assert snap1 is snap2


# ── Endpoints ───────────────────────────────────────────────────────────────


def test_get_env_list_returns_200_with_payload() -> None:
    """JSON endpoint отдаёт корректную структуру."""
    ear._invalidate_cache()
    client = _make_client()
    res = client.get("/api/admin/env/list")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "categories" in data
    assert "total_count" in data
    assert data["total_count"] == len(ear._ENV_METADATA)
    assert set(data["categories"].keys()) == _EXPECTED_CATEGORIES


def test_get_admin_env_html_renders() -> None:
    """HTML страница рендерится и содержит ключевые элементы."""
    client = _make_client()
    res = client.get("/admin/env")
    assert res.status_code == 200
    body = res.text
    assert "<title>Krab · Admin Env</title>" in body
    assert "/api/admin/env/list" in body  # fetch URL
    assert "Wave 189" in body
    assert "READ-ONLY" in body


def test_env_list_payload_has_var_record_fields() -> None:
    """Каждая var-запись содержит все обязательные поля."""
    ear._invalidate_cache()
    client = _make_client()
    res = client.get("/api/admin/env/list")
    data = res.json()
    required = {"key", "value", "masked", "set", "description", "default", "secret"}
    for cat in data["categories"].values():
        for rec in cat["vars"]:
            missing = required - set(rec.keys())
            assert not missing, f"var {rec.get('key')} missing: {missing}"
