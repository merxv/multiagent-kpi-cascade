"""RankingAnalyst — определяет индикаторы выбранных рейтингов, их веса и влияющую деятельность."""
from agents.base import BaseAgent, to_json
from core.schemas import RankingIndicators, StrategicGoals

WEIGHT_TOLERANCE = 1.5  # допуск для «сумма весов ≈ 100%»


class RankingAnalyst(BaseAgent):
    name = "RankingAnalyst"
    slug = "ranking_analyst"
    tool_names = ("vector_search", "state_store")

    def run(self, payload: dict) -> RankingIndicators:
        rankings: list[str] = payload["rankings"]
        goals = StrategicGoals.model_validate(payload["goals"])

        # 1) Методология каждого рейтинга целиком (все разделы файла)
        fragments = []
        for r in rankings:
            fragments += self.call_tool("vector_search", "search",
                                        query=f"Методология рейтинга {r}: индикаторы, веса, влияющая деятельность",
                                        ranking=r, k=15)
        # 2) Фрагменты, наиболее близкие к стратегическим целям университета
        goals_query = "; ".join(g.title for g in goals.goals)
        related = self.call_tool("vector_search", "search", query=goals_query, k=5)

        sources = "\n\n".join(f"[{f['ranking']} / {f['section']}]\n{f['text']}" for f in fragments)
        related_text = "\n".join(f"- {f['ranking']} / {f['section']} (сходство {f['score']})" for f in related)
        user = (
            f"Выбранные рейтинги: {', '.join(rankings)}\n\n"
            f"Стратегические цели университета:\n{to_json([g.model_dump() for g in goals.goals])}\n\n"
            f"Разделы методологий, наиболее близкие к целям университета:\n{related_text}\n\n"
            f"Фрагменты методологий из базы знаний:\n{sources}\n\n"
            "Составь полный список индикаторов выбранных рейтингов. Верни JSON по формату из инструкции."
        )

        def check(result: RankingIndicators) -> list[str]:
            # Критерий завершения: для каждого рейтинга найдены индикаторы, Σ весов ≈ 100%
            problems = []
            for r in rankings:
                total = sum(i.weight for i in result.indicators if i.ranking == r)
                if abs(total - 100) > WEIGHT_TOLERANCE:
                    problems.append(f"сумма весов индикаторов {r} = {total:g}%, должна быть ≈100%")
            ids = [i.id for i in result.indicators]
            if len(set(ids)) != len(ids):
                problems.append("id индикаторов повторяются")
            return problems

        def keep_selected(result: RankingIndicators) -> RankingIndicators:
            # Индикаторы невыбранных рейтингов отбрасываем
            result.indicators = [i for i in result.indicators if i.ranking in rankings]
            return result

        indicators = self.ask_llm(user, RankingIndicators, lambda r: check(keep_selected(r)))
        self.save("ranking_indicators", indicators)
        return indicators
