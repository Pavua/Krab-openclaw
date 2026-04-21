"""Counters для anti-bot detection signals (Chado §1 P2).

Layers:
- canvas: canvas fingerprint challenges detected
- webgl: WebGL vendor/renderer probes
- webrtc: WebRTC IP leak attempts
- captcha: captcha encountered
- ratelimit: HTTP 429 from target
- blocked: explicit block response
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_counts: dict[str, int] = {}


def record_detection(layer: str) -> None:
    """Increment counter for a layer (idempotent safe)."""
    with _lock:
        _counts[layer] = _counts.get(layer, 0) + 1


def get_counts() -> dict[str, int]:
    """Return shallow copy of current counts."""
    with _lock:
        return dict(_counts)


def reset() -> None:
    """For tests."""
    with _lock:
        _counts.clear()
