"""RankingAnalyst — определяет индикаторы выбранных рейтингов, их веса и влияющую деятельность.

Разделение работы:
  * факты (какие индикаторы есть в рейтинге и их веса) берутся из базы знаний через vector_search —
    их не нужно «угадывать», а небольшие модели в них путаются (например, принимают
    подындикаторы THE за отдельные индикаторы);
  * LLM интерпретирует методологию применительно к университету: что измеряет индикатор
    и какая деятельность преподавателей и сотрудников на него влияет.
После ответа LLM результат сверяется со справочником: повторы убираются, веса берутся из базы,
пропущенные индикаторы дополняются. Все исправления пишутся в лог.
"""
from agents.base import BaseAgent, to_json
from core.schemas import RankingIndicator, RankingIndicators, StrategicGoals

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

        # Справочник индикаторов из базы знаний: id -> фрагмент (с весом и названием)
        reference = {f["indicator_id"]: f for f in fragments if f.get("indicator_id")}
        reference_text = "\n".join(
            f"- {f['indicator_id']} | {f['ranking']} | {f['indicator_name']} | вес {f['weight']:g}%"
            for f in reference.values())
        counts = ", ".join(f"{r} — {sum(1 for f in reference.values() if f['ranking'] == r)}" for r in rankings)

        sources = "\n\n".join(f"[{f['ranking']} / {f['section']}]\n{f['text']}" for f in fragments)
        related_text = "\n".join(f"- {f['ranking']} / {f['section']} (сходство {f['score']})" for f in related)
        user = (
            f"Выбранные рейтинги: {', '.join(rankings)}\n\n"
            f"Справочник индикаторов из базы знаний (используй РОВНО эти id, названия и веса; "
            f"подындикаторы внутри разделов отдельными индикаторами НЕ считай). "
            f"Число индикаторов: {counts}.\n{reference_text}\n\n"
            f"Стратегические цели университета:\n{to_json([g.model_dump() for g in goals.goals])}\n\n"
            f"Разделы методологий, наиболее близкие к целям университета:\n{related_text}\n\n"
            f"Фрагменты методологий из базы знаний:\n{sources}\n\n"
            "Для каждого индикатора из справочника опиши, что он измеряет, и какая деятельность "
            "преподавателей и сотрудников на него влияет. Верни JSON по формату из инструкции."
        )

        def reconcile(result: RankingIndicators) -> RankingIndicators:
            """Сверка ответа LLM со справочником (детерминированно, без LLM)."""
            fixes = []
            by_id: dict[str, RankingIndicator] = {}
            for ind in result.indicators:
                if ind.ranking not in rankings:
                    continue  # индикаторы невыбранных рейтингов отбрасываем
                if ind.id in by_id:
                    fixes.append(f"удалён повтор {ind.id}")
                    continue
                if reference and ind.id not in reference:
                    fixes.append(f"удалён индикатор {ind.id}, которого нет в методологии")
                    continue
                if ind.id in reference and abs(ind.weight - reference[ind.id]["weight"]) > 1e-6:
                    fixes.append(f"вес {ind.id}: {ind.weight:g}% → {reference[ind.id]['weight']:g}%")
                    ind.weight = reference[ind.id]["weight"]
                by_id[ind.id] = ind
            # Индикаторы, которые LLM пропустил, дополняем из базы знаний
            for ind_id, f in reference.items():
                if f["ranking"] in rankings and ind_id not in by_id:
                    fixes.append(f"дополнен из базы знаний {ind_id}")
                    by_id[ind_id] = RankingIndicator(
                        id=ind_id, ranking=f["ranking"], name=f["indicator_name"], weight=f["weight"],
                        description="(дополнено из базы знаний)",
                        influencing_activities=[a.strip() for a in f["activities"].split(";") if a.strip()])
            if fixes:
                self.logger.log("info", self.name, "reconcile", message="сверка со справочником: " + "; ".join(fixes))
            result.indicators = list(by_id.values())
            return result

        def check(result: RankingIndicators) -> list[str]:
            # Критерий завершения: для каждого рейтинга найдены индикаторы, Σ весов ≈ 100%
            result = reconcile(result)
            problems = []
            for r in rankings:
                total = sum(i.weight for i in result.indicators if i.ranking == r)
                if abs(total - 100) > WEIGHT_TOLERANCE:
                    problems.append(f"сумма весов индикаторов {r} = {total:g}%, должна быть ≈100%")
            return problems

        indicators = self.ask_llm(user, RankingIndicators, check)
        self.save("ranking_indicators", indicators)
        return indicators
