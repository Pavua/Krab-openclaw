# -*- coding: utf-8 -*-
"""
STT Worker для Perceptor.

Что это:
Отдельный процесс-воркер для запуска MLX Whisper.

Зачем:
На части macOS-конфигураций MLX/Metal может завершаться SIGABRT (AGX assertion).
Если запускать STT внутри основного процесса, падает весь Krab.
Воркер изолирует этот риск: при аварии падает только дочерний процесс.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def _emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


def main() -> int:
    if len(sys.argv) < 4:
        return _emit({"ok": False, "error": "usage: <file_path> <model_name> <json_payload>"})

    file_path = str(sys.argv[1])
    model_name = str(sys.argv[2])
    raw_payload = str(sys.argv[3])

    try:
        options = json.loads(raw_payload)
    except Exception as exc:
        return _emit({"ok": False, "error": f"invalid_payload_json:{exc}"})

    if not isinstance(options, dict):
        return _emit({"ok": False, "error": "invalid_payload_type"})

    primary_kwargs = options.get("primary_kwargs", {})
    fallback_kwargs = options.get("fallback_kwargs", {})
    if not isinstance(primary_kwargs, dict):
        primary_kwargs = {}
    if not isinstance(fallback_kwargs, dict):
        fallback_kwargs = {}

    try:
        import mlx_whisper
    except Exception as exc:
        return _emit({"ok": False, "error": f"mlx_whisper_import_failed:{exc}"})

    try:
        result = mlx_whisper.transcribe(
            file_path,
            path_or_hf_repo=model_name,
            **primary_kwargs,
        )
    except TypeError:
        try:
            result = mlx_whisper.transcribe(
                file_path,
                path_or_hf_repo=model_name,
                **fallback_kwargs,
            )
        except Exception as exc:
            return _emit({"ok": False, "error": f"mlx_transcribe_fallback_failed:{exc}"})
    except Exception as exc:
        return _emit({"ok": False, "error": f"mlx_transcribe_failed:{exc}"})

    text = ""
    if isinstance(result, dict):
        text = str(result.get("text", "")).strip()
    return _emit({"ok": True, "text": text})


if __name__ == "__main__":
    raise SystemExit(main())
