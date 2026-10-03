"""coverage_calculator — детерминированный расчёт покрытия (без LLM).

Считает:
  * долю стратегических целей, на которые работает хотя бы один KPI;
  * долю суммарного веса индикаторов каждого рейтинга, покрытую KPI;
  * KPI-«сироты» (без связи с целями) и KPI, отсутствующие в матрице;
  * разрывы каскада (нет родителя / нет дочерних KPI на уровне ниже).
"""
from core.schemas import (LEVEL_NAMES_RU, AlignmentLink, CoverageReport, KPISet,
                          RankingIndicators, StrategicGoals)


def calculate_coverage(goals: StrategicGoals, indicators: RankingIndicators, kpi_set: KPISet,
                       links: list[AlignmentLink], levels: list[str]) -> CoverageReport:
    kpi_ids = {k.id for k in kpi_set.kpis}
    links = [l for l in links if l.kpi_id in kpi_ids]  # связи с несуществующими KPI игнорируем
    linked_goals = {g for l in links for g in l.goal_ids}
    linked_indicators = {i for l in links for i in l.indicator_ids}

    # Покрытие целей
    goal_ids = [g.id for g in goals.goals]
    uncovered_goals = [g for g in goal_ids if g not in linked_goals]
    goal_coverage = (len(goal_ids) - len(uncovered_goals)) / len(goal_ids) if goal_ids else 0.0

    # Покрытие веса индикаторов по каждому рейтингу
    weight_cov: dict[str, float] = {}
    for ranking in sorted({i.ranking for i in indicators.indicators}):
        items = [i for i in indicators.indicators if i.ranking == ranking]
        total = sum(i.weight for i in items)
        covered = sum(i.weight for i in items if i.id in linked_indicators)
        weight_cov[ranking] = round(100 * covered / total, 1) if total else 0.0
    uncovered_indicators = [i.id for i in indicators.indicators if i.id not in linked_indicators]

    # KPI без связей
    links_by_kpi = {l.kpi_id: l for l in links}
    unlinked = [k.id for k in kpi_set.kpis if k.id not in links_by_kpi]
    orphans = [k.id for k in kpi_set.kpis if k.id in links_by_kpi and not links_by_kpi[k.id].goal_ids]

    # Разрывы каскада
    gaps = []
    children = {}
    for k in kpi_set.kpis:
        if k.parent_id:
            children.setdefault(k.parent_id, []).append(k.id)
            if k.parent_id not in kpi_ids:
                gaps.append(f"{k.id}: родительский KPI {k.parent_id} не найден")
    for k in kpi_set.kpis:
        if k.level in levels and k.level != levels[-1] and k.id not in children:
            lower = LEVEL_NAMES_RU[levels[levels.index(k.level) + 1]]
            gaps.append(f"{k.id}: нет дочерних KPI на уровне «{lower}»")

    return CoverageReport(
        goal_coverage=round(goal_coverage, 3),
        uncovered_goals=uncovered_goals,
        indicator_weight_coverage=weight_cov,
        uncovered_indicators=uncovered_indicators,
        orphan_kpis=orphans,
        unlinked_kpis=unlinked,
        cascade_gaps=gaps,
    )


class CoverageCalculator:
    name = "coverage_calculator"

    def calculate(self, goals, indicators, kpi_set, links, levels) -> CoverageReport:
        return calculate_coverage(goals, indicators, kpi_set, links, levels)
