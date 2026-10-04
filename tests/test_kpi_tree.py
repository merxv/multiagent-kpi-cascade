"""KPIDesigner: дерево KPI от LLM -> плоский список с id и parent_id (проставляет код)."""
from agents.kpi_designer import flatten, to_tree
from core.schemas import LEVELS, KPISet, KPITree


def node(name, children=()):
    return {"name": name, "method": "m", "unit": "%", "target": 1, "period": "год", "children": list(children)}


def chain(prefix):
    return node(f"{prefix}-u", [node(f"{prefix}-f", [node(f"{prefix}-d", [node(f"{prefix}-t")])])])


def test_flatten_assigns_ids_and_parents():
    tree = KPITree.model_validate({"kpis": [chain("a"), chain("b")]})
    kpis, warnings = flatten(tree, LEVELS)
    assert warnings == []
    by_id = {k.id: k for k in kpis}
    assert sorted(by_id) == ["D-01", "D-02", "F-01", "F-02", "T-01", "T-02", "U-01", "U-02"]
    assert by_id["U-02"].name == "b-u" and by_id["U-02"].parent_id is None
    assert by_id["F-02"].parent_id == "U-02" and by_id["F-02"].level == "faculty"
    assert by_id["T-01"].parent_id == "D-01" and by_id["T-01"].level == "teacher"


def test_several_children_numbered_per_level():
    tree = KPITree.model_validate({"kpis": [
        node("u1", [node("f1"), node("f2")]),
        node("u2", [node("f3")]),
    ]})
    kpis, _ = flatten(tree, ["university", "faculty"])
    assert [(k.id, k.parent_id) for k in kpis] == [
        ("U-01", None), ("F-01", "U-01"), ("F-02", "U-01"), ("U-02", None), ("F-03", "U-02")]


def test_too_deep_tree_is_cut_with_warning():
    tree = KPITree.model_validate({"kpis": [chain("a")]})
    kpis, warnings = flatten(tree, ["faculty", "department"])  # запрошено только 2 уровня
    assert [k.level for k in kpis] == ["faculty", "department"]
    assert warnings and "«a-d»" in warnings[0]


def test_to_tree_roundtrip():
    tree = KPITree.model_validate({"kpis": [chain("a"), node("u2", [node("f2")])]})
    kpis, _ = flatten(tree, LEVELS)
    back = to_tree(KPISet(kpis=kpis))
    assert back[0]["id"] == "U-01" and back[0]["children"][0]["children"][0]["children"][0]["name"] == "a-t"
    again, _ = flatten(KPITree.model_validate({"kpis": back}), LEVELS)
    assert [(k.id, k.parent_id, k.name) for k in again] == [(k.id, k.parent_id, k.name) for k in kpis]
