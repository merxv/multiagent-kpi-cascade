"""Полный прогон пайплайна на mock-провайдере."""
import json

import pytest

from core.orchestrator import run_pipeline
from core.report import save_outputs
from core.schemas import TaskRequest
from core.state import StateStore
from llm.client import LLMClient, MockProvider
from scripts.load_report import compute_load

SAMPLES = ["data/samples/univ_a.pdf", "data/samples/univ_b.pdf"]


@pytest.mark.parametrize("path", SAMPLES)
def test_full_pipeline_on_samples(settings, path):
    request = TaskRequest(input_path=path, rankings=["QS", "THE"])
    result = run_pipeline(request, settings)

    assert result.status == "completed", result.message
    assert result.review.verdict == "approved"
    assert len(result.goals.goals) >= 3
    for level in request.levels:
        assert len(result.kpis.by_level(level)) >= 3
    assert result.kpis.validation["invalid"] == {}
    assert result.matrix.coverage.goal_coverage == 1.0

    # Лог есть и в нём есть вызовы всех 6 агентов; ни один не превышает 40%
    rows = compute_load(result.log_path)
    assert {r["agent"] for r in rows} == {"Orchestrator", "MissionAnalyst", "RankingAnalyst",
                                          "KPIDesigner", "AlignmentMapper", "Reviewer"}
    assert not any(r["over_limit"] for r in rows), rows

    # Состояние сохранено в SQLite
    store = StateStore(settings.db_path)
    assert store.get_task(result.task_id)["status"] == "completed"
    messages = store.get_messages(result.task_id)
    assert len(messages) == 2 * result.steps_used  # задание + ответ на каждом шаге
    assert store.get_artifact(result.task_id, "kpi_set")["iteration"] == result.revisions + 1

    # Выходные файлы
    folder = save_outputs(result, settings.output_dir)
    assert json.loads((folder / "result.json").read_text(encoding="utf-8"))["status"] == "completed"
    assert "## 3. Каскад KPI" in (folder / "report.md").read_text(encoding="utf-8")


def test_revision_loop_on_research_university(settings):
    result = run_pipeline(TaskRequest(input_path=SAMPLES[0], rankings=["QS", "THE"]), settings)
    assert result.revisions == 1  # первая версия возвращена проверяющим на доработку
    assert result.steps_used == 8


def test_single_ranking_and_partial_levels(settings):
    request = TaskRequest(input_path="data/samples/univ_b.md", rankings=["QS"],
                          levels=["faculty", "department", "teacher"])
    result = run_pipeline(request, settings)
    assert result.status == "completed", result.message
    assert {i.ranking for i in result.indicators.indicators} == {"QS"}
    assert all(k.level != "university" for k in result.kpis.kpis)
    assert list(result.matrix.coverage.indicator_weight_coverage) == ["QS"]


class AlwaysRejectingProvider(MockProvider):
    """Mock, у которого проверяющий никогда не одобряет результат."""

    def complete(self, system, user, agent, max_tokens):
        resp = super().complete(system, user, agent, max_tokens)
        if agent == "reviewer":
            data = json.loads(resp.text)
            data["verdict"] = "needs_revision"
            data["issues"].append({"addressee": "KPIDesigner", "priority": "high",
                                   "category": "тест", "description": "всегда плохо"})
            resp.text = json.dumps(data, ensure_ascii=False)
        return resp


def test_revision_limit_gives_partial_result(settings):
    llm = LLMClient(settings, provider=AlwaysRejectingProvider())
    result = run_pipeline(TaskRequest(input_path=SAMPLES[1], rankings=["QS", "THE"]), settings, llm)
    assert result.status == "partial"
    assert result.revisions == settings.max_revisions
    assert "MAX_REVISIONS" in result.message
    assert result.kpis is not None  # последняя версия KPI всё равно возвращается


def test_missing_file_fails_with_clear_message(settings):
    result = run_pipeline(TaskRequest(input_path="data/samples/missing.pdf", rankings=["QS"]), settings)
    assert result.status == "failed"
    assert "MissionAnalyst" in result.message and "не найден" in result.message


def test_step_limit(settings, monkeypatch):
    monkeypatch.setenv("MAX_STEPS", "3")
    from core.config import get_settings
    result = run_pipeline(TaskRequest(input_path=SAMPLES[0], rankings=["QS"]), get_settings())
    assert result.status == "partial" and "MAX_STEPS=3" in result.message
    assert result.steps_used == 3
