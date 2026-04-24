# Bootstrap: env validation and runtime lifecycle (Фаза 4 / 6.2 декомпозиция main.py)
from .env_and_lock import validate_config
from .pyrogram_patch import apply_pyrogram_sqlite_hardening
from .runtime import run_app
from .sentry_init import init_sentry

# Apply pyrofork SQLite hardening на уровне импорта bootstrap.
# Важно: это происходит ДО того как userbot_bridge / swarm-клиенты создадут
# первый pyrogram.Client(), чтобы все sessions открывались уже с WAL.
apply_pyrogram_sqlite_hardening()

__all__ = [
    "validate_config",
    "run_app",
    "init_sentry",
    "apply_pyrogram_sqlite_hardening",
]
