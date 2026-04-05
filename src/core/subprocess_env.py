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


def clean_subprocess_env() -> dict[str, str]:
    """Return a copy of os.environ without macOS malloc debugging keys."""
    env = os.environ.copy()
    for key in _MALLOC_DEBUG_KEYS:
        env.pop(key, None)
    return env
