"""Clean subprocess environment to suppress macOS malloc debugging noise."""

from __future__ import annotations

import os

_MALLOC_DEBUG_KEYS = (
    "MallocStackLogging",
    "MallocStackLoggingNoCompact",
    "MallocScribble",
    "MallocGuardEdges",
    "MallocCheckHeapEach",
)

# LaunchAgent запускает Krab со стерильным PATH (`/usr/bin:/bin:/usr/sbin:/sbin`),
# в котором нет homebrew. Это ломает любой subprocess, ищущий бинарь по имени:
# ffmpeg (voice_engine), node (mcp_relay), playwright/chromium (mercadona),
# tor, openclaw — всё это лежит в /opt/homebrew/bin. Дополняем PATH здесь,
# чтобы не дублировать workaround в каждом call site.
_HOMEBREW_PATH_PREFIXES = (
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
)


def clean_subprocess_env() -> dict[str, str]:
    """Return a copy of os.environ without macOS malloc debugging keys.

    Дополнительно гарантирует, что homebrew-пути присутствуют в PATH,
    чтобы subprocess'ы под LaunchAgent могли находить ffmpeg/node/etc.
    """
    env = os.environ.copy()
    for key in _MALLOC_DEBUG_KEYS:
        env.pop(key, None)

    current_path = env.get("PATH", "")
    path_entries = current_path.split(os.pathsep) if current_path else []
    missing = [p for p in _HOMEBREW_PATH_PREFIXES if p not in path_entries]
    if missing:
        env["PATH"] = (
            os.pathsep.join([*missing, *path_entries]) if path_entries else os.pathsep.join(missing)
        )
    return env
