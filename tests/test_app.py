"""Smoke-тест веб-интерфейса через streamlit.testing (без браузера)."""
from streamlit.testing.v1 import AppTest


def test_app_runs_pipeline_and_shows_tabs(settings):
    at = AppTest.from_file("../app.py", default_timeout=60)
    at.run()
    assert not at.exception
    at.selectbox(key="sample").set_value("univ_b.pdf")
    at.button(key="run").click()
    at.run()
    assert not at.exception
    assert at.success[0].value.startswith("**Завершено.**")
    assert [t.label for t in at.tabs] == ["Каскад KPI", "Матрица связей", "Цели и индикаторы",
                                         "Замечания проверяющего", "Логи и нагрузка"]
    assert any("Ни один агент не превышает 40%" in s.value for s in at.success)


def test_app_reports_failure(settings):
    at = AppTest.from_file("../app.py", default_timeout=60)
    at.run()
    at.multiselect(key="rankings").set_value([])
    at.button(key="run").click()
    at.run()
    assert not at.exception
    assert "хотя бы один рейтинг" in at.error[0].value
