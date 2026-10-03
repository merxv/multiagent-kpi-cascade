import pytest
from pydantic import ValidationError

from core.schemas import (AgentMessage, AlignmentLink, KPISet, RankingIndicator, ReviewReport,
                          StrategicGoals, TaskRequest)


def test_task_request_defaults_and_ordering():
    req = TaskRequest(input_path="x.pdf", rankings=["THE", "QS", "QS"], levels=["teacher", "university"])
    assert req.rankings == ["THE", "QS"]  # повторы убраны
    assert req.levels == ["university", "teacher"]  # уровни упорядочены сверху вниз
    assert TaskRequest(input_path="x.pdf", rankings=["QS"]).levels == [
        "university", "faculty", "department", "teacher"]


def test_task_request_rejects_unknown_ranking_and_empty_lists():
    with pytest.raises(ValidationError):
        TaskRequest(input_path="x.pdf", rankings=["ARWU"])
    with pytest.raises(ValidationError):
        TaskRequest(input_path="x.pdf", rankings=[])


def test_agent_message_json_roundtrip():
    msg = AgentMessage(task_id="t1", sender="Orchestrator", receiver="KPIDesigner",
                       type="task", payload={"a": 1})
    restored = AgentMessage.model_validate_json(msg.model_dump_json())
    assert restored == msg
    with pytest.raises(ValidationError):
        AgentMessage(task_id="t1", sender="a", receiver="b", type="unknown", payload={})


def test_domain_models_validation():
    with pytest.raises(ValidationError):
        RankingIndicator(id="QS-AR", ranking="QS", name="AR", weight=130)
    with pytest.raises(ValidationError):
        AlignmentLink(kpi_id="K1", strength=5)
    with pytest.raises(ValidationError):
        ReviewReport(verdict="maybe", summary="")
    with pytest.raises(ValidationError):
        StrategicGoals(university="U", mission="M", goals=[{"id": "G1", "title": "t"}])  # нет evidence


def test_kpi_keeps_integer_targets():
    kpis = KPISet(kpis=[{"id": "K1", "name": "n", "target": 45, "baseline": 0.5}])
    assert kpis.kpis[0].target == 45 and isinstance(kpis.kpis[0].target, int)
    assert kpis.kpis[0].baseline == 0.5
