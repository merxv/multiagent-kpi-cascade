"""Отчёт о нагрузке агентов по логу прогона (правило: ни один агент не превышает 40%).

Считаются вызовы LLM (llm_call) и инструментов (tool_call) каждого агента.
Запуск:
  python scripts/load_report.py logs/<task_id>.jsonl
  python scripts/load_report.py            # последний лог в папке logs/
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 0.40


def compute_load(log_path: Path) -> list[dict]:
    """Возвращает строки таблицы: agent, llm, tools, total, share, over_limit."""
    counts: dict[str, dict[str, int]] = {}
    with Path(log_path).open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec["event"] not in ("llm_call", "tool_call"):
                continue
            c = counts.setdefault(rec["agent"], {"llm": 0, "tools": 0})
            c["llm" if rec["event"] == "llm_call" else "tools"] += 1
    grand = sum(c["llm"] + c["tools"] for c in counts.values()) or 1
    rows = []
    for agent, c in counts.items():
        total = c["llm"] + c["tools"]
        rows.append({"agent": agent, "llm": c["llm"], "tools": c["tools"], "total": total,
                     "share": total / grand, "over_limit": total / grand > LIMIT})
    return sorted(rows, key=lambda r: -r["total"])


def print_table(rows: list[dict]) -> None:
    print(f"{'Агент':<18}{'LLM':>6}{'Инстр.':>8}{'Всего':>7}{'Доля':>8}")
    print("-" * 47)
    for r in rows:
        mark = "  <-- ПРЕВЫШЕН ЛИМИТ 40%" if r["over_limit"] else ""
        print(f"{r['agent']:<18}{r['llm']:>6}{r['tools']:>8}{r['total']:>7}{r['share']:>8.1%}{mark}")
    print("-" * 47)
    print(f"{'ИТОГО':<18}{sum(r['llm'] for r in rows):>6}{sum(r['tools'] for r in rows):>8}"
          f"{sum(r['total'] for r in rows):>7}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        logs = sorted((ROOT / "logs").glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not logs:
            sys.exit("В папке logs/ нет логов. Сначала запустите main.py.")
        path = logs[-1]
    print(f"Лог: {path}\n")
    rows = compute_load(path)
    print_table(rows)
    over = [r["agent"] for r in rows if r["over_limit"]]
    if over:
        print(f"\nВНИМАНИЕ: лимит 40% превышен у агентов: {', '.join(over)}")
        sys.exit(1)
    print("\nOK: ни один агент не превышает 40% вызовов.")
