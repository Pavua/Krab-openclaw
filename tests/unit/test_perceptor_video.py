"""Тесты video frame extraction в perceptor (Bug 5 follow-up)."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from src.modules import perceptor as perceptor_mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeCompleted:
    """Имитация subprocess.CompletedProcess с настраиваемым побочным эффектом."""

    def __init__(self, returncode: int = 0, stderr: bytes = b"", stdout: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


def _make_fake_video(tmp_path: Path) -> Path:
    """Создаёт файл-плейсхолдер для тестов (содержимое не важно — ffmpeg замокан)."""
    video = tmp_path / "test.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return video


# ---------------------------------------------------------------------------
# extract_video_frames
# ---------------------------------------------------------------------------


def test_extract_calls_ffmpeg_and_returns_frames(tmp_path, monkeypatch):
    """Уверяемся что ffmpeg вызывается и созданные jpg возвращаются как bytes."""
    video = _make_fake_video(tmp_path)

    captured: dict[str, list[str]] = {}

    def fake_which(name: str, path: str | None = None) -> str | None:
        return f"/opt/homebrew/bin/{name}"

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        # ffprobe path — отдаём длительность; ffmpeg path — создаём jpg
        if cmd[0].endswith("ffprobe"):
            return _FakeCompleted(returncode=0, stdout="6.0\n")
        # ffmpeg: вытаскиваем шаблон вывода и создаём 2 jpg
        out_pattern = cmd[-1]
        # шаблон вида .../frame_%03d.jpg → подставим 1,2
        template = out_pattern.replace("%03d", "{idx:03d}")
        for idx in (1, 2):
            Path(template.format(idx=idx)).write_bytes(b"JPEGDATA" + bytes([idx]))
        return _FakeCompleted(returncode=0)

    monkeypatch.setattr(perceptor_mod.shutil, "which", fake_which)
    monkeypatch.setattr(perceptor_mod.subprocess, "run", fake_run)

    frames = perceptor_mod.extract_video_frames(str(video), max_frames=2)

    assert len(frames) == 2
    assert all(isinstance(f, bytes) and f.startswith(b"JPEGDATA") for f in frames)
    # ffmpeg действительно вызывался
    assert any("ffmpeg" in str(part) for part in captured["cmd"])


def test_extract_caps_at_max_frames(tmp_path, monkeypatch):
    """Если ffmpeg сгенерил больше — функция возвращает не более max_frames."""
    video = _make_fake_video(tmp_path)

    monkeypatch.setattr(
        perceptor_mod.shutil,
        "which",
        lambda name, path=None: f"/opt/homebrew/bin/{name}",
    )

    def fake_run(cmd, **kwargs):
        if cmd[0].endswith("ffprobe"):
            return _FakeCompleted(returncode=0, stdout="10.0\n")
        out_pattern = cmd[-1]
        template = out_pattern.replace("%03d", "{idx:03d}")
        # Создаём 5 кадров несмотря на max_frames=2
        for idx in range(1, 6):
            Path(template.format(idx=idx)).write_bytes(b"F" + bytes([idx]))
        return _FakeCompleted(returncode=0)

    monkeypatch.setattr(perceptor_mod.subprocess, "run", fake_run)

    frames = perceptor_mod.extract_video_frames(str(video), max_frames=2)

    assert len(frames) == 2


def test_extract_graceful_when_ffmpeg_missing(tmp_path, monkeypatch, caplog):
    """Нет ffmpeg в PATH — возвращаем [] и не падаем."""
    video = _make_fake_video(tmp_path)

    monkeypatch.setattr(perceptor_mod.shutil, "which", lambda name, path=None: None)

    # subprocess.run не должен вызываться
    def boom(*a, **kw):  # pragma: no cover
        raise AssertionError("subprocess.run не должен вызываться без ffmpeg")

    monkeypatch.setattr(perceptor_mod.subprocess, "run", boom)

    frames = perceptor_mod.extract_video_frames(str(video), max_frames=3)

    assert frames == []


def test_extract_single_frame_video_edge_case(tmp_path, monkeypatch):
    """Edge case: видео без определимой длительности (gif/sticker) → fallback ветка."""
    video = _make_fake_video(tmp_path)

    monkeypatch.setattr(
        perceptor_mod.shutil,
        "which",
        lambda name, path=None: f"/opt/homebrew/bin/{name}",
    )

    def fake_run(cmd, **kwargs):
        if cmd[0].endswith("ffprobe"):
            # ffprobe не смог определить длительность
            return _FakeCompleted(returncode=1, stdout="")
        # ffmpeg fallback — без -vf fps. Создаём ровно 1 кадр.
        out_pattern = cmd[-1]
        template = out_pattern.replace("%03d", "{idx:03d}")
        Path(template.format(idx=1)).write_bytes(b"SINGLE")
        # Проверка что мы попали в fallback ветку: нет '-vf' в cmd
        assert "-vf" not in cmd, "ожидалась fallback ветка без -vf fps"
        return _FakeCompleted(returncode=0)

    monkeypatch.setattr(perceptor_mod.subprocess, "run", fake_run)

    frames = perceptor_mod.extract_video_frames(str(video), max_frames=3)

    assert frames == [b"SINGLE"]


def test_extract_invalid_strategy_returns_empty(tmp_path, monkeypatch):
    """Неизвестная стратегия → [] без вызова ffmpeg."""
    video = _make_fake_video(tmp_path)
    monkeypatch.setattr(
        perceptor_mod.shutil,
        "which",
        lambda name, path=None: f"/opt/homebrew/bin/{name}",
    )
    frames = perceptor_mod.extract_video_frames(
        str(video), max_frames=3, sample_strategy="bogus"
    )
    assert frames == []


def test_extract_missing_video_file(tmp_path, monkeypatch):
    """Несуществующий файл → [] без падения."""
    monkeypatch.setattr(
        perceptor_mod.shutil,
        "which",
        lambda name, path=None: f"/opt/homebrew/bin/{name}",
    )
    frames = perceptor_mod.extract_video_frames(
        str(tmp_path / "nope.mp4"), max_frames=3
    )
    assert frames == []


def test_extract_ffmpeg_timeout_returns_empty(tmp_path, monkeypatch):
    """ffmpeg висит → TimeoutExpired → [] без падения."""
    video = _make_fake_video(tmp_path)
    monkeypatch.setattr(
        perceptor_mod.shutil,
        "which",
        lambda name, path=None: f"/opt/homebrew/bin/{name}",
    )

    def fake_run(cmd, **kwargs):
        if cmd[0].endswith("ffprobe"):
            return _FakeCompleted(returncode=0, stdout="5.0\n")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=60)

    monkeypatch.setattr(perceptor_mod.subprocess, "run", fake_run)
    frames = perceptor_mod.extract_video_frames(str(video), max_frames=2)
    assert frames == []


# ---------------------------------------------------------------------------
# process_video_message
# ---------------------------------------------------------------------------


def test_process_video_message_aggregates_frames_and_caption(tmp_path, monkeypatch):
    """process_video_message склеивает caption + описания кадров."""
    video = _make_fake_video(tmp_path)

    # extract_video_frames замокаем напрямую, чтобы не возиться с ffmpeg
    def fake_extract(path, *, max_frames=3, sample_strategy="uniform"):
        return [b"frame1", b"frame2"]

    monkeypatch.setattr(perceptor_mod, "extract_video_frames", fake_extract)

    async def describer(frame: bytes, idx: int) -> str:
        return f"кадр-{idx}-{len(frame)}"

    text = asyncio.run(
        perceptor_mod.process_video_message(
            str(video),
            caption="Привет",
            frame_describer=describer,
        )
    )

    assert "Подпись к видео: Привет" in text
    assert "кадр-0-6" in text
    assert "кадр-1-6" in text


def test_process_video_message_empty_when_no_frames_no_caption(tmp_path, monkeypatch):
    """Без кадров и без caption — пустая строка."""
    video = _make_fake_video(tmp_path)

    monkeypatch.setattr(
        perceptor_mod, "extract_video_frames", lambda *a, **kw: []
    )
    text = asyncio.run(perceptor_mod.process_video_message(str(video), caption=None))
    assert text == ""


def test_process_video_message_caption_only_when_extraction_fails(
    tmp_path, monkeypatch
):
    """Caption есть, кадры пусты → fallback с пометкой."""
    video = _make_fake_video(tmp_path)
    monkeypatch.setattr(
        perceptor_mod, "extract_video_frames", lambda *a, **kw: []
    )
    text = asyncio.run(
        perceptor_mod.process_video_message(str(video), caption="мяу")
    )
    assert "Подпись к видео: мяу" in text
    assert "не удалось извлечь" in text
