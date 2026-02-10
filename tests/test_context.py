
import pytest
import os
import shutil
import json
from src.core.context_manager import ContextKeeper

# Тестовая директория для памяти
TEST_MEMORY_DIR = ".test_brain"

@pytest.fixture
def context_keeper():
    """Фикстура для ContextKeeper с временной директорией."""
    # Используем base_path вместо root_dir
    keeper = ContextKeeper(base_path=TEST_MEMORY_DIR)
    yield keeper
    # Очистка после теста
    if os.path.exists(TEST_MEMORY_DIR):
        shutil.rmtree(TEST_MEMORY_DIR)

def test_initialization(context_keeper):
    """Проверка создания корневой директории."""
    assert os.path.exists(TEST_MEMORY_DIR)
    # base_path is a Path object
    assert str(context_keeper.base_path) == TEST_MEMORY_DIR

def test_get_chat_storage(context_keeper):
    """Проверка генерации пути для конкретного чата."""
    path = context_keeper.get_chat_storage_path(12345)
    # path is a Path object, convert to string
    # We expect just the directory, e.g. .test_brain/12345
    assert str(path).endswith("12345")
    assert TEST_MEMORY_DIR in str(path)

def test_save_and_retrieve_message(context_keeper):
    """Проверка сохранения и чтения сообщений."""
    chat_id = 999
    msg1 = {"user": "test_user", "text": "Hello"}
    msg2 = {"role": "assistant", "text": "Hi in response"}
    
    # Сохраняем
    context_keeper.save_message(chat_id, msg1)
    context_keeper.save_message(chat_id, msg2)
    
    # Читаем
    history = context_keeper.get_recent_context(chat_id, limit=10)
    
    assert len(history) == 2
    assert history[0]['user'] == "test_user"
    assert history[1]['role'] == "assistant"
    assert history[1]['text'] == "Hi in response"

def test_limit_context(context_keeper):
    """Проверка лимита истории."""
    chat_id = 888
    for i in range(20):
        context_keeper.save_message(chat_id, {"msg_id": i})
        
    history = context_keeper.get_recent_context(chat_id, limit=5)
    assert len(history) == 5
    assert history[-1]['msg_id'] == 19
