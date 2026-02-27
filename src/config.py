"""
Конфигурация проекта Краб
"""
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Загрузить .env файл
load_dotenv()


class Config:
    """Центральная конфигурация приложения"""
    
    # Paths
    BASE_DIR: Path = Path(__file__).parent.parent
    
    # Telegram
    TELEGRAM_API_ID: int = int(os.getenv("TELEGRAM_API_ID", "0"))
    TELEGRAM_API_HASH: str = os.getenv("TELEGRAM_API_HASH", "")
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")  # Optional fallback
    TELEGRAM_SESSION_NAME: str = os.getenv("TELEGRAM_SESSION_NAME", "kraab")
    
    # OpenClaw
    OPENCLAW_URL: str = os.getenv("OPENCLAW_URL", "http://127.0.0.1:18792")
    OPENCLAW_TOKEN: str = os.getenv("OPENCLAW_GATEWAY_TOKEN", os.getenv("OPENCLAW_TOKEN", ""))
    
    # LM Studio
    LM_STUDIO_URL: str = os.getenv("LM_STUDIO_URL", "http://192.168.0.171:1234")
    
    # Gemini (fallback)
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")
    GEMINI_MODELS: list[str] = [
        "google/gemini-pro-latest",  # Stable
        "google/gemini-2.0-flash",   # Fast & New
        "google/gemini-flash-latest", # Fallback
    ]
    MODEL: str = os.getenv("MODEL", "google/gemini-pro-latest")
    
    # Skills / APIs
    BRAVE_SEARCH_API_KEY: Optional[str] = os.getenv("BRAVE_SEARCH_API_KEY")
    
    # Memory limits
    MAX_RAM_GB: int = int(os.getenv("MAX_RAM_GB", "24"))
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Routing: force_cloud полностью обходит локальный путь (Фаза 2.2)
    FORCE_CLOUD: bool = os.getenv("FORCE_CLOUD", "0").strip().lower() in ("1", "true", "yes")
    
    # User settings
    OWNER_USERNAME: str = "@yung_nagato"
    ALLOWED_USERS: list[str] = [u.strip().lstrip("@") for u in os.getenv("ALLOWED_USERS", "pablito,admin").split(",") if u.strip()]
    TRIGGER_PREFIXES: list[str] = [p.strip() for p in os.getenv("TRIGGER_PREFIXES", "!краб,@краб,/краб,Краб,,краб,").split(",") if p.strip()]
    
    @classmethod
    def validate(cls) -> list[str]:
        """Проверяет обязательные настройки и возвращает список ошибок"""
        errors = []
        
        if not cls.TELEGRAM_API_ID:
            errors.append("TELEGRAM_API_ID не установлен")
        if not cls.TELEGRAM_API_HASH:
            errors.append("TELEGRAM_API_HASH не установлен")
            
        return errors
    
    @classmethod
    def is_valid(cls) -> bool:
        """Проверяет валидность конфигурации"""
        return len(cls.validate()) == 0

    @classmethod
    def update_setting(cls, key: str, value: str) -> bool:
        """Обновляет настройку в памяти и в .env файле"""
        try:
            key = key.upper()
            # Обновляем в текущем процессе
            if hasattr(cls, key):
                if key == "ALLOWED_USERS":
                    cls.ALLOWED_USERS = [u.strip().lstrip("@") for u in value.split(",") if u.strip()]
                elif key == "TRIGGER_PREFIXES":
                    cls.TRIGGER_PREFIXES = [p.strip() for p in value.split(",") if p.strip()]
                elif key == "MAX_RAM_GB":
                    cls.MAX_RAM_GB = int(value)
                elif key == "MODEL":
                    cls.MODEL = value
                elif key == "FORCE_CLOUD":
                    cls.FORCE_CLOUD = value.strip().lower() in ("1", "true", "yes")
                elif key == "GEMINI_API_KEY":
                    cls.GEMINI_API_KEY = value
                elif key == "BRAVE_SEARCH_API_KEY":
                    cls.BRAVE_SEARCH_API_KEY = value
                
            # Обновляем .env файл для сохранения между перезапусками
            env_path = cls.BASE_DIR / ".env"
            if not env_path.exists():
                # Попробуем создать из примера если нет (бэкап)
                example_path = cls.BASE_DIR / ".env.example"
                if example_path.exists():
                    import shutil
                    shutil.copy(example_path, env_path)
                else:
                    with open(env_path, "w") as f:
                        f.write("# Generated .env\n")

            lines = env_path.read_text().splitlines()
            found = False
            new_lines = []
            for line in lines:
                if line.strip().startswith(f"{key}="):
                    new_lines.append(f"{key}={value}")
                    found = True
                else:
                    new_lines.append(line)
            
            if not found:
                # Добавляем новую настройку в конец, но перед пустыми строками если можно
                new_lines.append(f"{key}={value}")
                
            env_path.write_text("\n".join(new_lines) + "\n")
            return True
        except Exception as e:
            print(f"Error updating config: {e}")
            return False


# Синглтон для удобства
config = Config()
