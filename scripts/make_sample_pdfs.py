"""Генерирует PDF из markdown-примеров стратегий (data/samples/*.md -> *.pdf).

Используется fpdf2 со шрифтом DejaVu (поддерживает кириллицу).
Поддерживается упрощённая разметка: заголовки (#), курсив (*...*), абзацы.
"""
import sys
from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parent.parent
FONT_DIRS = [Path("/usr/share/fonts/truetype/dejavu"), Path("C:/Windows/Fonts"), Path("/Library/Fonts")]


def find_font(name: str) -> Path:
    for d in FONT_DIRS:
        if (d / name).exists():
            return d / name
    sys.exit(f"Не найден шрифт {name}. Установите шрифты DejaVu (пакет fonts-dejavu).")


def md_to_pdf(md_path: Path, pdf_path: Path) -> None:
    pdf = FPDF(format="A4")
    pdf.set_margins(20, 20, 20)
    pdf.add_font("DejaVu", "", str(find_font("DejaVuSans.ttf")))
    pdf.add_font("DejaVu", "B", str(find_font("DejaVuSans-Bold.ttf")))
    pdf.add_page()
    sizes = {"# ": 16, "## ": 13, "### ": 11.5}
    for line in md_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            pdf.ln(2)
            continue
        prefix = next((p for p in ("### ", "## ", "# ") if line.startswith(p)), None)
        if prefix:
            pdf.set_font("DejaVu", "B", sizes[prefix])
            pdf.multi_cell(0, 7, line[len(prefix):])
            pdf.ln(1)
        else:
            pdf.set_font("DejaVu", "", 10.5)
            pdf.multi_cell(0, 5.5, line.strip("*"))
            pdf.ln(1)
    pdf.output(str(pdf_path))


if __name__ == "__main__":
    for md in sorted((ROOT / "data" / "samples").glob("*.md")):
        out = md.with_suffix(".pdf")
        md_to_pdf(md, out)
        print(f"{md.name} -> {out.name}")
