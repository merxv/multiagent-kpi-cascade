"""Тесты на сбои и лимиты (раздел 8 ТЗ): ни один сбой не должен ронять систему."""
import json

import pytest

from core.config import get_settings
from core.orchestrator import Orchestrator, fingerprint, run_pipeline
from core.schemas import TaskRequest
from llm.client import LLMClient, MockProvider

UNIV_A, UNIV_B = "data/samples/univ_a.pdf", "data/samples/univ_b.pdf"


def run(settings, path, provider=None, **request_kwargs):
    llm = LLMClient(settings, provider=provider) if provider else None
    request_kwargs.setdefault("rankings", ["QS", "THE"])
    return run_pipeline(TaskRequest(input_path=str(path), **request_kwargs), settings, llm)


def log_events(result) -> list[dict]:
    with open(result.log_path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


# --- входной документ ---------------------------------------------------------
def test_broken_pdf(settings, tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\n\x00\x13 this is not really a pdf \xff\xfe")
    result = run(settings, broken)
    assert result.status == "failed"
    assert "MissionAnalyst" in result.message and "повреждён" in result.message


def test_empty_document(settings, tmp_path):
    empty = tmp_path / "empty.md"
    empty.write_text("   \n", encoding="utf-8")
    result = run(settings, empty)
    assert result.status == "failed" and "пуст" in result.message


def test_unsupported_format(settings, tmp_path):
    doc = tmp_path / "plan.docx"
    doc.write_bytes(b"PK")
    result = run(settings, doc)
    assert result.status == "failed" and "Неподдерживаемый формат" in result.message


# --- LLM ----------------------------------------------------------------------
class DownProvider(MockProvider):
    """LLM, который всегда недоступен (сеть/сервер)."""
    calls = 0

    def complete(self, system, user, agent, max_tokens):
        DownProvider.calls += 1
        raise ConnectionError("сервер не отвечает")


def test_llm_unavailable(settings, monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    settings = get_settings()
    DownProvider.calls = 0
    result = run(settings, UNIV_A, DownProvider())
    assert result.status == "failed"
    assert "MissionAnalyst" in result.message and "не ответил после 3 попыток" in result.message
    # Оркестратор (план) и MissionAnalyst сделали по 3 попытки; повторы видны в логе
    assert DownProvider.calls == 6
    assert sum(e["event"] == "retry" for e in log_events(result)) == 4


def test_llm_not_configured(settings, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    result = run(get_settings(), UNIV_A)
    assert result.status == "failed" and "ANTHROPIC_API_KEY" in result.message


class FlakyJsonProvider(MockProvider):
    """Первый ответ каждому агенту — не JSON, дальше — нормальные ответы."""

    def __init__(self):
        super().__init__()
        self.seen = set()

    def complete(self, system, user, agent, max_tokens):
        if agent not in self.seen:
            self.seen.add(agent)
            return super().complete(system, user, agent, max_tokens).__class__(
                text="Конечно! Вот результат: {не json", model="mock")
        return super().complete(system, user, agent, max_tokens)


def test_invalid_json_is_retried(settings):
    result = run(settings, UNIV_B, FlakyJsonProvider())
    assert result.status == "completed", result.message
    retries = [e for e in log_events(result) if e["event"] == "retry"]
    assert len(retries) == 6  # по одному повтору на каждого из 6 агентов
    assert all("не соответствует формату" in e["message"] for e in retries)


class GarbageProvider(MockProvider):
    def complete(self, system, user, agent, max_tokens):
        resp = super().complete(system, user, agent, max_tokens)
        if agent == "kpi_designer":
            resp.text = '{"kpis": "не список"}'
        return resp


def test_invalid_json_gives_up_after_two_fixes(settings):
    result = run(settings, UNIV_B, GarbageProvider())
    assert result.status == "failed"  # KPI так и не получены
    assert "KPIDesigner" in result.message and "после 3 попыток" in result.message
    kd_calls = [e for e in log_events(result) if e["event"] == "llm_call" and e["agent"] == "KPIDesigner"]
    assert len(kd_calls) == 3  # исходный запрос + 2 доработки


def test_invalid_plan_falls_back_to_default(settings):
    class BadPlanProvider(MockProvider):
        def complete(self, system, user, agent, max_tokens):
            if agent == "orchestrator":
                return super().complete(system, user, agent, max_tokens).__class__(
                    text='{"steps": [{"agent": "Reviewer"}, {"agent": "MissionAnalyst"}]}', model="mock")
            return super().complete(system, user, agent, max_tokens)

    result = run(settings, UNIV_B, BadPlanProvider())
    assert result.status == "completed"
    assert "стандартный план" in result.plan.rationale


# --- лимиты и зацикливание ----------------------------------------------------
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


def test_revision_limit(settings, monkeypatch):
    monkeypatch.setenv("MAX_REVISIONS", "1")
    result = run(get_settings(), UNIV_A, AlwaysRejectingProvider())
    assert result.status == "partial"
    assert result.revisions == 1 and "MAX_REVISIONS=1" in result.message
    assert result.kpis is not None and result.kpis.iteration == 2  # последняя версия возвращается


def test_loop_detection(settings):
    # Для «Вектора» KPIDesigner на доработке возвращает те же KPI, поэтому AlignmentMapper
    # получил бы тот же по существу вход — оркестратор останавливается
    result = run(settings, UNIV_B, AlwaysRejectingProvider())
    assert result.status == "partial"
    assert "зацикливание" in result.message and "AlignmentMapper" in result.message
    assert result.steps_used == 6


def test_fingerprint_ignores_iteration():
    a = {"kpis": {"iteration": 1, "kpis": [{"id": "K1"}], "validation": {"total": 1}}}
    b = {"kpis": {"iteration": 2, "kpis": [{"id": "K1"}], "validation": {"total": 1}}}
    c = {"kpis": {"iteration": 2, "kpis": [{"id": "K2"}]}}
    assert fingerprint("AlignmentMapper", a) == fingerprint("AlignmentMapper", b)
    assert fingerprint("AlignmentMapper", a) != fingerprint("AlignmentMapper", c)
    assert fingerprint("AlignmentMapper", a) != fingerprint("Reviewer", a)


def test_timeout(settings, monkeypatch):
    monkeypatch.setenv("TASK_TIMEOUT_SEC", "0")
    result = run(get_settings(), UNIV_B)
    assert result.status == "partial" and "TASK_TIMEOUT_SEC" in result.message


def test_unexpected_orchestrator_error_is_caught(settings, monkeypatch):
    def boom(self, *a, **kw):
        raise RuntimeError("диск переполнен")
    monkeypatch.setattr(Orchestrator, "make_plan", boom)
    result = run(settings, UNIV_B)
    assert result.status == "failed" and "диск переполнен" in result.message


@pytest.mark.parametrize("bad", [{"rankings": ["ARWU"]}, {"rankings": []}])
def test_bad_request_is_rejected_before_run(bad):
    with pytest.raises(ValueError):
        TaskRequest(input_path=UNIV_A, **bad)
