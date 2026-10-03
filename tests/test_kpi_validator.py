from core.schemas import LEVELS, KPI, KPISet
from tools.kpi_validator import validate_kpi_set


def make_chain(prefix: str) -> list[KPI]:
    """Цепочка из 4 корректных KPI: университет -> факультет -> кафедра -> преподаватель."""
    kpis, parent = [], None
    for level in LEVELS:
        kid = f"{prefix}-{level}"
        kpis.append(KPI(id=kid, name=kid, level=level, method="a / b", unit="%",
                        target=10, period="год", parent_id=parent))
        parent = kid
    return kpis


def full_set() -> KPISet:
    return KPISet(kpis=make_chain("A") + make_chain("B") + make_chain("C"))


def test_valid_set_passes():
    report = validate_kpi_set(full_set(), LEVELS)
    assert report["valid"] == report["total"] == 12
    assert report["invalid"] == {} and report["set_problems"] == []


def test_missing_fields_are_reported():
    kpis = full_set()
    k = kpis.kpis[1]  # A-faculty
    k.unit, k.target, k.period, k.method = "", None, " ", ""
    report = validate_kpi_set(kpis, LEVELS)
    problems = " ".join(report["invalid"][k.id])
    for field in ("unit", "target", "period", "method"):
        assert field in problems


def test_parent_rules():
    kpis = full_set()
    kpis.kpis[1].parent_id = None  # у факультета нет родителя
    kpis.kpis[2].parent_id = "NOPE"  # несуществующий родитель
    kpis.kpis[3].parent_id = "A-university"  # родитель «через уровень»
    report = validate_kpi_set(kpis, LEVELS)
    assert "нет родительского KPI" in report["invalid"]["A-faculty"][0]
    assert "не найден" in report["invalid"]["A-department"][0]
    assert "должен быть на уровне" in report["invalid"]["A-teacher"][0]
    assert "A-university" not in report["invalid"]  # у верхнего уровня родитель не нужен


def test_minimum_per_level_and_unknown_level():
    kpis = KPISet(kpis=make_chain("A"))
    kpis.kpis[0].level = "rectorate"
    report = validate_kpi_set(kpis, LEVELS)
    assert any("недопустимый уровень" in p for p in report["invalid"]["A-university"])
    assert len(report["set_problems"]) == 4  # на каждом уровне меньше 3 KPI
