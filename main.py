"""CLI: python main.py --input data/samples/univ_a.pdf --rankings QS THE"""
import argparse
import sys

from core.config import get_settings
from core.orchestrator import run_pipeline
from core.report import save_outputs
from core.schemas import LEVELS, TaskRequest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Многоагентное каскадирование KPI университета")
    parser.add_argument("--input", required=True, help="PDF (или .md/.txt) стратегического документа")
    parser.add_argument("--rankings", nargs="+", default=["QS", "THE"], choices=["QS", "THE"],
                        help="целевые глобальные рейтинги")
    parser.add_argument("--levels", nargs="+", default=LEVELS, choices=LEVELS,
                        help="уровни каскада (по умолчанию все)")
    args = parser.parse_args(argv)

    settings = get_settings()
    request = TaskRequest(input_path=args.input, rankings=args.rankings, levels=args.levels)
    print(f"Задача {request.task_id} | провайдер LLM: {settings.llm_provider}")

    result = run_pipeline(request, settings, on_progress=lambda text: print("  " + text, flush=True))
    folder = save_outputs(result, settings.output_dir)

    print(f"\nСтатус: {result.status} — {result.message}")
    print(f"Результат: {folder / 'result.json'}")
    print(f"Отчёт:     {folder / 'report.md'}")
    print(f"Лог:       {result.log_path}")
    print(f"Нагрузка:  python scripts/load_report.py {result.log_path}")
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
