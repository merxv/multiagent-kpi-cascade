"""Загрузка настроек из файла .env.

Ключи API здесь НЕ хранятся: SDK провайдеров читают их из переменных окружения
(ANTHROPIC_API_KEY, OPENAI_API_KEY), которые python-dotenv подгружает из .env.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Корень проекта — относительно него считаются все пути из .env
ROOT_DIR = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    llm_provider: str
    anthropic_model: str
    openai_model: str
    ollama_model: str
    ollama_url: str
    # Лимиты
    max_steps: int
    max_revisions: int
    task_timeout_sec: float
    llm_timeout_sec: float
    llm_max_retries: int
    llm_max_tokens: int
    # Пути
    db_path: Path
    log_dir: Path
    output_dir: Path
    chroma_dir: Path
    rankings_dir: Path
    # Векторный поиск
    embedding_backend: str
    embedding_model: str


def _path(name: str, default: str) -> Path:
    """Путь из переменной окружения; относительный путь считаем от корня проекта."""
    p = Path(os.getenv(name, default))
    return p if p.is_absolute() else ROOT_DIR / p


def get_settings() -> Settings:
    """Читает настройки при каждом вызове (удобно для тестов, которые меняют окружение)."""
    load_dotenv(ROOT_DIR / ".env", override=False)
    return Settings(
        llm_provider=os.getenv("LLM_PROVIDER", "mock").lower(),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
        ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
        max_steps=int(os.getenv("MAX_STEPS", "30")),
        max_revisions=int(os.getenv("MAX_REVISIONS", "2")),
        task_timeout_sec=float(os.getenv("TASK_TIMEOUT_SEC", "600")),
        llm_timeout_sec=float(os.getenv("LLM_TIMEOUT_SEC", "60")),
        llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "3")),
        llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "8000")),
        db_path=_path("DB_PATH", "data/state.db"),
        log_dir=_path("LOG_DIR", "logs"),
        output_dir=_path("OUTPUT_DIR", "outputs"),
        chroma_dir=_path("CHROMA_DIR", "data/chroma"),
        rankings_dir=_path("RANKINGS_DIR", "data/rankings"),
        embedding_backend=os.getenv("EMBEDDING_BACKEND", "auto").lower(),
        embedding_model=os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2"),
    )
