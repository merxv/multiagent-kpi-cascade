"""KPIDesigner — формулирует SMART-KPI для всех уровней каскада за ОДИН вызов LLM."""
from agents.base import BaseAgent, to_json
from core.schemas import (LEVEL_NAMES_RU, KPISet, RankingIndicators, ReviewReport,
                          StrategicGoals)


class KPIDesigner(BaseAgent):
    name = "KPIDesigner"
    slug = "kpi_designer"
    tool_names = ("kpi_validator", "state_store")

    def run(self, payload: dict) -> KPISet:
        goals = StrategicGoals.model_validate(payload["goals"])
        indicators = RankingIndicators.model_validate(payload["indicators"])
        levels: list[str] = payload["levels"]
        iteration: int = payload.get("iteration", 1)

        levels_text = " → ".join(f"{l} ({LEVEL_NAMES_RU[l]})" for l in levels)
        user = (
            f"Университет: {goals.university}\n"
            f"Миссия: {goals.mission}\n\n"
            f"Уровни каскада (сверху вниз): {levels_text}\n\n"
            f"Стратегические цели:\n{to_json([g.model_dump() for g in goals.goals])}\n\n"
            f"Индикаторы рейтингов:\n{to_json([i.model_dump() for i in indicators.indicators])}\n"
        )
        # На доработке добавляем замечания проверяющего и предыдущую версию KPI
        if payload.get("review"):
            review = ReviewReport.model_validate(payload["review"])
            issues = [i for i in review.issues if i.addressee in ("KPIDesigner", "AlignmentMapper")]
            user += (
                f"\n## Замечания проверяющего (итерация {review.iteration})\n"
                + "\n".join(f"- [{i.priority}] {i.description} {i.related_ids}" for i in issues)
                + f"\n\n## Предыдущая версия KPI\n{to_json(payload['previous_kpis']['kpis'])}\n"
                "Исправь KPI с учётом замечаний и верни полный набор KPI заново.\n"
            )
        user += "\nСформулируй KPI для всех уровней сразу. Верни JSON по формату из инструкции."

        def prepare(kpi_set: KPISet) -> KPISet:
            # Оставляем только запрошенные уровни; у верхнего уровня родителя нет
            kpi_set.kpis = [k for k in kpi_set.kpis if k.level in levels]
            for k in kpi_set.kpis:
                if k.level == levels[0]:
                    k.parent_id = None
            kpi_set.university = goals.university
            kpi_set.iteration = iteration
            return kpi_set

        def check(kpi_set: KPISet) -> list[str]:
            # Критерий завершения: ≥3 KPI на каждом уровне, все проходят kpi_validator
            report = self.call_tool("kpi_validator", "validate", kpi_set=prepare(kpi_set), levels=levels)
            kpi_set.validation = report
            return report["set_problems"] + [
                f"{kpi_id}: {', '.join(p)}" for kpi_id, p in report["invalid"].items()
            ]

        kpi_set = self.ask_llm(user, KPISet, check)
        self.save("kpi_set", kpi_set)
        return kpi_set
