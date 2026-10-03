"""Все сценарии из tests/scenarios/ как pytest-тесты (во временных папках)."""
import pytest

from scripts.run_scenarios import run_scenario, scenario_files


@pytest.mark.parametrize("path", scenario_files(), ids=lambda p: p.stem)
def test_scenario(settings, path):
    ok, errors, result = run_scenario(path)
    assert ok, errors
