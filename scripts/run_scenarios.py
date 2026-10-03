"""Прогон тестовых сценариев из tests/scenarios/*.json и таблица «пройден / не пройден».

Каждый сценарий — JSON-файл:
  {"name", "description", "input", "rankings", "levels"?, "env"?, "expected": {...}}
По умолчанию сценарии выполняются на провайдере mock без OpenAlex (детерминированно);
поле "env" сценария переопределяет переменные окружения.

Запуск:
  python scripts/run_scenarios.py
  python scripts/run_scenarios.py 03 06     # только сценарии, имя файла которых начинается с 03 или 06
"""
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import get_settings  # noqa: E402
from core.orchestrator import run_pipeline  # noqa: E402
from core.schemas import TaskRequest, TaskResult  # noqa: E402

SCENARIOS_DIR = ROOT / "tests" / "scenarios"
DEFAULT_ENV = {"LLM_PROVIDER": "mock", "OPENALEX_ENABLED": "false"}


@contextmanager
def scenario_env(overrides: dict):
    """Временно подменяет переменные окружения и восстанавливает их после сценария."""
    saved = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def check_expectations(result: TaskResult, expected: dict) -> list[str]:
    """Сравнивает результат с ожиданиями. Возвращает список расхождений (пустой — сценарий пройден)."""
    errors = []
    if result.status != expected["status"]:
        errors.append(f"статус {result.status}, ожидался {expected['status']} ({result.message})")
    if "revisions" in expected and result.revisions != expected["revisions"]:
        errors.append(f"доработок {result.revisions}, ожидалось {expected['revisions']}")
    if "message_contains" in expected and expected["message_contains"] not in result.message:
        errors.append(f"в сообщении нет «{expected['message_contains']}»: {result.message}")
    if "min_goals" in expected:
        n = len(result.goals.goals) if result.goals else 0
        if n < expected["min_goals"]:
            errors.append(f"целей {n}, ожидалось ≥{expected['min_goals']}")
    if "min_goal_coverage" in expected:
        cov = result.matrix.coverage.goal_coverage if result.matrix and result.matrix.coverage else 0
        if cov < expected["min_goal_coverage"]:
            errors.append(f"покрытие целей {cov:.0%}, ожидалось ≥{expected['min_goal_coverage']:.0%}")
    if "indicator_rankings" in expected:
        got = sorted({i.ranking for i in result.indicators.indicators}) if result.indicators else []
        if got != sorted(expected["indicator_rankings"]):
            errors.append(f"рейтинги индикаторов {got}, ожидалось {expected['indicator_rankings']}")
    if "kpi_levels" in expected:
        got = sorted({k.level for k in result.kpis.kpis}) if result.kpis else []
        if got != sorted(expected["kpi_levels"]):
            errors.append(f"уровни KPI {got}, ожидалось {expected['kpi_levels']}")
    if "mission_startswith" in expected:
        mission = result.goals.mission if result.goals else ""
        if not mission.startswith(expected["mission_startswith"]):
            errors.append(f"миссия не начинается с «{expected['mission_startswith']}»")
    if "issue_category" in expected:
        cats = [i.category for i in result.review.issues] if result.review else []
        if expected["issue_category"] not in cats:
            errors.append(f"нет замечания категории «{expected['issue_category']}» (есть: {cats})")
    return errors


def run_scenario(path: Path) -> tuple[bool, list[str], TaskResult]:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    with scenario_env({**DEFAULT_ENV, **spec.get("env", {})}):
        settings = get_settings()
        request = TaskRequest(input_path=str(ROOT / spec["input"]), rankings=spec["rankings"],
                              **({"levels": spec["levels"]} if "levels" in spec else {}))
        result = run_pipeline(request, settings)
    errors = check_expectations(result, spec["expected"])
    return not errors, errors, result


def scenario_files(prefixes: list[str] | None = None) -> list[Path]:
    files = sorted(SCENARIOS_DIR.glob("*.json"))
    if prefixes:
        files = [f for f in files if any(f.name.startswith(p) for p in prefixes)]
    return files


def main(argv: list[str]) -> int:
    files = scenario_files(argv or None)
    if not files:
        print("Сценарии не найдены.")
        return 1
    rows = []
    for f in files:
        spec = json.loads(f.read_text(encoding="utf-8"))
        ok, errors, result = run_scenario(f)
        rows.append((f.stem, spec["name"], spec["expected"]["status"], result.status, ok, errors))
        print(f"{'ПРОЙДЕН    ' if ok else 'НЕ ПРОЙДЕН '} {f.stem}")

    print(f"\n{'Сценарий':<28}{'Ожидалось':<12}{'Получено':<12}Итог")
    print("-" * 64)
    for sid, _, exp, got, ok, errors in rows:
        print(f"{sid:<28}{exp:<12}{got:<12}{'пройден' if ok else 'НЕ ПРОЙДЕН'}")
        for e in errors:
            print(f"    - {e}")
    passed = sum(r[4] for r in rows)
    print("-" * 64)
    print(f"Пройдено: {passed} из {len(rows)} ({passed / len(rows):.0%})")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
