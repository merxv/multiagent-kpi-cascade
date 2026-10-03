"""Веб-интерфейс на Streamlit.

Запуск: streamlit run app.py
Интерфейс только вызывает тот же пайплайн, что и CLI (core.orchestrator.run_pipeline),
и показывает результат: каскад KPI, матрицу связей, замечания проверяющего, логи и нагрузку.
"""
import dataclasses
import json
import uuid
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from core.config import ROOT_DIR, get_settings
from core.orchestrator import run_pipeline
from core.report import PRIORITY_RU, STATUS_RU, render_report, save_outputs
from core.schemas import LEVEL_NAMES_RU, LEVELS, RANKINGS, TaskRequest, TaskResult
from scripts.load_report import LIMIT, compute_load

PROVIDERS = ["mock", "anthropic", "openai", "ollama"]
SAMPLES_DIR = ROOT_DIR / "data" / "samples"
BAR_COLOR = "#2a78d6"  # один ряд данных — один цвет
LIMIT_COLOR = "#c0392b"

st.set_page_config(page_title="KPI Cascade", page_icon="🎯", layout="wide")


# ---------------------------------------------------------------------------
# Боковая панель: настройки запуска
# ---------------------------------------------------------------------------
def sidebar_settings():
    base = get_settings()
    st.sidebar.header("Настройки")
    provider = st.sidebar.selectbox(
        "Провайдер LLM", PROVIDERS,
        index=PROVIDERS.index(base.llm_provider) if base.llm_provider in PROVIDERS else 0,
        key="provider",
        help="mock — заготовленные ответы (работает только для примеров из data/samples). "
             "Ключи API задаются в файле .env.")
    openalex = st.sidebar.checkbox("Использовать OpenAlex", value=base.openalex_enabled, key="openalex",
                                   help="Реальные публикационные показатели для целевых значений KPI")
    st.sidebar.caption(f"Лимиты: шагов {base.max_steps}, доработок {base.max_revisions}, "
                       f"время {base.task_timeout_sec:g} с")
    return dataclasses.replace(base, llm_provider=provider, openalex_enabled=openalex)


# ---------------------------------------------------------------------------
# Форма запуска
# ---------------------------------------------------------------------------
def input_form(settings) -> TaskRequest | None:
    """Показывает форму. Возвращает запрос, если пользователь нажал «Запустить» и ввод корректен."""
    source = st.radio("Стратегический документ", ["Пример", "Загрузить файл"], horizontal=True, key="source")
    path = None
    if source == "Пример":
        samples = sorted(p.name for p in SAMPLES_DIR.glob("*.pdf"))
        name = st.selectbox("Пример стратегии", samples, key="sample")
        path = SAMPLES_DIR / name if name else None
    else:
        uploaded = st.file_uploader("PDF, Markdown или текст", type=["pdf", "md", "txt"], key="upload")
        if uploaded is not None:
            # Сохраняем загруженный файл, т.к. агенты работают с путём к файлу
            settings.upload_dir.mkdir(parents=True, exist_ok=True)
            path = settings.upload_dir / f"{uuid.uuid4().hex[:8]}_{Path(uploaded.name).name}"
            path.write_bytes(uploaded.getvalue())

    col1, col2 = st.columns(2)
    rankings = col1.multiselect("Глобальные рейтинги", RANKINGS, default=RANKINGS, key="rankings")
    levels = col2.multiselect("Уровни каскада", LEVELS, default=LEVELS, key="levels",
                              format_func=lambda l: LEVEL_NAMES_RU[l])

    if not st.button("Запустить", type="primary", key="run"):
        return None
    if path is None:
        st.error("Выберите пример или загрузите файл.")
        return None
    if not rankings or not levels:
        st.error("Выберите хотя бы один рейтинг и один уровень каскада.")
        return None
    return TaskRequest(input_path=str(path), rankings=rankings, levels=levels)


def run_with_progress(request: TaskRequest, settings) -> TaskResult:
    """Запускает пайплайн и показывает прогресс по агентам."""
    with st.status("Агенты работают…", expanded=True) as status:
        result = run_pipeline(request, settings, on_progress=status.write)
        state = {"completed": "complete", "partial": "complete", "failed": "error"}[result.status]
        status.update(label=f"Готово: {STATUS_RU[result.status]}", state=state, expanded=False)
    save_outputs(result, settings.output_dir)
    return result


# ---------------------------------------------------------------------------
# Отображение результата
# ---------------------------------------------------------------------------
def show_summary(r: TaskResult) -> None:
    text = f"**{STATUS_RU[r.status].capitalize()}.** {r.message}"
    {"completed": st.success, "partial": st.warning, "failed": st.error}[r.status](text)
    cov = r.matrix.coverage if r.matrix else None
    weights = cov.indicator_weight_coverage if cov else {}
    cols = st.columns(4 + len(weights))
    cols[0].metric("Шагов", r.steps_used)
    cols[1].metric("Доработок", r.revisions)
    cols[2].metric("Время, с", r.elapsed_sec)
    cols[3].metric("Покрытие целей", f"{cov.goal_coverage:.0%}" if cov else "—")
    for col, (ranking, value) in zip(cols[4:], weights.items()):
        col.metric(f"Покрыто веса {ranking}", f"{value:g}%")

    d1, d2, _ = st.columns([1, 1, 3])
    d1.download_button("Скачать report.md", render_report(r), file_name=f"report_{r.task_id}.md",
                       mime="text/markdown", key="dl_md")
    d2.download_button("Скачать result.json", r.model_dump_json(indent=2), file_name=f"result_{r.task_id}.json",
                       mime="application/json", key="dl_json")


def full_height(rows: int) -> int:
    """Высота таблицы, при которой видны все строки без прокрутки."""
    return 35 * (rows + 1) + 3


def tab_cascade(r: TaskResult) -> None:
    if not r.kpis:
        st.info("KPI не сформированы.")
        return
    for level in r.request.levels:
        kpis = r.kpis.by_level(level)
        if not kpis:
            continue
        st.subheader(LEVEL_NAMES_RU[level])
        st.dataframe(pd.DataFrame([{
            "ID": k.id, "KPI": k.name, "Метод расчёта": k.method, "Ед.": k.unit,
            "База": "" if k.baseline is None else str(k.baseline), "Цель": str(k.target),
            "Период": k.period, "Родитель": k.parent_id or "—"} for k in kpis]),
            hide_index=True, use_container_width=True)

    with st.expander("Дерево каскада"):
        children: dict = {}
        for k in r.kpis.kpis:
            children.setdefault(k.parent_id, []).append(k)
        lines = []

        def walk(parent, depth):
            for k in children.get(parent, []):
                lines.append(f"{'    ' * depth}- **{k.id}** {k.name} — цель: {k.target} {k.unit}")
                walk(k.id, depth + 1)
        walk(None, 0)
        st.markdown("\n".join(lines))


def tab_matrix(r: TaskResult) -> None:
    if not r.matrix:
        st.info("Матрица связей не построена.")
        return
    goals = [g.id for g in r.goals.goals] if r.goals else []
    indicators = [i.id for i in r.indicators.indicators] if r.indicators else []

    def pivot(ids, field):
        # Строка — KPI, столбец — цель/индикатор, значение — сила связи 1–3
        rows = []
        for link in r.matrix.links:
            linked = getattr(link, field)
            rows.append({"KPI": link.kpi_id, **{i: (str(link.strength) if i in linked else "") for i in ids}})
        return pd.DataFrame(rows).set_index("KPI")

    st.caption("Значение в ячейке — сила связи: 3 — прямая, 2 — существенная косвенная, 1 — слабая.")
    st.subheader("KPI × стратегические цели")
    st.dataframe(pivot(goals, "goal_ids"), use_container_width=True, height=full_height(len(r.matrix.links)))
    st.subheader("KPI × индикаторы рейтингов")
    st.dataframe(pivot(indicators, "indicator_ids"), use_container_width=True,
                 height=full_height(len(r.matrix.links)))
    with st.expander("Обоснования связей"):
        st.dataframe(pd.DataFrame([{"KPI": l.kpi_id, "Цели": ", ".join(l.goal_ids),
                                    "Индикаторы": ", ".join(l.indicator_ids), "Сила": l.strength,
                                    "Обоснование": l.rationale} for l in r.matrix.links]),
                     hide_index=True, use_container_width=True)
    if r.matrix.coverage:
        c = r.matrix.coverage
        st.subheader("Покрытие")
        st.markdown("\n".join([
            f"- Цели без KPI: {', '.join(c.uncovered_goals) or 'нет'}",
            f"- Непокрытые индикаторы: {', '.join(c.uncovered_indicators) or 'нет'}",
            f"- KPI-сироты: {', '.join(c.orphan_kpis + c.unlinked_kpis) or 'нет'}",
            f"- Разрывы каскада: {'; '.join(c.cascade_gaps) or 'нет'}",
        ]))


def tab_goals(r: TaskResult) -> None:
    if r.goals:
        st.markdown(f"**Университет:** {r.goals.university}\n\n**Миссия.** {r.goals.mission}")
        if r.goals.vision:
            st.markdown(f"**Видение.** {r.goals.vision}")
        st.dataframe(pd.DataFrame([{"ID": g.id, "Цель": g.title, "Описание": g.description,
                                    "Цитата из документа": g.evidence} for g in r.goals.goals]),
                     hide_index=True, use_container_width=True)
    if r.indicators:
        st.subheader("Индикаторы рейтингов")
        st.dataframe(pd.DataFrame([{"ID": i.id, "Рейтинг": i.ranking, "Индикатор": i.name, "Вес, %": i.weight,
                                    "Влияющая деятельность": "; ".join(i.influencing_activities)}
                                   for i in r.indicators.indicators]),
                     hide_index=True, use_container_width=True)


def tab_review(r: TaskResult) -> None:
    if not r.review:
        st.info("Проверка не выполнялась.")
        return
    verdict = "одобрено" if r.review.verdict == "approved" else "требуется доработка"
    st.markdown(f"**Вердикт (итерация {r.review.iteration}):** {verdict}\n\n{r.review.summary}")
    if r.review.issues:
        order = {"high": 0, "medium": 1, "low": 2}
        issues = sorted(r.review.issues, key=lambda i: order[i.priority])
        # Списком, а не таблицей: замечания длинные и в ячейках обрезались бы
        for i in issues:
            ids = f" · связано: {', '.join(i.related_ids)}" if i.related_ids else ""
            st.markdown(f"- **[{PRIORITY_RU[i.priority]}] {i.category}** → {i.addressee}: {i.description}{ids}")


def load_chart(load: pd.DataFrame) -> alt.Chart:
    """Горизонтальные бары доли вызовов по агентам и линия лимита 40%."""
    base = alt.Chart(load)
    bars = base.mark_bar(color=BAR_COLOR, cornerRadiusEnd=4, height=18).encode(
        x=alt.X("Доля, %:Q", scale=alt.Scale(domain=[0, max(50, load["Доля, %"].max() + 5)]),
                title="Доля вызовов LLM и инструментов, %"),
        y=alt.Y("Агент:N", sort="-x", title=None),
        tooltip=["Агент", "LLM", "Инструменты", "Всего", "Доля, %"],
    )
    labels = bars.mark_text(align="left", dx=4, color="#555").encode(text=alt.Text("Доля, %:Q", format=".1f"))
    limit = alt.Chart(pd.DataFrame({"x": [LIMIT * 100]}))
    rule = limit.mark_rule(color=LIMIT_COLOR, strokeDash=[4, 4], strokeWidth=2).encode(x="x:Q")
    rule_label = limit.mark_text(color=LIMIT_COLOR, align="left", dx=4, dy=-6, text="лимит 40%").encode(
        x="x:Q", y=alt.value(0))
    return (bars + labels + rule + rule_label).properties(height=40 * len(load) + 20)


def tab_logs(r: TaskResult) -> None:
    if not r.log_path or not Path(r.log_path).exists():
        st.info("Лог не найден.")
        return
    rows = compute_load(Path(r.log_path))
    load = pd.DataFrame([{"Агент": x["agent"], "LLM": x["llm"], "Инструменты": x["tools"], "Всего": x["total"],
                          "Доля, %": round(100 * x["share"], 1),
                          "Лимит 40%": "превышен" if x["over_limit"] else "в норме"} for x in rows])
    st.subheader("Нагрузка агентов")
    over = [x["agent"] for x in rows if x["over_limit"]]
    if over:
        st.error(f"Лимит 40% превышен: {', '.join(over)}")
    else:
        st.success("Ни один агент не превышает 40% вызовов LLM и инструментов.")
    c1, c2 = st.columns([3, 2])
    c1.altair_chart(load_chart(load), use_container_width=True)
    c2.dataframe(load, hide_index=True, use_container_width=True)

    st.subheader("Журнал событий")
    with open(r.log_path, encoding="utf-8") as f:
        events = pd.DataFrame([json.loads(line) for line in f])
    types = st.multiselect("Типы событий", sorted(events["event"].unique()),
                           default=[t for t in ("llm_call", "tool_call", "error", "retry") if t in set(events["event"])],
                           key="event_types")
    view = events[events["event"].isin(types)][["ts", "agent", "event", "name", "duration_ms", "status", "message",
                                                "input", "output"]]
    st.dataframe(view, hide_index=True, use_container_width=True)
    st.caption(f"Файл лога: {r.log_path}")


def show_result(r: TaskResult) -> None:
    show_summary(r)
    tabs = st.tabs(["Каскад KPI", "Матрица связей", "Цели и индикаторы", "Замечания проверяющего",
                    "Логи и нагрузка"])
    with tabs[0]:
        tab_cascade(r)
    with tabs[1]:
        tab_matrix(r)
    with tabs[2]:
        tab_goals(r)
    with tabs[3]:
        tab_review(r)
    with tabs[4]:
        tab_logs(r)


# ---------------------------------------------------------------------------
st.title("🎯 KPI Cascade")
st.caption("Многоагентное каскадирование KPI: миссия университета и глобальные рейтинги → "
           "университет → факультет → кафедра → преподаватель")

settings = sidebar_settings()
request = input_form(settings)
if request is not None:
    st.session_state["result"] = run_with_progress(request, settings)
if "result" in st.session_state:
    show_result(st.session_state["result"])
