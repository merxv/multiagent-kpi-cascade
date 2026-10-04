"""KPIDesigner — формулирует SMART-KPI для всех уровней каскада.

1. LLM возвращает KPI деревом: KPI университета → вложенные KPI факультета → кафедры → преподавателя.
   Связи задаются вложенностью, а id и parent_id проставляет код (flatten): небольшие локальные
   модели путают ссылки на id, а вложенность держат хорошо.
2. Оборванные ветки (KPI без дочерних, кроме нижнего уровня) код находит сам и просит LLM
   дописать только недостающие звенья — не переделывая весь набор.
3. На доработке ветки без блокирующих замечаний сохраняются как есть; LLM переписывает только
   ветки с замечаниями и добавляет ветки для непокрытых целей и индикаторов.
"""
from agents.base import AgentError, BaseAgent, to_json
from core.schemas import (KPI, LEVEL_NAMES_RU, KPICompletion, KPINode, KPISet, KPITree,
                          RankingIndicators, ReviewReport, StrategicGoals)

LEVEL_CODES = {"university": "U", "faculty": "F", "department": "D", "teacher": "T"}
MIN_TOP_LEVEL = 3  # при ≥3 KPI верхнего уровня и полных ветках на каждом уровне будет ≥3 KPI


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
    """Обратное преобразование: плоский список -> вложенный вид (с id, для показа модели)."""
    children: dict = {}
    for k in kpi_set.kpis:
        children.setdefault(k.parent_id, []).append(k)

    def node(k: KPI) -> dict:
        data = k.model_dump(exclude={"level", "parent_id"})
        data["children"] = [node(c) for c in children.get(k.id, [])]
        return data

    return [node(k) for k in children.get(None, [])]


def branch_ids(branch: dict) -> set[str]:
    """Все id KPI в ветке (сам KPI и все потомки)."""
    ids = {branch["id"]}
    for child in branch["children"]:
        ids |= branch_ids(child)
    return ids


def find_gaps(kpis: list[KPI], levels: list[str]) -> list[KPI]:
    """KPI, у которых нет дочерних KPI, хотя их уровень не нижний (оборванные ветки)."""
    parents = {k.parent_id for k in kpis}
    return [k for k in kpis if k.level != levels[-1] and k.id not in parents]


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
        context = (
            f"Университет: {goals.university}\n"
            f"Миссия: {goals.mission}\n\n"
            f"Уровни каскада (глубина вложенности дерева): {depth_text}.\n"
            f"Дерево должно иметь ровно {len(levels)} уровн(я/ей) вложенности; "
            f"у KPI уровня «{LEVEL_NAMES_RU[levels[-1]]}» поле children пустое.\n\n"
            f"Стратегические цели:\n{to_json([g.model_dump() for g in goals.goals])}\n\n"
            f"Индикаторы рейтингов:\n{to_json([i.model_dump() for i in indicators.indicators])}\n\n"
            + stats_text
        )

        kept: list[dict] = []  # ветки, которые сохраняются без изменений (на доработке)
        if payload.get("review"):
            review = ReviewReport.model_validate(payload["review"])
            previous = KPISet.model_validate(payload["previous_kpis"])
            kept, task = self._revision_task(review, previous)
        else:
            task = "\nСформулируй дерево KPI для всех уровней сразу. Верни JSON по формату из инструкции."

        # 1) Основной запрос: дерево KPI (на доработке — только исправленные и новые ветки)
        def check(tree: KPITree) -> list[str]:
            full = KPITree(kpis=[KPINode.model_validate(b) for b in kept] + tree.kpis)
            kpis, warnings = flatten(full, levels)
            if warnings:
                self.logger.log("info", self.name, "flatten", message="; ".join(warnings))
            problems = []
            if kept and not tree.kpis:
                problems.append("ты не вернул ни одной исправленной или новой ветки")
            top = sum(1 for k in kpis if k.level == levels[0])
            if top < MIN_TOP_LEVEL:
                problems.append(f"на уровне «{LEVEL_NAMES_RU[levels[0]]}» {top} KPI, нужно не менее {MIN_TOP_LEVEL}")
            report = self.call_tool("kpi_validator", "validate",
                                    kpi_set=KPISet(kpis=kpis), levels=levels)
            # Нехватку KPI на нижних уровнях из-за оборванных веток исправит шаг дописывания
            return problems + [f"KPI «{next(k.name for k in kpis if k.id == kid)}»: {', '.join(p)}"
                               for kid, p in report["invalid"].items()]

        tree = self.ask_llm(context + task, KPITree, check)
        kpis, _ = flatten(KPITree(kpis=[KPINode.model_validate(b) for b in kept] + tree.kpis), levels)

        # 2) Дописывание оборванных веток
        gaps = find_gaps(kpis, levels)
        if gaps:
            kpis = self._complete_gaps(context, kpis, gaps, levels)

        # 3) Итоговая проверка (критерий завершения): ≥3 KPI на каждом уровне, все проходят валидатор
        kpi_set = KPISet(university=goals.university, iteration=iteration, kpis=kpis)
        report = self.call_tool("kpi_validator", "validate", kpi_set=kpi_set, levels=levels)
        kpi_set.validation = report
        if report["set_problems"] or report["invalid"]:
            raise AgentError(self.name, "набор KPI не прошёл проверку: "
                             + "; ".join(report["set_problems"] + list(report["invalid"]))[:500])
        self.save("kpi_set", kpi_set)
        return kpi_set

    # --- доработка ------------------------------------------------------------
    def _revision_task(self, review: ReviewReport, previous: KPISet) -> tuple[list[dict], str]:
        """Делит предыдущее дерево на сохраняемые ветки и ветки с замечаниями; формирует задание."""
        branches = to_tree(previous)
        mine = [i for i in review.issues if i.addressee == "KPIDesigner"]
        flagged_ids = {rid for i in mine if i.blocking for rid in i.related_ids}
        kept = [b for b in branches if not (branch_ids(b) & flagged_ids)]
        rewrite = [b for b in branches if branch_ids(b) & flagged_ids]

        def issue_line(i) -> str:
            ids = f" [{', '.join(i.related_ids)}]" if i.related_ids else ""
            return f"- {i.description}{ids}"

        task = "\n## Замечания проверяющего (итерация {})\n".format(review.iteration)
        blocking = [i for i in mine if i.blocking]
        advice = [i for i in mine if not i.blocking]
        if blocking:
            task += "Обязательно исправить:\n" + "\n".join(issue_line(i) for i in blocking) + "\n"
        if advice:
            task += "Рекомендации (учти, если это не ломает остальное):\n" + "\n".join(issue_line(i) for i in advice) + "\n"
        task += (f"\n## Ветки без замечаний — СОХРАНЯЮТСЯ как есть, НЕ возвращай их\n{to_json(kept)}\n"
                 if kept else "\n## Все ветки требуют доработки\n")
        if rewrite:
            task += f"\n## Ветки с замечаниями — верни их исправленный вариант\n{to_json(rewrite)}\n"
        task += ("\nВерни ТОЛЬКО исправленные ветки и новые ветки для непокрытых целей и индикаторов "
                 "(каждая ветка — полная цепочка до нижнего уровня), в том же формате дерева. "
                 "id в ответе не указывай.")
        self.logger.log("info", self.name, "revision_plan",
                        message=f"сохраняются ветки: {[b['id'] for b in kept]}; "
                                f"переписываются: {[b['id'] for b in rewrite]}")
        return kept, task

    # --- дописывание оборванных веток -------------------------------------------
    def _complete_gaps(self, context: str, kpis: list[KPI], gaps: list[KPI], levels: list[str]) -> list[KPI]:
        """Просит LLM дописать дочерние KPI только для оборванных веток. При неудаче — оставляет как есть."""
        gap_ids = {k.id for k in gaps}
        lines = []
        for k in gaps:
            below = levels[levels.index(k.level) + 1:]
            chain = " → ".join(LEVEL_NAMES_RU[l] for l in below)
            lines.append(f"- {k.id} «{k.name}» (уровень «{LEVEL_NAMES_RU[k.level]}»): нужна цепочка {chain}")
        user = (context + "\n## Текущее дерево KPI\n" + to_json(to_tree(KPISet(kpis=kpis)))
                + "\n\n## Допиши недостающие звенья\nУ этих KPI нет дочерних KPI, ветки оборваны:\n"
                + "\n".join(lines)
                + "\n\nДля каждого из них верни дочерние KPI (полную цепочку до нижнего уровня) в формате:\n"
                '{"completions": [{"parent_id": "F-06", "children": [ {KPI в формате дерева} ]}]}\n'
                "Остальные KPI не возвращай.")

        def check(completion: KPICompletion) -> list[str]:
            got = {c.parent_id for c in completion.completions if c.children}
            problems = [f"нет дочерних KPI для {gid}" for gid in sorted(gap_ids - got)]
            problems += [f"{c.parent_id} не входит в список оборванных веток"
                         for c in completion.completions if c.parent_id not in gap_ids]
            return problems

        try:
            completion = self.ask_llm(user, KPICompletion, check)
        except AgentError as e:
            self.logger.log("info", self.name, "complete_gaps", status="error",
                            message=f"не удалось дописать ветки: {e.message}")
            return kpis
        # Прикрепляем дописанные звенья к дереву и заново проставляем id
        tree = to_tree(KPISet(kpis=kpis))
        attach = {c.parent_id: [n.model_dump() for n in c.children] for c in completion.completions}

        def visit(node: dict) -> None:
            for child in node["children"]:
                visit(child)
            if node["id"] in attach:  # прикрепляем после обхода: у новых звеньев ещё нет id
                node["children"] = attach[node["id"]]

        for branch in tree:
            visit(branch)
        new_kpis, _ = flatten(KPITree.model_validate({"kpis": tree}), levels)
        self.logger.log("info", self.name, "complete_gaps",
                        message=f"дописаны ветки: {', '.join(sorted(attach))}")
        return new_kpis
