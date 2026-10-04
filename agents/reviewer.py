"""Reviewer — независимо проверяет каскад KPI и матрицу связей и выносит вердикт.

Разделение полномочий:
  * одобрение блокируют только ПРОВЕРЯЕМЫЕ замечания — их считает coverage_calculator без LLM:
    цели без KPI, KPI без связи с целями, непокрытые индикаторы с большим весом, разрывы каскада;
  * замечания LLM (перекосы, нереалистичные цели, размытые формулировки) — экспертные рекомендации:
    они попадают в отчёт и передаются на доработку, но сами по себе одобрение не блокируют.
Так итог не зависит от ошибок рассуждения LLM (особенно небольших локальных моделей).
"""
from agents.base import BaseAgent, to_json
from core.schemas import (AlignmentMatrix, CoverageReport, KPISet, RankingIndicators,
                          ReviewIssue, ReviewReport, StrategicGoals)

BIG_WEIGHT = 10.0  # индикатор с весом ≥10% обязательно должен быть покрыт KPI


def coverage_issues(cov: CoverageReport, indicators: RankingIndicators) -> list[ReviewIssue]:
    """Блокирующие замечания, которые следуют из детерминированного расчёта покрытия."""
    issues = []
    if cov.uncovered_goals:
        issues.append(ReviewIssue(addressee="KPIDesigner", priority="high", category="цели без KPI",
                                  description="Нет KPI для стратегических целей: " + ", ".join(cov.uncovered_goals),
                                  related_ids=cov.uncovered_goals, blocking=True))
    big = [i for i in indicators.indicators if i.id in cov.uncovered_indicators and i.weight >= BIG_WEIGHT]
    if big:
        issues.append(ReviewIssue(addressee="KPIDesigner", priority="high", category="неучтённые индикаторы",
                                  description="Не покрыты индикаторы с большим весом: "
                                              + ", ".join(f"{i.id} ({i.weight:g}%)" for i in big),
                                  related_ids=[i.id for i in big], blocking=True))
    if cov.cascade_gaps:
        ids = [gap.split(":")[0] for gap in cov.cascade_gaps]
        issues.append(ReviewIssue(addressee="KPIDesigner", priority="high", category="разрывы каскада",
                                  description="; ".join(cov.cascade_gaps), related_ids=ids, blocking=True))
    if cov.orphan_kpis or cov.unlinked_kpis:
        # KPI есть, но в матрице он не связан с целями — это прежде всего работа AlignmentMapper
        ids = cov.orphan_kpis + cov.unlinked_kpis
        issues.append(ReviewIssue(addressee="AlignmentMapper", priority="high", category="KPI-сироты",
                                  description="KPI не связаны ни с одной стратегической целью: " + ", ".join(ids),
                                  related_ids=ids, blocking=True))
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
            + f"\n\nKPI каскада:\n{to_json([k.model_dump(exclude={'rationale'}) for k in kpi_set.kpis])}\n\n"
            f"Матрица связей:\n{to_json([l.model_dump(exclude={'rationale'}) for l in matrix.links])}\n\n"
            f"Расчёт покрытия (coverage_calculator):\n{to_json(cov)}\n\n"
            "Проверь результат и вынеси вердикт. Верни JSON по формату из инструкции."
        )
        report = self.ask_llm(user, ReviewReport)
        report.iteration = iteration
        report.llm_verdict = report.verdict
        for issue in report.issues:
            issue.blocking = False  # замечания LLM — только рекомендации

        # Итоговый вердикт определяют только проверяемые (блокирующие) замечания
        auto = coverage_issues(cov, indicators)
        report.issues = auto + report.issues
        report.verdict = "needs_revision" if auto else "approved"

        self.save("review_report", report)
        return report
