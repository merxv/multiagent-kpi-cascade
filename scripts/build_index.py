"""Индексирует методологии рейтингов (data/rankings/*.md) в ChromaDB.

Запуск: python scripts/build_index.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import get_settings  # noqa: E402
from tools.vector_search import VectorSearch, build_index  # noqa: E402

if __name__ == "__main__":
    settings = get_settings()
    stats = build_index(settings)
    print(f"Проиндексировано файлов: {stats['files']}, фрагментов: {stats['chunks']}, "
          f"эмбеддер: {stats['embedding_backend']}, папка: {settings.chroma_dir}")
    # Быстрая проверка поиска
    for hit in VectorSearch(settings).search("цитируемость публикаций преподавателей", k=3):
        print(f"  {hit['score']:.3f}  {hit['ranking']} / {hit['section']}")
