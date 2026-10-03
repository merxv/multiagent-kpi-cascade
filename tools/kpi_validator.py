"""kpi_validator — детерминированная (без LLM) проверка полноты KPI.

Проверяется: метод расчёта, единица, целевое значение, период, уровень,
родительский KPI (кроме верхнего уровня) и то, что родитель стоит ровно на уровень выше.
"""
from core.schemas import LEVEL_NAMES_RU, KPISet


def _empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def validate_kpi_set(kpi_set: KPISet, levels: list[str], min_per_level: int = 3) -> dict:
    """Возвращает отчёт: {'total', 'valid', 'invalid': {kpi_id: [проблемы]}, 'per_level', 'set_problems'}."""
    by_id = {k.id: k for k in kpi_set.kpis}
    invalid: dict[str, list[str]] = {}
    set_problems: list[str] = []

    if len(by_id) != len(kpi_set.kpis):
        set_problems.append("есть повторяющиеся id KPI")

    for kpi in kpi_set.kpis:
        problems = []
        if _empty(kpi.method):
            problems.append("нет метода расчёта (method)")
        if _empty(kpi.unit):
            problems.append("нет единицы измерения (unit)")
        if _empty(kpi.target):
            problems.append("нет целевого значения (target)")
        if _empty(kpi.period):
            problems.append("нет периода (period)")
        if kpi.level not in levels:
            problems.append(f"недопустимый уровень '{kpi.level}' (допустимо: {', '.join(levels)})")
        elif kpi.level != levels[0]:
            # Для всех уровней, кроме верхнего, нужен родитель уровнем выше
            if _empty(kpi.parent_id):
                problems.append("нет родительского KPI (parent_id)")
            elif kpi.parent_id not in by_id:
                problems.append(f"родительский KPI '{kpi.parent_id}' не найден")
            else:
                expected = levels[levels.index(kpi.level) - 1]
                if by_id[kpi.parent_id].level != expected:
                    problems.append(f"родитель '{kpi.parent_id}' должен быть на уровне '{expected}'")
        if problems:
            invalid[kpi.id] = problems

    per_level = {lvl: len(kpi_set.by_level(lvl)) for lvl in levels}
    for lvl, n in per_level.items():
        if n < min_per_level:
            set_problems.append(f"на уровне «{LEVEL_NAMES_RU[lvl]}» {n} KPI, нужно не менее {min_per_level}")

    return {
        "total": len(kpi_set.kpis),
        "valid": len(kpi_set.kpis) - len(invalid),
        "invalid": invalid,
        "per_level": per_level,
        "set_problems": set_problems,
    }


class KpiValidator:
    name = "kpi_validator"

    def validate(self, kpi_set: KPISet, levels: list[str]) -> dict:
        return validate_kpi_set(kpi_set, levels)
