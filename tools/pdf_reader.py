"""pdf_reader — извлечение текста из PDF (а также .txt/.md для тестов) и разбиение на фрагменты."""
import logging
from pathlib import Path

from tools import ToolError


# pypdf печатает в консоль предупреждения о битых файлах; мы сообщаем о них сами через ToolError
logging.getLogger("pypdf").setLevel(logging.ERROR)


class PdfReader:
    name = "pdf_reader"

    def read(self, path: str, chunk_size: int = 1500, overlap: int = 200) -> dict:
        """Читает документ и возвращает {'text', 'pages', 'chunks'}."""
        p = Path(path)
        if not p.exists():
            raise ToolError(f"Файл не найден: {path}")
        suffix = p.suffix.lower()
        if suffix == ".pdf":
            text, pages = self._read_pdf(p)
        elif suffix in (".txt", ".md"):
            text, pages = p.read_text(encoding="utf-8"), 1
        else:
            raise ToolError(f"Неподдерживаемый формат файла: {suffix} (нужен .pdf, .txt или .md)")
        if len(text.strip()) < 50:
            raise ToolError(f"Документ пуст или содержит слишком мало текста: {path}")
        return {"text": text, "pages": pages, "chunks": self.chunk(text, chunk_size, overlap)}

    @staticmethod
    def _read_pdf(p: Path) -> tuple[str, int]:
        from pypdf import PdfReader as _PypdfReader
        try:
            reader = _PypdfReader(str(p))
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as e:  # pypdf бросает разные исключения на битых файлах
            raise ToolError(f"Не удалось прочитать PDF «{p.name}» (файл повреждён?): {e}") from e
        return "\n".join(pages), len(pages)

    @staticmethod
    def chunk(text: str, size: int = 1500, overlap: int = 200) -> list[str]:
        """Режет текст на фрагменты ~size символов с перекрытием overlap."""
        chunks, start = [], 0
        while start < len(text):
            chunks.append(text[start:start + size])
            start += size - overlap
        return chunks
