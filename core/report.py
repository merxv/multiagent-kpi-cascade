"""Сохранение результата: outputs/<task_id>/result.json и читаемый отчёт report.md."""
from pathlib import Path

from core.schemas import LEVEL_NAMES_RU, TaskResult

STATUS_RU = {"completed": "завершено", "partial": "частичный результат", "failed": "ошибка"}
PRIORITY_RU = {"high": "высокий", "medium": "средний", "low": "низкий"}


def _cell(value) -> str:
    """Значение для ячейки markdown-таблицы."""
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def render_report(r: TaskResult) -> str:
    lines = [f"# Каскад KPI: {r.goals.university if r.goals else r.request.input_path}", ""]
    lines += [
        f"- Задача: `{r.task_id}`",
        f"- Статус: **{STATUS_RU[r.status]}** — {r.message}",
        f"- Документ: `{r.request.input_path}`",
        f"- Рейтинги: {', '.join(r.request.rankings)}",
        f"- Шагов: {r.steps_used}, доработок: {r.revisions}, время: {r.elapsed_sec} с",
        f"- Лог: `{r.log_path}`",
        "",
    ]

    if r.goals:
        lines += ["## 1. Миссия и стратегические цели", "", f"**Миссия.** {r.goals.mission}", ""]
        if r.goals.vision:
            lines += [f"**Видение.** {r.goals.vision}", ""]
        lines += ["| ID | Цель | Описание | Основание в документе |", "|---|---|---|---|"]
        lines += [f"| {g.id} | {_cell(g.title)} | {_cell(g.description)} | «{_cell(g.evidence)}» |"
                  for g in r.goals.goals]
        lines.append("")

    if r.indicators:
        lines += ["## 2. Индикаторы рейтингов", "",
                  "| ID | Рейтинг | Индикатор | Вес, % | Влияющая деятельность |", "|---|---|---|---|---|"]
        lines += [f"| {i.id} | {i.ranking} | {_cell(i.name)} | {i.weight:g} | "
                  f"{_cell('; '.join(i.influencing_activities))} |" for i in r.indicators.indicators]
        lines.append("")

    if r.kpis:
        lines += ["## 3. Каскад KPI", ""]
        for level in r.request.levels:
            kpis = r.kpis.by_level(level)
            if not kpis:
                continue
            lines += [f"### {LEVEL_NAMES_RU[level]}", "",
                      "| ID | KPI | Метод расчёта | Ед. | База | Цель | Период | Родитель |",
                      "|---|---|---|---|---|---|---|---|"]
            lines += [f"| {k.id} | {_cell(k.name)} | {_cell(k.method)} | {_cell(k.unit)} | "
                      f"{_cell(k.baseline)} | {_cell(k.target)} | {_cell(k.period)} | {_cell(k.parent_id or '—')} |"
                      for k in kpis]
            lines.append("")

    if r.matrix:
        lines += ["## 4. Матрица связей", "",
                  "| KPI | Цели | Индикаторы | Сила (1–3) | Обоснование |", "|---|---|---|---|---|"]
        lines += [f"| {l.kpi_id} | {', '.join(l.goal_ids) or '—'} | {', '.join(l.indicator_ids) or '—'} | "
                  f"{l.strength} | {_cell(l.rationale)} |" for l in r.matrix.links]
        lines.append("")
        if r.matrix.coverage:
            c = r.matrix.coverage
            lines += ["**Покрытие:**", "",
                      f"- целей с KPI: {c.goal_coverage:.0%}"
                      + (f" (без KPI: {', '.join(c.uncovered_goals)})" if c.uncovered_goals else ""),
                      *[f"- вес индикаторов {rk}, покрытый KPI: {v:g}%" for rk, v in c.indicator_weight_coverage.items()],
                      f"- непокрытые индикаторы: {', '.join(c.uncovered_indicators) or 'нет'}",
                      f"- KPI-сироты: {', '.join(c.orphan_kpis + c.unlinked_kpis) or 'нет'}",
                      f"- разрывы каскада: {'; '.join(c.cascade_gaps) or 'нет'}", ""]

    if r.review:
        verdict = "одобрено" if r.review.verdict == "approved" else "требуется доработка"
        lines += ["## 5. Заключение проверяющего", "",
                  f"**Вердикт (итерация {r.review.iteration}):** {verdict}", "", r.review.summary, ""]
        if r.review.issues:
            lines += ["| Приоритет | Адресат | Категория | Замечание |", "|---|---|---|---|"]
            lines += [f"| {PRIORITY_RU[i.priority]} | {i.addressee} | {_cell(i.category)} | {_cell(i.description)} |"
                      for i in r.review.issues]
            lines.append("")
    return "\n".join(lines)


def save_outputs(result: TaskResult, output_dir: Path) -> Path:
    """Сохраняет result.json и report.md, возвращает папку с результатами."""
    folder = Path(output_dir) / result.task_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    (folder / "report.md").write_text(render_report(result), encoding="utf-8")
    return folder
