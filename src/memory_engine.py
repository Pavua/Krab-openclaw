
import chromadb
from chromadb.utils import embedding_functions
from structlog import get_logger
import os
from .config import config

logger = get_logger(__name__)

class MemoryManager:
    """
    Управляет долгосрочной памятью бота используя Vector Database (ChromaDB).
    Позволяет сохранять факты и искать их по смыслу.
    """
    def __init__(self):
        self.persist_directory = os.path.join(config.BASE_DIR, "memory_db")
        
        # Используем локальный или in-memory клиент
        self.client = chromadb.PersistentClient(path=self.persist_directory)
        
        # Используем стандартный модель (all-MiniLM-L6-v2) - он легкий и быстрый
        # ChromaDB скачает его автоматически при первом запуске
        self.embedding_fn = embedding_functions.DefaultEmbeddingFunction()
        
        self.collection = self.client.get_or_create_collection(
            name="krab_facts",
            embedding_function=self.embedding_fn
        )
        logger.info("memory_manager_initialized", path=self.persist_directory)

    def save_fact(self, text: str, metadata: dict = None) -> bool:
        """Сохраняет факт в память"""
        try:
            # Генерируем ID на основе хеша текста или просто рандом
            import uuid
            fact_id = str(uuid.uuid4())
            
            self.collection.add(
                documents=[text],
                metadatas=[metadata or {"source": "user"}],
                ids=[fact_id]
            )
            logger.info("fact_saved", text=text[:50])
            return True
        except (ValueError, RuntimeError, OSError, TypeError) as e:
            logger.error("save_fact_error", error=str(e))
            return False

    def recall(self, query: str, n_results: int = 3) -> str:
        """Поиск релевантных фактов"""
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results
            )
            
            if not results['documents'][0]:
                return ""
            
            # Форматируем найденные факты
            facts = results['documents'][0]
            return "\n".join([f"- {fact}" for fact in facts])
        except (ValueError, RuntimeError, OSError, KeyError, IndexError, TypeError) as e:
            logger.error("recall_error", error=str(e))
            return ""

    def count(self) -> int:
        return self.collection.count()

# Синглтон
memory_manager = MemoryManager()
