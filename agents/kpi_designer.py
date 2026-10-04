"""KPIDesigner — формулирует SMART-KPI для всех уровней каскада за ОДИН вызов LLM.

LLM возвращает KPI деревом: KPI университета → вложенные KPI факультета → кафедры → преподавателя.
Связи «родитель — потомок» задаются вложенностью, а id и parent_id проставляет код (flatten).
Так модель не может ошибиться в ссылках на родителя: небольшие локальные модели путают id
(например, делают KPI факультета дочерним для соседнего KPI факультета), а вложенность держат хорошо.
"""
from agents.base import BaseAgent, to_json
from core.schemas import (KPI, LEVEL_NAMES_RU, KPINode, KPISet, KPITree, RankingIndicators,
                          ReviewReport, StrategicGoals)

LEVEL_CODES = {"university": "U", "faculty": "F", "department": "D", "teacher": "T"}


def flatten(tree: KPITree, levels: list[str]) -> tuple[list[KPI], list[str]]:
    """Разворачивает дерево в плоский список KPI с id вида U-01, F-01 ... и parent_id.

    Глубина вложенности определяет уровень: 0 — levels[0], 1 — levels[1] и т.д.
    Номера идут по порядку обхода дерева отдельно для каждого уровня.
    Возвращает (KPI, предупреждения).
    """
    kpis: list[KPI] = []
    warnings: list[str] = []
    counters = {lvl: 0 for lvl in levels}

    def walk(nodes: list[KPINode], depth: int, parent_id: str | None) -> None:
        if not nodes:
            return
        if depth >= len(levels):
            names = ", ".join(f"«{n.name}»" for n in nodes)
            warnings.append(f"отброшены KPI глубже уровня «{LEVEL_NAMES_RU[levels[-1]]}»: {names}")
            return
        level = levels[depth]
        for node in nodes:
            counters[level] += 1
            kpi_id = f"{LEVEL_CODES[level]}-{counters[level]:02d}"
            kpis.append(KPI(id=kpi_id, name=node.name, level=level, method=node.method, unit=node.unit,
                            target=node.target, baseline=node.baseline, period=node.period,
                            parent_id=parent_id, rationale=node.rationale))
            walk(node.children, depth + 1, kpi_id)

    walk(tree.kpis, 0, None)
    return kpis, warnings


def to_tree(kpi_set: KPISet) -> list[dict]:
    """Обратное преобразование: плоский список -> вложенный вид (для показа модели на доработке)."""
    children: dict = {}
    for k in kpi_set.kpis:
        children.setdefault(k.parent_id, []).append(k)

    def node(k: KPI) -> dict:
        data = k.model_dump(exclude={"level", "parent_id"})
        data["children"] = [node(c) for c in children.get(k.id, [])]
        return data

    return [node(k) for k in children.get(None, [])]


class KPIDesigner(BaseAgent):
    name = "KPIDesigner"
    slug = "kpi_designer"
    tool_names = ("kpi_validator", "openalex_stats", "state_store")

    def run(self, payload: dict) -> KPISet:
        goals = StrategicGoals.model_validate(payload["goals"])
        indicators = RankingIndicators.model_validate(payload["indicators"])
        levels: list[str] = payload["levels"]
        iteration: int = payload.get("iteration", 1)

        # Реальные публикационные ориентиры из OpenAlex (ответы кэшируются; при недоступности — без них)
        stats = self.call_tool("openalex_stats", "benchmarks")
        if stats.get("available"):
            stats_text = (f"Справочные публикационные показатели (OpenAlex, {stats['country']}, {stats['year']} г.) — "
                          f"используй их, чтобы целевые значения были реалистичными:\n{to_json(stats)}\n")
        else:
            stats_text = f"Справочные данные OpenAlex недоступны ({stats.get('error')}); опирайся на базовые значения.\n"

        depth_text = " → ".join(f"уровень {i + 1}: {LEVEL_NAMES_RU[l]}" for i, l in enumerate(levels))
        user = (
            f"Университет: {goals.university}\n"
            f"Миссия: {goals.mission}\n\n"
            f"Уровни каскада (глубина вложенности дерева): {depth_text}.\n"
            f"Дерево должно иметь ровно {len(levels)} уровн(я/ей) вложенности; "
            f"у KPI уровня «{LEVEL_NAMES_RU[levels[-1]]}» поле children пустое.\n\n"
            f"Стратегические цели:\n{to_json([g.model_dump() for g in goals.goals])}\n\n"
            f"Индикаторы рейтингов:\n{to_json([i.model_dump() for i in indicators.indicators])}\n\n"
            + stats_text
        )
        # На доработке добавляем замечания проверяющего и предыдущую версию KPI
        if payload.get("review"):
            review = ReviewReport.model_validate(payload["review"])
            issues = [i for i in review.issues if i.addressee in ("KPIDesigner", "AlignmentMapper")]
            previous = KPISet.model_validate(payload["previous_kpis"])
            user += (
                f"\n## Замечания проверяющего (итерация {review.iteration})\n"
                + "\n".join(f"- [{i.priority}] {i.description} {i.related_ids}" for i in issues)
                + f"\n\n## Предыдущая версия KPI (id указаны для понимания замечаний)\n{to_json(to_tree(previous))}\n"
                "Исправь KPI с учётом замечаний и верни полное дерево KPI заново.\n"
            )
        user += "\nСформулируй дерево KPI для всех уровней сразу. Верни JSON по формату из инструкции."

        result: dict = {}  # сюда check положит развёрнутый набор KPI последней попытки

        def check(tree: KPITree) -> list[str]:
            # Критерий завершения: ≥3 KPI на каждом уровне, все проходят kpi_validator
            kpis, warnings = flatten(tree, levels)
            kpi_set = KPISet(university=goals.university, iteration=iteration, kpis=kpis)
            report = self.call_tool("kpi_validator", "validate", kpi_set=kpi_set, levels=levels)
            kpi_set.validation = report
            result["kpi_set"] = kpi_set
            if warnings:
                self.logger.log("info", self.name, "flatten", message="; ".join(warnings))
            return report["set_problems"] + [
                f"KPI «{next(k.name for k in kpis if k.id == kpi_id)}»: {', '.join(p)}"
                for kpi_id, p in report["invalid"].items()
            ]

        self.ask_llm(user, KPITree, check)
        kpi_set = result["kpi_set"]
        self.save("kpi_set", kpi_set)
        return kpi_set
