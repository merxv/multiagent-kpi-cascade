"""AlignmentMapper — строит матрицу связей «цель — индикатор — KPI» и считает покрытие."""
from agents.base import BaseAgent, to_json
from core.schemas import (AlignmentDraft, AlignmentMatrix, KPISet, RankingIndicators,
                          ReviewReport, StrategicGoals)


class AlignmentMapper(BaseAgent):
    name = "AlignmentMapper"
    slug = "alignment_mapper"
    tool_names = ("coverage_calculator", "state_store")

    def run(self, payload: dict) -> AlignmentMatrix:
        goals = StrategicGoals.model_validate(payload["goals"])
        indicators = RankingIndicators.model_validate(payload["indicators"])
        kpi_set = KPISet.model_validate(payload["kpis"])
        levels: list[str] = payload["levels"]

        kpis_short = [{"id": k.id, "name": k.name, "level": k.level, "parent_id": k.parent_id}
                      for k in kpi_set.kpis]
        user = (
            f"Университет: {goals.university}\n\n"
            f"Стратегические цели:\n{to_json([{'id': g.id, 'title': g.title} for g in goals.goals])}\n\n"
            "Индикаторы рейтингов:\n"
            + to_json([{"id": i.id, "name": i.name, "weight": i.weight} for i in indicators.indicators])
            + f"\n\nKPI каскада:\n{to_json(kpis_short)}\n\n"
        )
        # На доработке — замечания проверяющего к связям (например, KPI-сироты)
        if payload.get("review"):
            review = ReviewReport.model_validate(payload["review"])
            issues = [i for i in review.issues if i.addressee == "AlignmentMapper"]
            if issues:
                user += ("## Замечания проверяющего к связям\n"
                         + "\n".join(f"- {i.description}" for i in issues)
                         + "\nПроверь эти KPI особенно внимательно: у каждого KPI, который работает на цель, "
                           "должна быть указана хотя бы одна цель.\n\n")
        user += "Для каждого KPI укажи связанные цели и индикаторы. Верни JSON по формату из инструкции."

        goal_ids = {g.id for g in goals.goals}
        indicator_ids = {i.id for i in indicators.indicators}
        kpi_ids = [k.id for k in kpi_set.kpis]

        rankings = {i.ranking for i in indicators.indicators}

        def drop_unselected(draft: AlignmentDraft) -> AlignmentDraft:
            # Ссылки на индикаторы невыбранных рейтингов (например, THE при выборе только QS) отбрасываем
            for l in draft.links:
                l.indicator_ids = [i for i in l.indicator_ids if i.split("-")[0] in rankings]
            return draft

        def check(draft: AlignmentDraft) -> list[str]:
            # Критерий завершения: каждый KPI обработан, ссылки только на существующие id
            problems = []
            linked = {l.kpi_id for l in draft.links}
            missing = [k for k in kpi_ids if k not in linked]
            if missing:
                problems.append(f"нет связей для KPI: {', '.join(missing)}")
            for l in drop_unselected(draft).links:
                bad = [g for g in l.goal_ids if g not in goal_ids] + \
                      [i for i in l.indicator_ids if i not in indicator_ids]
                if bad:
                    problems.append(f"{l.kpi_id}: неизвестные id {bad}")
            return problems

        draft = self.ask_llm(user, AlignmentDraft, check)
        links = [l for l in draft.links if l.kpi_id in set(kpi_ids)]
        coverage = self.call_tool("coverage_calculator", "calculate", goals=goals, indicators=indicators,
                                  kpi_set=kpi_set, links=links, levels=levels)
        matrix = AlignmentMatrix(links=links, coverage=coverage)
        self.save("alignment_matrix", matrix)
        return matrix
