# Bootstrap: env validation and runtime lifecycle (Фаза 4 / 6.2 декомпозиция main.py)
from .env_and_lock import validate_config
from .runtime import run_app

__all__ = ["validate_config", "run_app"]
