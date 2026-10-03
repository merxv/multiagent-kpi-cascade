"""Общие фикстуры: все тесты работают на mock-провайдере, во временных папках, без интернета."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import get_settings  # noqa: E402


@pytest.fixture(scope="session")
def chroma_dir(tmp_path_factory) -> Path:
    # Индекс строится один раз на всю сессию тестов
    return tmp_path_factory.mktemp("chroma")


@pytest.fixture
def settings(tmp_path, chroma_dir, monkeypatch):
    env = {
        "LLM_PROVIDER": "mock",
        "EMBEDDING_BACKEND": "hashing",  # офлайн-эмбеддер: тестам не нужен интернет
        "DB_PATH": str(tmp_path / "state.db"),
        "LOG_DIR": str(tmp_path / "logs"),
        "OUTPUT_DIR": str(tmp_path / "outputs"),
        "CHROMA_DIR": str(chroma_dir),
        "MAX_STEPS": "30",
        "MAX_REVISIONS": "2",
        "LLM_MAX_RETRIES": "1",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return get_settings()
