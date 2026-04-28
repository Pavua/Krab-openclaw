# Bootstrap: env validation and runtime lifecycle (Фаза 4 / 6.2 декомпозиция main.py)
from .db_corruption_guard import preflight_known_dbs
from .env_and_lock import validate_config
from .pyrogram_patch import apply_pyrogram_session_guard, apply_pyrogram_sqlite_hardening
from .runtime import run_app
from .sentry_init import init_sentry

# Apply pyrofork SQLite hardening на уровне импорта bootstrap.
# Важно: это происходит ДО того как userbot_bridge / swarm-клиенты создадут
# первый pyrogram.Client(), чтобы все sessions открывались уже с WAL.
apply_pyrogram_sqlite_hardening()
# Защита от NoneType.to_bytes race в Session.start (Sentry PYTHON-FASTAPI-6G).
apply_pyrogram_session_guard()

__all__ = [
    "validate_config",
    "run_app",
    "init_sentry",
    "apply_pyrogram_sqlite_hardening",
    "apply_pyrogram_session_guard",
    "preflight_known_dbs",
]
