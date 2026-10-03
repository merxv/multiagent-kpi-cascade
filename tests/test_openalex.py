"""Тесты инструмента openalex_stats без сети: HTTP-ответы подменяются."""
import json

from core.config import get_settings
from core.orchestrator import run_pipeline
from core.schemas import TaskRequest
from llm.client import LLMClient, MockProvider
from tools.openalex_stats import OpenAlexStats


def fake_api(url: str, timeout: float) -> dict:
    """Имитация OpenAlex API."""
    if "/institutions?" in url:
        return {"results": [
            {"works_count": 90000, "summary_stats": {"2yr_mean_citedness": 1.2},
             "counts_by_year": [{"year": 2023, "works_count": 4000}]},
            {"works_count": 50000, "summary_stats": {"2yr_mean_citedness": 0.8},
             "counts_by_year": [{"year": 2023, "works_count": 2000}]},
            {"works_count": 10000, "summary_stats": {"2yr_mean_citedness": 1.0},
             "counts_by_year": [{"year": 2023, "works_count": 1000}]},
        ]}
    if "countries_distinct_count" in url:
        return {"meta": {"count": 200}}
    if "is_oa" in url:
        return {"meta": {"count": 400}}
    return {"meta": {"count": 1000}}


def enabled(settings, monkeypatch):
    monkeypatch.setenv("OPENALEX_ENABLED", "true")
    return get_settings()


def test_benchmarks_computed(settings, monkeypatch):
    stats = OpenAlexStats(enabled(settings, monkeypatch), fetch=fake_api).benchmarks("RU", 2023)
    assert stats["available"] is True
    assert stats["works_total"] == 1000
    assert stats["intl_collab_share_pct"] == 20.0
    assert stats["open_access_share_pct"] == 40.0
    assert stats["median_works_per_university"] == 2000
    assert stats["median_2yr_mean_citedness"] == 1.0


def test_cache_avoids_repeated_requests(settings, monkeypatch):
    s = enabled(settings, monkeypatch)
    calls = []

    def counting(url, timeout):
        calls.append(url)
        return fake_api(url, timeout)

    OpenAlexStats(s, fetch=counting).benchmarks("RU", 2023)
    assert len(calls) == 4
    # Новый экземпляр читает кэш с диска — сеть не нужна
    second = OpenAlexStats(s, fetch=lambda url, timeout: (_ for _ in ()).throw(OSError("offline")))
    assert second.benchmarks("RU", 2023)["available"] is True
    assert json.loads(s.openalex_cache_path.read_text(encoding="utf-8"))


def test_unavailable_api_does_not_fail(settings, monkeypatch):
    monkeypatch.setenv("OPENALEX_MAX_RETRIES", "2")
    s = enabled(settings, monkeypatch)
    attempts = []

    def down(url, timeout):
        attempts.append(url)
        raise TimeoutError("timed out")

    stats = OpenAlexStats(s, fetch=down).benchmarks("RU", 2023)
    assert stats["available"] is False and "недоступен" in stats["error"]
    assert len(attempts) == 2  # были повторные попытки


def test_disabled(settings):
    stats = OpenAlexStats(settings).benchmarks()
    assert stats == {"available": False, "error": "OpenAlex отключён (OPENALEX_ENABLED=false)"}


class RecordingProvider(MockProvider):
    """Mock, который запоминает запросы агентов."""

    def __init__(self):
        super().__init__()
        self.prompts = {}

    def complete(self, system, user, agent, max_tokens):
        self.prompts.setdefault(agent, []).append(user)
        return super().complete(system, user, agent, max_tokens)


def test_kpi_designer_gets_benchmarks_in_prompt(settings, monkeypatch):
    s = enabled(settings, monkeypatch)
    monkeypatch.setattr("tools.openalex_stats.http_get_json", fake_api)
    provider = RecordingProvider()
    result = run_pipeline(TaskRequest(input_path="data/samples/univ_b.pdf", rankings=["QS"]), s,
                          LLMClient(s, provider=provider))
    assert result.status == "completed"
    with open(result.log_path, encoding="utf-8") as f:
        events = [json.loads(line) for line in f]
    tool = [e for e in events if e["name"] == "openalex_stats.benchmarks"]
    assert len(tool) == 1 and '"available": true' in tool[0]["output"]
    assert '"intl_collab_share_pct": 20.0' in provider.prompts["kpi_designer"][0]


def test_kpi_designer_works_without_openalex(settings):
    provider = RecordingProvider()
    result = run_pipeline(TaskRequest(input_path="data/samples/univ_b.pdf", rankings=["QS"]), settings,
                          LLMClient(settings, provider=provider))
    assert result.status == "completed"
    assert "Справочные данные OpenAlex недоступны" in provider.prompts["kpi_designer"][0]
