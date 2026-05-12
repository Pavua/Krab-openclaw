# -*- coding: utf-8 -*-
"""
Wave 162: тесты для HuggingFace cache cleanup.

Покрытие:
- discover_caches группирует HF model dirs + игнорирует симлинки/datasets/.locks
- filter_stale_candidates отбирает только stale+large
- active_models guard защищает loaded LM Studio модели
- _parse_repo_from_dir корректно конвертит `models--org--name` → `org/name`
- run_cleanup dry-run не удаляет файлы
- run_cleanup --apply реально удаляет и репортит freed_gb
- load_active_models читает LM Studio index + дополнительные state files
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import krab_hf_cache_cleanup as hf


def _make_hf_model(
    hub: Path,
    *,
    repo_dir: str,
    payload_bytes: int,
    mtime: float,
) -> Path:
    """Создаёт HF cache-like model dir с одним blob нужного размера и mtime."""
    model_dir = hub / repo_dir
    blobs = model_dir / "blobs"
    snapshots = model_dir / "snapshots" / "abc123"
    blobs.mkdir(parents=True, exist_ok=True)
    snapshots.mkdir(parents=True, exist_ok=True)
    blob_file = blobs / "deadbeef"
    blob_file.write_bytes(b"x" * payload_bytes)
    # snapshot — симлинк на blob (как делает HF). Симлинки внутри model dir
    # игнорируются _dir_size_bytes но это OK — мы считаем blob.
    target = snapshots / "weights.bin"
    target.write_text("dummy")  # реальный файл, не симлинк, для теста
    # Принудительно проставляем mtime для ВСЕХ элементов включая parent dirs.
    # Идём bottom-up чтобы parent utime не перезаписался при write inner.
    for sub in [
        blob_file,
        target,
        snapshots,
        snapshots.parent,
        blobs,
        model_dir,
    ]:
        os.utime(sub, (mtime, mtime))
    return model_dir


# ---- 1: parse repo helper -------------------------------------------------


def test_parse_repo_from_dir_canonical() -> None:
    assert (
        hf._parse_repo_from_dir("models--mlx-community--whisper-large-v3-mlx")
        == "mlx-community/whisper-large-v3-mlx"
    )


def test_parse_repo_from_dir_non_model_returns_none() -> None:
    assert hf._parse_repo_from_dir("datasets--wikitext") is None
    assert hf._parse_repo_from_dir(".locks") is None


# ---- 2: discover_caches ----------------------------------------------------


def test_discover_caches_finds_models_skips_other(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    _make_hf_model(hub, repo_dir="models--org--small", payload_bytes=1024, mtime=1_000_000.0)
    _make_hf_model(hub, repo_dir="models--org--big", payload_bytes=2_048_000, mtime=2_000_000.0)
    # datasets-- должен быть проигнорирован
    (hub / "datasets--wikitext").mkdir()
    (hub / "datasets--wikitext" / "x.bin").write_bytes(b"y" * 10_000)
    # .locks тоже игнорируется
    (hub / ".locks").mkdir()
    (hub / ".locks" / "lockfile").write_bytes(b"z" * 5_000)
    # Симлинк (как `4TB -> /Volumes/4TB`) — игнорируется
    (hub / "4TB").symlink_to(tmp_path / "nowhere")

    out = hf.discover_caches(cache_roots=[hub], now_fn=lambda: 3_000_000.0)
    repos = sorted((e["repo_id"] or "") for e in out)
    assert repos == ["org/big", "org/small"]
    big = next(e for e in out if e["repo_id"] == "org/big")
    # size_bytes должен покрывать ≥2_048_000 (плюс ~5 байт от weights.bin)
    assert big["size_bytes"] >= 2_048_000
    # age_days = (3M - 2M) / 86400 ≈ 11.57
    assert big["age_days"] == pytest.approx((3_000_000 - 2_000_000) / 86400.0, abs=0.01)


def test_discover_caches_missing_root_returns_empty(tmp_path: Path) -> None:
    out = hf.discover_caches(cache_roots=[tmp_path / "nonexistent"])
    assert out == []


# ---- 3: filter_stale_candidates ------------------------------------------


def test_filter_stale_candidates_age_and_size_thresholds() -> None:
    caches = [
        {"path": "/a", "repo_id": "x/old-big", "size_bytes": 600 * 1024 * 1024, "age_days": 45.0},
        {"path": "/b", "repo_id": "x/old-small", "size_bytes": 100 * 1024 * 1024, "age_days": 45.0},  # too small
        {"path": "/c", "repo_id": "x/new-big", "size_bytes": 700 * 1024 * 1024, "age_days": 5.0},  # too fresh
        {"path": "/d", "repo_id": "x/stale-active", "size_bytes": 800 * 1024 * 1024, "age_days": 60.0},
    ]
    filtered = hf.filter_stale_candidates(
        caches,
        min_age_days=30,
        min_size_mb=500,
        active_models={"x/stale-active"},
    )
    # Only /a remains: /b (small), /c (fresh), /d (active) excluded
    assert len(filtered) == 1
    assert filtered[0]["path"] == "/a"


# ---- 4: load_active_models ------------------------------------------------


def test_load_active_models_from_lm_studio_index(tmp_path: Path) -> None:
    index = tmp_path / "model-index-cache.json"
    index.write_text(
        json.dumps(
            {
                "models": [
                    {"indexedModelIdentifier": "mlx-community/whisper-large-v3-mlx"},
                    {"indexedModelIdentifier": "EZCon/Huihui-gemma-4-E4B-it-abliterated-4bit-mlx"},
                    {"random": "noise"},
                ]
            }
        ),
        encoding="utf-8",
    )
    active = hf.load_active_models(lm_studio_index=index)
    assert "mlx-community/whisper-large-v3-mlx" in active
    assert "EZCon/Huihui-gemma-4-E4B-it-abliterated-4bit-mlx" in active
    assert len(active) == 2


def test_load_active_models_missing_file_returns_empty(tmp_path: Path) -> None:
    active = hf.load_active_models(lm_studio_index=tmp_path / "nope.json")
    assert active == set()


def test_load_active_models_handles_extra_paths(tmp_path: Path) -> None:
    extra = tmp_path / "current_model.json"
    extra.write_text(json.dumps({"repo_id": "user/special-model"}), encoding="utf-8")
    active = hf.load_active_models(
        lm_studio_index=tmp_path / "nope.json",
        extra_active_paths=[extra],
    )
    assert active == {"user/special-model"}


# ---- 5: run_cleanup dry-run safety ----------------------------------------


def test_run_cleanup_dry_run_does_not_delete(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    model_dir = _make_hf_model(
        hub,
        repo_dir="models--org--ancient-big",
        payload_bytes=600 * 1024 * 1024,
        mtime=1_000_000.0,  # very old
    )
    # now = 1_000_000 + 100 days
    now = 1_000_000.0 + 100 * 86400
    report = hf.run_cleanup(
        cache_roots=[hub],
        min_age_days=30,
        min_size_mb=500,
        apply=False,
        active_models=set(),
        now_fn=lambda: now,
    )
    assert report["apply"] is False
    assert report["stale_candidates_count"] == 1
    assert len(report["would_save"]) == 1
    assert report["would_save"][0]["repo_id"] == "org/ancient-big"
    # Файл ВСЁ ЕЩЁ на месте
    assert model_dir.exists()
    assert "deleted" not in report  # dry-run не репортит deleted


# ---- 6: run_cleanup --apply actually deletes -----------------------------


def test_run_cleanup_apply_deletes_and_reports_freed(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    stale_dir = _make_hf_model(
        hub,
        repo_dir="models--org--stale",
        payload_bytes=600 * 1024 * 1024,
        mtime=1_000_000.0,
    )
    fresh_dir = _make_hf_model(
        hub,
        repo_dir="models--org--fresh",
        payload_bytes=600 * 1024 * 1024,
        mtime=1_000_000.0 + 99 * 86400,  # fresh
    )
    now = 1_000_000.0 + 100 * 86400
    report = hf.run_cleanup(
        cache_roots=[hub],
        min_age_days=30,
        min_size_mb=500,
        apply=True,
        active_models=set(),
        now_fn=lambda: now,
    )
    assert report["apply"] is True
    assert len(report["deleted"]) == 1
    assert report["deleted"][0]["repo_id"] == "org/stale"
    assert report["freed_gb"] >= 0.5  # 600 MB == 0.585 GB
    # Файл удалён, fresh остался
    assert not stale_dir.exists()
    assert fresh_dir.exists()


# ---- 7: active_models guard prevents deletion ----------------------------


def test_active_models_guard_blocks_deletion(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    protected_dir = _make_hf_model(
        hub,
        repo_dir="models--mlx-community--whisper-large-v3-mlx",
        payload_bytes=600 * 1024 * 1024,
        mtime=1_000_000.0,  # very stale
    )
    now = 1_000_000.0 + 100 * 86400
    report = hf.run_cleanup(
        cache_roots=[hub],
        min_age_days=30,
        min_size_mb=500,
        apply=True,
        active_models={"mlx-community/whisper-large-v3-mlx"},
        now_fn=lambda: now,
    )
    # Нет ни кандидатов, ни удалений
    assert report["stale_candidates_count"] == 0
    assert report["deleted"] == []
    # Файл цел
    assert protected_dir.exists()
    assert "mlx-community/whisper-large-v3-mlx" in report["active_models_protected"]
