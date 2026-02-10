# -*- coding: utf-8 -*-
"""
RAG Engine v2.0 (Retrieval-Augmented Generation).
Долгосрочная память бота на основе ChromaDB с embeddings.

Зачем: Хранит и извлекает знания, факты, саммари и результаты 
анализа изображений. Позволяет боту "вспоминать" информацию.

Что нового в v2.0:
- Decay (устаревание): документы старше TTL помечаются как устаревшие
- Категоризация: документы разделены по источникам (vision, learning, summary, document)
- Статистика по категориям
- Bulk-операции для массовой индексации
- Экспорт/Импорт знаний

Связь: Используется в model_manager.py для обогащения промптов,
в main.py для команды !learn, в handle_vision для OCR-to-RAG.
"""

import os
import json
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import chromadb
from chromadb.utils import embedding_functions

logger = logging.getLogger("RAG")


class RAGEngine:
    """Движок RAG v2.0 с decay и категоризацией."""
    
    # TTL по умолчанию — 90 дней (в секундах)
    DEFAULT_TTL = 90 * 24 * 60 * 60
    
    # Категории документов
    CATEGORIES = ["learning", "vision", "summary", "document", "web", "general"]
    
    def __init__(self, db_path="artifacts/memory/chroma_db"):
        self.db_path = db_path
        self.client = chromadb.PersistentClient(path=db_path)
        
        # Используем дефолтную модель от Chroma (all-MiniLM-L6-v2)
        self.emb_fn = embedding_functions.DefaultEmbeddingFunction()
        
        self.collection = self.client.get_or_create_collection(
            name="krab_knowledge",
            embedding_function=self.emb_fn
        )
        logger.info(f"✅ RAG Engine v2.0 Initialized. Collection size: {self.collection.count()}")

    def add_document(self, text: str, metadata: Optional[dict] = None, 
                     doc_id: Optional[str] = None, category: str = "general",
                     ttl_days: Optional[int] = None) -> Optional[str]:
        """
        Индексация нового документа с метаданными и TTL.
        
        Args:
            text: Текст для индексации
            metadata: Дополнительные метаданные
            doc_id: Уникальный ID (авто-генерация если None)
            category: Категория документа (learning, vision, summary, document, web)
            ttl_days: Время жизни в днях (None = DEFAULT_TTL)
        """
        try:
            # Генерация уникального ID 
            doc_id = doc_id or f"doc_{category}_{int(time.time())}_{self.collection.count()}"
            
            # Расчёт TTL
            ttl_seconds = (ttl_days * 86400) if ttl_days else self.DEFAULT_TTL
            expires_at = time.time() + ttl_seconds
            
            # Обогащаем метаданные
            enriched_metadata = {
                "source": category,
                "indexed_at": datetime.now().isoformat(),
                "indexed_timestamp": time.time(),
                "expires_at": expires_at,
                "ttl_days": ttl_days or (self.DEFAULT_TTL // 86400),
                **(metadata or {})
            }
            
            self.collection.add(
                documents=[text],
                metadatas=[enriched_metadata],
                ids=[doc_id]
            )
            logger.info(f"Indexed document: {doc_id} (category={category}, ttl={ttl_days or 90}d)")
            return doc_id
            
        except Exception as e:
            logger.error(f"Failed to index document: {e}")
            return None

    def query(self, text: str, n_results: int = 3, 
              category: Optional[str] = None,
              include_expired: bool = False) -> str:
        """
        Поиск релевантных кусков текста.
        
        Args:
            text: Запрос для поиска
            n_results: Количество результатов
            category: Фильтр по категории (None = все)
            include_expired: Включать ли устаревшие документы
        """
        try:
            # Формируем фильтры
            where_filter = {}
            if category:
                where_filter["source"] = category
            
            results = self.collection.query(
                query_texts=[text],
                n_results=n_results * 2,  # Берём больше, потом фильтруем expired
                where=where_filter if where_filter else None
            )
            
            # Фильтрация устаревших
            documents = results.get('documents', [[]])[0]
            metadatas = results.get('metadatas', [[]])[0]
            
            if not include_expired:
                now = time.time()
                filtered = []
                for doc, meta in zip(documents, metadatas):
                    expires = meta.get('expires_at', float('inf'))
                    if now < expires:
                        filtered.append(doc)
                documents = filtered[:n_results]
            else:
                documents = documents[:n_results]
            
            if documents:
                return "\n---\n".join(documents)
            return ""
            
        except Exception as e:
            logger.error(f"RAG Query error: {e}")
            return ""

    def query_with_scores(self, text: str, n_results: int = 5) -> List[Dict[str, Any]]:
        """
        Поиск с возвратом скоров релевантности.
        Полезно для дебага и визуализации.
        """
        try:
            results = self.collection.query(
                query_texts=[text],
                n_results=n_results,
                include=["documents", "metadatas", "distances"]
            )
            
            output = []
            docs = results.get('documents', [[]])[0]
            metas = results.get('metadatas', [[]])[0]
            dists = results.get('distances', [[]])[0]
            
            for doc, meta, dist in zip(docs, metas, dists):
                output.append({
                    "text": doc[:200],  # Превью
                    "score": round(1 - dist, 3),  # Конвертируем distance в similarity
                    "category": meta.get("source", "unknown"),
                    "indexed_at": meta.get("indexed_at", "?"),
                    "expired": time.time() > meta.get("expires_at", float('inf'))
                })
            
            return output
            
        except Exception as e:
            logger.error(f"RAG scored query error: {e}")
            return []

    def cleanup_expired(self) -> int:
        """
        Удаляет устаревшие (expired) документы из базы.
        Вызывается периодически через Scheduler.
        
        Returns:
            int: Количество удалённых документов
        """
        try:
            # Получаем все документы
            all_data = self.collection.get(include=["metadatas"])
            
            now = time.time()
            expired_ids = []
            
            for doc_id, meta in zip(all_data['ids'], all_data['metadatas']):
                expires = meta.get('expires_at', float('inf'))
                if now > expires:
                    expired_ids.append(doc_id)
            
            if expired_ids:
                self.collection.delete(ids=expired_ids)
                logger.info(f"🧹 RAG Cleanup: удалено {len(expired_ids)} устаревших документов")
            
            return len(expired_ids)
            
        except Exception as e:
            logger.error(f"RAG cleanup error: {e}")
            return 0

    def get_stats(self) -> Dict[str, Any]:
        """Расширенная статистика по базе знаний."""
        try:
            total = self.collection.count()
            
            # Статистика по категориям
            category_stats = {}
            expired_count = 0
            
            if total > 0:
                all_data = self.collection.get(include=["metadatas"])
                now = time.time()
                
                for meta in all_data['metadatas']:
                    cat = meta.get('source', 'unknown')
                    category_stats[cat] = category_stats.get(cat, 0) + 1
                    
                    if now > meta.get('expires_at', float('inf')):
                        expired_count += 1
            
            return {
                "count": total,
                "path": self.db_path,
                "categories": category_stats,
                "expired": expired_count,
                "active": total - expired_count
            }
            
        except Exception as e:
            logger.error(f"RAG stats error: {e}")
            return {"count": 0, "path": self.db_path, "error": str(e)}

    def bulk_add(self, items: List[Dict[str, str]], category: str = "general") -> int:
        """
        Массовая индексация документов.
        
        Args:
            items: Список словарей с ключами "text" и опциональным "id"
            category: Категория для всех документов
            
        Returns:
            int: Количество успешно добавленных
        """
        added = 0
        for item in items:
            doc_id = self.add_document(
                text=item.get("text", ""),
                metadata=item.get("metadata"),
                doc_id=item.get("id"),
                category=category
            )
            if doc_id:
                added += 1
        return added

    def export_knowledge(self, output_path: str = "artifacts/exports/rag_export.json") -> str:
        """Экспорт всей базы знаний в JSON."""
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            all_data = self.collection.get(include=["documents", "metadatas"])
            
            export = {
                "exported_at": datetime.now().isoformat(),
                "total": len(all_data['ids']),
                "documents": []
            }
            
            for doc_id, doc, meta in zip(all_data['ids'], all_data['documents'], all_data['metadatas']):
                export["documents"].append({
                    "id": doc_id,
                    "text": doc,
                    "metadata": meta
                })
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(export, f, ensure_ascii=False, indent=2)
            
            logger.info(f"📦 RAG Export: {len(export['documents'])} документов -> {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"RAG export error: {e}")
            return ""

    def format_stats_report(self) -> str:
        """Форматированный отчёт для Telegram."""
        stats = self.get_stats()
        
        report = (
            f"**🧠 RAG Knowledge Base v2.0**\n\n"
            f"📊 **Всего документов:** {stats['count']}\n"
            f"✅ **Активных:** {stats.get('active', stats['count'])}\n"
            f"⏰ **Устаревших:** {stats.get('expired', 0)}\n\n"
        )
        
        cats = stats.get('categories', {})
        if cats:
            report += "**📂 По категориям:**\n"
            cat_icons = {
                "learning": "📚", "vision": "👁️", "summary": "📝",
                "document": "📄", "web": "🌐", "general": "📌"
            }
            for cat, count in sorted(cats.items(), key=lambda x: x[1], reverse=True):
                icon = cat_icons.get(cat, "🔹")
                report += f"  {icon} {cat}: {count}\n"
        
        return report
