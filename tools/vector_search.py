"""vector_search — поиск по методологиям рейтингов в векторной БД ChromaDB.

Индекс строится из файлов data/rankings/*.md (по одному фрагменту на раздел «## ...»).
Эмбеддинги считаем сами и передаём в Chroma готовыми векторами, поэтому
понятно, какая модель используется:
  * sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2) — основной вариант;
  * hashing — офлайн-запасной вариант, если модель нельзя скачать (нет интернета).
Название бэкенда сохраняется в метаданных коллекции: поиск всегда использует
тот же эмбеддер, что и индексация.
"""
import math
import re
import zlib
from pathlib import Path

from tools import ToolError

COLLECTION = "ranking_methodologies"


# ---------------------------------------------------------------------------
# Эмбеддеры
# ---------------------------------------------------------------------------
class HashingEmbedder:
    """Простой офлайн-эмбеддер: символьные триграммы слов хешируются в вектор фиксированной длины.

    Похожие по написанию тексты (общие корни слов) получают близкие векторы —
    этого достаточно для поиска по небольшому корпусу методологий.
    """
    name = "hashing"
    dim = 1024

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * self.dim
            for word in re.findall(r"\w+", text.lower().replace("ё", "е")):
                w = f" {word} "
                for i in range(len(w) - 2):
                    vec[zlib.crc32(w[i:i + 3].encode("utf-8")) % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vectors.append([x / norm for x in vec])
        return vectors


class SentenceEmbedder:
    """Мультиязычная модель sentence-transformers (документы на русском)."""
    name = "sentence-transformers"

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # тяжёлый импорт — только по требованию
        self.model = SentenceTransformer(model_name)

    def encode(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()


_EMBEDDER_CACHE: dict[str, object] = {}


def get_embedder(backend: str, model_name: str):
    """backend: auto | sentence-transformers | hashing."""
    key = f"{backend}:{model_name}"
    if key in _EMBEDDER_CACHE:
        return _EMBEDDER_CACHE[key]
    if backend == "hashing":
        embedder = HashingEmbedder()
    elif backend == "sentence-transformers":
        embedder = SentenceEmbedder(model_name)
    elif backend == "auto":
        try:
            embedder = SentenceEmbedder(model_name)
        except Exception as e:
            print(f"[vector_search] модель {model_name} недоступна ({type(e).__name__}); "
                  f"используется офлайн-эмбеддер hashing")
            embedder = HashingEmbedder()
    else:
        raise ToolError(f"Неизвестный EMBEDDING_BACKEND='{backend}'")
    _EMBEDDER_CACHE[key] = embedder
    return embedder


# ---------------------------------------------------------------------------
# Индексация
# ---------------------------------------------------------------------------
def _client(chroma_dir: Path):
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    return chromadb.PersistentClient(path=str(chroma_dir),
                                     settings=ChromaSettings(anonymized_telemetry=False))


def split_methodology(path: Path) -> list[dict]:
    """Режет файл методологии на фрагменты по заголовкам второго уровня."""
    text = path.read_text(encoding="utf-8")
    ranking = path.stem.upper()  # qs.md -> QS
    title = text.splitlines()[0].lstrip("# ").strip()
    chunks = []
    for section in re.split(r"\n(?=## )", text):
        heading = section.splitlines()[0].lstrip("# ").strip()
        chunks.append({"text": f"{title}. {section.strip()}", "ranking": ranking, "section": heading})
    return chunks


def build_index(settings) -> dict:
    """(Пере)строит индекс методологий рейтингов. Возвращает статистику."""
    files = sorted(Path(settings.rankings_dir).glob("*.md"))
    if not files:
        raise ToolError(f"Нет файлов методологий в {settings.rankings_dir}")
    chunks = [c for f in files for c in split_methodology(f)]
    embedder = get_embedder(settings.embedding_backend, settings.embedding_model)

    client = _client(settings.chroma_dir)
    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass  # коллекции ещё не было
    col = client.create_collection(COLLECTION, embedding_function=None,
                                   metadata={"hnsw:space": "cosine", "embedding_backend": embedder.name})
    col.add(
        ids=[f"{c['ranking']}-{i}" for i, c in enumerate(chunks)],
        documents=[c["text"] for c in chunks],
        embeddings=embedder.encode([c["text"] for c in chunks]),
        metadatas=[{"ranking": c["ranking"], "section": c["section"]} for c in chunks],
    )
    return {"files": len(files), "chunks": len(chunks), "embedding_backend": embedder.name}


# ---------------------------------------------------------------------------
# Инструмент
# ---------------------------------------------------------------------------
class VectorSearch:
    name = "vector_search"

    def __init__(self, settings):
        self.settings = settings
        self._collection = None

    def _get_collection(self):
        if self._collection is None:
            client = _client(self.settings.chroma_dir)
            try:
                col = client.get_collection(COLLECTION)
            except Exception:
                col = None
            if col is None or col.count() == 0:
                # Индекса нет — строим автоматически (то же делает scripts/build_index.py)
                build_index(self.settings)
                col = client.get_collection(COLLECTION)
            self._collection = col
        return self._collection

    def search(self, query: str, ranking: str | None = None, k: int = 5) -> list[dict]:
        """Возвращает k ближайших фрагментов: [{'text', 'ranking', 'section', 'score'}]."""
        col = self._get_collection()
        backend = col.metadata.get("embedding_backend", "hashing")
        embedder = get_embedder(backend, self.settings.embedding_model)
        res = col.query(query_embeddings=embedder.encode([query]), n_results=k,
                        where={"ranking": ranking} if ranking else None)
        return [
            {"text": doc, "ranking": meta["ranking"], "section": meta["section"],
             "score": round(1 - dist, 3)}
            for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
        ]
