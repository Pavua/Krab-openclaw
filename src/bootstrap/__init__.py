# Bootstrap: env validation and runtime lifecycle (Фаза 4 / 6.2 декомпозиция main.py)
from .db_corruption_guard import preflight_known_dbs
from .env_and_lock import validate_config
from .pyrogram_patch import (
    apply_pyrogram_session_guard,
    apply_pyrogram_sqlite_hardening,
    install_pyrogram_patches,
)
from .runtime import run_app
from .sentry_init import init_sentry

# Apply pyrofork SQLite hardening на уровне импорта bootstrap.
# Важно: это происходит ДО того как userbot_bridge / swarm-клиенты создадут
# первый pyrogram.Client(), чтобы все sessions открывались уже с WAL.
apply_pyrogram_sqlite_hardening()
# Защита от NoneType.to_bytes race в Session.start (Sentry PYTHON-FASTAPI-6G).
# Re-enabled 29.04.2026 после rewrite: теперь патчим **сами accessor-методы**
# (api_id/dc_id/...), а не _get — pyrogram inspect.stack() chain не ломается,
# SQL колонка всегда корректная.
apply_pyrogram_session_guard()
# S69 Wave 1: sync add_handler patch (default OFF — env opt-in).
# Активируется только если KRAB_PYROGRAM_PATCH_ADD_HANDLER=1. S68 W1 барьер
# в _start_client_serialized остаётся primary fix пока патч валидируется.
install_pyrogram_patches()

__all__ = [
    "validate_config",
    "run_app",
    "init_sentry",
    "apply_pyrogram_sqlite_hardening",
    "apply_pyrogram_session_guard",
    "install_pyrogram_patches",
    "preflight_known_dbs",
]
