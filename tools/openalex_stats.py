"""openalex_stats — реальные публикационные показатели из OpenAlex API (https://openalex.org).

Нужен KPIDesigner, чтобы целевые значения KPI были реалистичными. Инструмент возвращает
ориентиры по стране за год:
  * число публикаций с аффилиацией организаций страны;
  * доля публикаций с международным соавторством;
  * доля публикаций в открытом доступе;
  * по крупнейшим университетам страны: медиана публикаций в год на университет
    и медиана 2-летней средней цитируемости (2yr_mean_citedness).

Надёжность:
  * ответы кэшируются в JSON-файле (по URL запроса) на OPENALEX_CACHE_DAYS дней;
  * повторные попытки с экспоненциальной паузой (tenacity);
  * если API недоступен — инструмент НЕ падает, а возвращает {"available": False, "error": ...},
    и KPIDesigner работает без справочных данных.
"""
import json
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path

from tenacity import Retrying, stop_after_attempt, wait_exponential

API_URL = "https://api.openalex.org"


def http_get_json(url: str, timeout: float) -> dict:
    """GET-запрос, возвращающий JSON. Вынесен в функцию, чтобы в тестах его можно было подменить."""
    req = urllib.request.Request(url, headers={"User-Agent": "kpi-cascade (educational project)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class OpenAlexStats:
    name = "openalex_stats"

    def __init__(self, settings, fetch=None):
        self.settings = settings
        self.fetch = fetch  # функцию запроса можно подменить (в тестах); по умолчанию http_get_json
        self.cache_path = Path(settings.openalex_cache_path)

    # --- кэш ------------------------------------------------------------------
    def _load_cache(self) -> dict:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_cache(self, cache: dict) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    def _get(self, path: str, params: dict, cache: dict) -> dict:
        """Запрос к API с кэшем и повторными попытками."""
        if self.settings.openalex_email:
            params["mailto"] = self.settings.openalex_email  # «вежливый» пул OpenAlex
        if self.settings.openalex_api_key:
            params["api_key"] = self.settings.openalex_api_key
        url = f"{API_URL}/{path}?{urllib.parse.urlencode(params)}"
        # Ключ кэша — URL без персональных параметров
        key = url.split("&mailto=")[0].split("&api_key=")[0]
        entry = cache.get(key)
        if entry and time.time() - entry["saved_at"] < self.settings.openalex_cache_days * 86400:
            return entry["data"]
        retryer = Retrying(stop=stop_after_attempt(max(1, self.settings.openalex_max_retries)),
                           wait=wait_exponential(multiplier=self.settings.retry_base_sec, max=10),
                           reraise=True)
        data = retryer(self.fetch or http_get_json, url, self.settings.openalex_timeout_sec)
        cache[key] = {"saved_at": time.time(), "data": data}
        return data

    # --- метод инструмента -----------------------------------------------------
    def benchmarks(self, country_code: str | None = None, year: int | None = None) -> dict:
        """Ориентиры публикационной активности по стране за год."""
        if not self.settings.openalex_enabled:
            return {"available": False, "error": "OpenAlex отключён (OPENALEX_ENABLED=false)"}
        country = (country_code or self.settings.openalex_country).lower()
        year = year or time.localtime().tm_year - 2  # последний год с почти полными данными
        works_filter = f"publication_year:{year},institutions.country_code:{country}"
        cache = self._load_cache()
        try:
            total = self._get("works", {"filter": works_filter, "per_page": 1}, cache)["meta"]["count"]
            intl = self._get("works", {"filter": works_filter + ",countries_distinct_count:>1",
                                       "per_page": 1}, cache)["meta"]["count"]
            oa = self._get("works", {"filter": works_filter + ",open_access.is_oa:true",
                                     "per_page": 1}, cache)["meta"]["count"]
            unis = self._get("institutions", {"filter": f"country_code:{country},type:education",
                                              "sort": "works_count:desc", "per_page": 50}, cache)["results"]
        except Exception as e:  # сеть, таймаут, неожиданный формат ответа
            self._save_cache(cache)  # то, что успели получить, сохраняем
            return {"available": False, "error": f"OpenAlex недоступен: {type(e).__name__}: {e}"}
        self._save_cache(cache)

        works_per_uni = [c["works_count"] for u in unis for c in u.get("counts_by_year", [])
                         if c.get("year") == year]
        citedness = [u["summary_stats"]["2yr_mean_citedness"] for u in unis
                     if u.get("summary_stats", {}).get("2yr_mean_citedness") is not None]
        return {
            "available": True,
            "source": "OpenAlex",
            "country": country.upper(),
            "year": year,
            "works_total": total,
            "intl_collab_share_pct": round(100 * intl / total, 1) if total else None,
            "open_access_share_pct": round(100 * oa / total, 1) if total else None,
            "universities_sampled": len(unis),
            "median_works_per_university": statistics.median(works_per_uni) if works_per_uni else None,
            "median_2yr_mean_citedness": round(statistics.median(citedness), 2) if citedness else None,
        }
