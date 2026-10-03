"""Reviewer — независимо проверяет каскад KPI и матрицу связей и выносит вердикт."""
from agents.base import BaseAgent, to_json
from core.schemas import (AlignmentMatrix, CoverageReport, KPISet, RankingIndicators,
                          ReviewIssue, ReviewReport, StrategicGoals)

BIG_WEIGHT = 10.0  # индикатор с весом ≥10% обязательно должен быть покрыт KPI


def coverage_issues(cov: CoverageReport, indicators: RankingIndicators) -> list[ReviewIssue]:
    """Замечания, которые следуют из детерминированного расчёта покрытия (без LLM)."""
    issues = []
    if cov.uncovered_goals:
        issues.append(ReviewIssue(addressee="KPIDesigner", priority="high", category="цели без KPI",
                                  description="Нет KPI для стратегических целей: " + ", ".join(cov.uncovered_goals),
                                  related_ids=cov.uncovered_goals))
    if cov.orphan_kpis or cov.unlinked_kpis:
        ids = cov.orphan_kpis + cov.unlinked_kpis
        issues.append(ReviewIssue(addressee="KPIDesigner", priority="high", category="KPI-сироты",
                                  description="KPI не связаны ни с одной стратегической целью: " + ", ".join(ids),
                                  related_ids=ids))
    big = [i for i in indicators.indicators if i.id in cov.uncovered_indicators and i.weight >= BIG_WEIGHT]
    if big:
        issues.append(ReviewIssue(addressee="KPIDesigner", priority="high",
                                  category="неучтённые индикаторы",
                                  description="Не покрыты индикаторы с большим весом: "
                                              + ", ".join(f"{i.id} ({i.weight:g}%)" for i in big),
                                  related_ids=[i.id for i in big]))
    if cov.cascade_gaps:
        issues.append(ReviewIssue(addressee="KPIDesigner", priority="high", category="разрывы каскада",
                                  description="; ".join(cov.cascade_gaps), related_ids=[]))
    return issues


class Reviewer(BaseAgent):
    name = "Reviewer"
    slug = "reviewer"
    tool_names = ("coverage_calculator", "state_store")
    result_type = "review"

    def run(self, payload: dict) -> ReviewReport:
        goals = StrategicGoals.model_validate(payload["goals"])
        indicators = RankingIndicators.model_validate(payload["indicators"])
        kpi_set = KPISet.model_validate(payload["kpis"])
        matrix = AlignmentMatrix.model_validate(payload["matrix"])
        levels: list[str] = payload["levels"]
        iteration: int = payload.get("iteration", 1)

        # Независимый пересчёт покрытия — не доверяем цифрам AlignmentMapper
        cov = self.call_tool("coverage_calculator", "calculate", goals=goals, indicators=indicators,
                             kpi_set=kpi_set, links=matrix.links, levels=levels)
        user = (
            f"Университет: {goals.university}\n"
            f"Итерация проверки: {iteration}\n\n"
            f"Стратегические цели:\n{to_json([g.model_dump() for g in goals.goals])}\n\n"
            "Индикаторы рейтингов:\n"
            + to_json([{"id": i.id, "name": i.name, "weight": i.weight} for i in indicators.indicators])
            + f"\n\nKPI каскада:\n{to_json([k.model_dump() for k in kpi_set.kpis])}\n\n"
            f"Матрица связей:\n{to_json([l.model_dump() for l in matrix.links])}\n\n"
            f"Расчёт покрытия (coverage_calculator):\n{to_json(cov)}\n\n"
            "Проверь результат и вынеси вердикт. Верни JSON по формату из инструкции."
        )
        report = self.ask_llm(user, ReviewReport)
        report.iteration = iteration

        # Детерминированные замечания добавляем всегда; при серьёзных проблемах
        # одобрение невозможно, даже если LLM их пропустил
        auto = coverage_issues(cov, indicators)
        report.issues = auto + report.issues
        if any(i.priority == "high" for i in report.issues):
            report.verdict = "needs_revision"

        self.save("review_report", report)
        return report
