from core.schemas import (LEVELS, AlignmentLink, KPI, KPISet, RankingIndicator, RankingIndicators,
                          StrategicGoal, StrategicGoals)
from tools.coverage_calculator import calculate_coverage

GOALS = StrategicGoals(university="U", mission="M", goals=[
    StrategicGoal(id=g, title=g, description="", evidence="e") for g in ("G1", "G2", "G3")])
INDICATORS = RankingIndicators(indicators=[
    RankingIndicator(id="QS-AR", ranking="QS", name="AR", weight=60),
    RankingIndicator(id="QS-CPF", ranking="QS", name="CPF", weight=40),
    RankingIndicator(id="THE-TEACH", ranking="THE", name="Teaching", weight=100),
])
LEVELS2 = ["university", "faculty"]


def kpi(id, level, parent=None):
    return KPI(id=id, name=id, level=level, method="m", unit="%", target=1, period="год", parent_id=parent)


def test_full_and_partial_coverage():
    kpis = KPISet(kpis=[kpi("U1", "university"), kpi("F1", "faculty", "U1"), kpi("U2", "university")])
    links = [
        AlignmentLink(kpi_id="U1", goal_ids=["G1", "G2"], indicator_ids=["QS-AR"], strength=3),
        AlignmentLink(kpi_id="F1", goal_ids=["G1"], indicator_ids=["THE-TEACH"], strength=2),
        AlignmentLink(kpi_id="U2", goal_ids=[], indicator_ids=[], strength=1),  # сирота
    ]
    cov = calculate_coverage(GOALS, INDICATORS, kpis, links, LEVELS2)
    assert cov.goal_coverage == round(2 / 3, 3)
    assert cov.uncovered_goals == ["G3"]
    assert cov.indicator_weight_coverage == {"QS": 60.0, "THE": 100.0}
    assert cov.uncovered_indicators == ["QS-CPF"]
    assert cov.orphan_kpis == ["U2"]
    assert cov.unlinked_kpis == []
    # У U2 нет дочернего KPI на уровне факультета — это разрыв каскада
    assert any(g.startswith("U2:") for g in cov.cascade_gaps)


def test_unlinked_kpi_and_missing_parent():
    kpis = KPISet(kpis=[kpi("U1", "university"), kpi("F1", "faculty", "GHOST")])
    links = [AlignmentLink(kpi_id="U1", goal_ids=["G1", "G2", "G3"], indicator_ids=[], strength=1),
             AlignmentLink(kpi_id="XX", goal_ids=["G1"], indicator_ids=["QS-CPF"], strength=1)]
    cov = calculate_coverage(GOALS, INDICATORS, kpis, links, LEVELS2)
    assert cov.unlinked_kpis == ["F1"]
    assert cov.indicator_weight_coverage["QS"] == 0.0  # связь с несуществующим KPI не учитывается
    assert any("GHOST" in g for g in cov.cascade_gaps)
    assert any(g.startswith("U1:") for g in cov.cascade_gaps)


def test_bottom_level_needs_no_children():
    kpis = KPISet(kpis=[kpi("U1", "university")])
    links = [AlignmentLink(kpi_id="U1", goal_ids=["G1"], strength=1)]
    cov = calculate_coverage(GOALS, INDICATORS, kpis, links, ["university"])
    assert cov.cascade_gaps == []
    assert LEVELS[0] == "university"
