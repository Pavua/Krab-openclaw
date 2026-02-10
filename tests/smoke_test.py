# -*- coding: utf-8 -*-
"""
Smoke-тест Krab v6.0 — отправляет команду через Pyrogram и проверяет ответ.

Для работы нужен ДРУГОЙ Telegram-сессия (не та что использует бот).
Вместо этого — используем Telethon или прямой API-вызов.

Но поскольку бот — юзербот и реагирует на СВОИ сообщения тоже,
мы можем просто проверить через лог, что хендлеры зарегистрированы.
"""

import os
import sys
import asyncio
import importlib

# Добавляем корень проекта
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_all_imports():
    """Проверяем что все модули импортируются без ошибок."""
    modules = [
        "src.core.model_manager",
        "src.core.context_manager",
        "src.core.error_handler",
        "src.core.rate_limiter",
        "src.core.config_manager",
        "src.core.security_manager",
        "src.core.logger_setup",
        "src.core.persona_manager",
        "src.core.rag_engine",
        "src.core.scheduler",
        "src.core.agent_manager",
        "src.core.tool_handler",
        "src.core.mcp_client",
        "src.modules.perceptor",
        "src.modules.screen_catcher",
        "src.utils.black_box",
        "src.utils.web_scout",
        "src.utils.system_monitor",
        "src.handlers",
        "src.handlers.auth",
        "src.handlers.commands",
        "src.handlers.ai",
        "src.handlers.media",
        "src.handlers.tools",
        "src.handlers.system",
        "src.handlers.scheduling",
        "src.handlers.mac",
        "src.handlers.rag",
        "src.handlers.persona",
    ]
    
    passed = 0
    failed = 0
    errors = []
    
    for mod_name in modules:
        try:
            importlib.import_module(mod_name)
            passed += 1
            print(f"  ✅ {mod_name}")
        except Exception as e:
            failed += 1
            errors.append((mod_name, str(e)))
            print(f"  ❌ {mod_name}: {e}")
    
    return passed, failed, errors


def test_config_reads_env():
    """Проверяем что конфиг читается из .env."""
    from dotenv import load_dotenv
    load_dotenv()
    
    checks = {
        "TELEGRAM_API_ID": os.getenv("TELEGRAM_API_ID"),
        "TELEGRAM_API_HASH": os.getenv("TELEGRAM_API_HASH"),
        "TELEGRAM_SESSION_NAME": os.getenv("TELEGRAM_SESSION_NAME"),
        "OWNER_USERNAME": os.getenv("OWNER_USERNAME"),
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY"),
    }
    
    passed = 0
    failed = 0
    
    for key, val in checks.items():
        if val and val.strip():
            passed += 1
            # Маскируем значение
            masked = val[:4] + "..." if len(val) > 4 else val
            print(f"  ✅ {key} = {masked}")
        else:
            failed += 1
            print(f"  ❌ {key} = NOT SET")
    
    return passed, failed


def test_router_init():
    """Проверяем инициализацию ModelRouter."""
    from src.core.model_manager import ModelRouter
    
    router = ModelRouter(config=os.environ)
    
    checks = [
        ("models.chat", "chat" in router.models),
        ("models.thinking", "thinking" in router.models),
        ("gemini_key", bool(router.gemini_key)),
        ("lm_studio_url", bool(router.lm_studio_url)),
    ]
    
    passed = 0
    for name, ok in checks:
        if ok:
            passed += 1
            print(f"  ✅ {name}")
        else:
            print(f"  ❌ {name}")
    
    return passed, len(checks) - passed


def test_auth_functions():
    """Проверяем auth-модуль."""
    from src.handlers.auth import get_owner, get_allowed_users
    
    owner = get_owner()
    allowed = get_allowed_users()
    
    checks = [
        ("owner не пустой", bool(owner)),
        ("owner без @", "@" not in owner),
        ("owner в allowed", owner in allowed),
        ("allowed >= 1", len(allowed) >= 1),
    ]
    
    passed = 0
    for name, ok in checks:
        if ok:
            passed += 1
            print(f"  ✅ {name}: {owner if 'owner' in name else allowed}")
        else:
            print(f"  ❌ {name}")
    
    return passed, len(checks) - passed


def test_rag_engine():
    """Проверяем RAG Engine."""
    from src.core.rag_engine import RAGEngine
    
    rag = RAGEngine()
    
    # Добавляем тестовый документ
    rag.add_document("Это тестовый документ для проверки RAG.", 
                     metadata={"source": "smoke_test"})
    
    # Ищем
    result = rag.query("тестовый документ")
    
    ok = result and len(result) > 0
    if ok:
        print(f"  ✅ RAG query работает: {result[:60]}...")
        return 1, 0
    else:
        print(f"  ❌ RAG query вернул пустой результат")
        return 0, 1


def test_security_manager():
    """Проверяем SecurityManager."""
    from src.core.security_manager import SecurityManager
    
    sec = SecurityManager(owner_username="testowner")
    
    checks = [
        ("owner", sec.owner == "testowner"),
        ("stealth off", not sec.stealth_mode),
    ]
    
    passed = 0
    for name, ok in checks:
        if ok:
            passed += 1
            print(f"  ✅ {name}")
        else:
            print(f"  ❌ {name}")
    
    return passed, len(checks) - passed


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    print("=" * 60)
    print("🦀 KRAB v6.0 SMOKE TEST")
    print("=" * 60)
    
    total_passed = 0
    total_failed = 0
    
    # 1. Imports
    print("\n📦 1. Module Imports:")
    p, f, _ = test_all_imports()
    total_passed += p
    total_failed += f
    
    # 2. Config
    print("\n⚙️  2. Environment Config:")
    p, f = test_config_reads_env()
    total_passed += p
    total_failed += f
    
    # 3. Router
    print("\n🧠 3. ModelRouter Init:")
    p, f = test_router_init()
    total_passed += p
    total_failed += f
    
    # 4. Auth
    print("\n🔐 4. Auth Module:")
    p, f = test_auth_functions()
    total_passed += p
    total_failed += f
    
    # 5. RAG
    print("\n📚 5. RAG Engine:")
    p, f = test_rag_engine()
    total_passed += p
    total_failed += f
    
    # 6. Security
    print("\n🛡️  6. SecurityManager:")
    p, f = test_security_manager()
    total_passed += p
    total_failed += f
    
    # Summary
    print("\n" + "=" * 60)
    total = total_passed + total_failed
    print(f"🏆 ИТОГО: {total_passed}/{total} passed ({total_passed/total*100:.0f}%)")
    if total_failed == 0:
        print("✅ ALL SMOKE TESTS PASSED!")
    else:
        print(f"❌ {total_failed} TESTS FAILED")
    print("=" * 60)
    
    sys.exit(0 if total_failed == 0 else 1)
