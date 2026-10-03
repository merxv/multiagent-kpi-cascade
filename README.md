# KPI Cascade — многоагентная система каскадирования KPI университета

Учебный проект по курсу «Многоагентные ИИ-системы». Тема магистерской работы: «Модели и методы на основе ИИ
для стратегического согласования ключевых показателей эффективности преподавателей и сотрудников
с миссией университета и показателями глобальных рейтингов».

**Вход:** стратегический документ университета (PDF) и целевые рейтинги (QS, THE).
**Выход:** согласованный каскад KPI «университет → факультет → кафедра → преподаватель», где каждый KPI
связан со стратегической целью и рейтинговым индикатором, плюс заключение проверяющего.

Шесть агентов (оркестратор + 5 исполнителей) написаны на чистом Python, без агентных фреймворков:

| Агент | Что делает |
|---|---|
| Orchestrator | планирует, распределяет задачи, следит за лимитами, решает о доработке |
| MissionAnalyst | извлекает миссию и стратегические цели из документа |
| RankingAnalyst | определяет индикаторы рейтингов и их веса (поиск по ChromaDB) |
| KPIDesigner | формулирует SMART-KPI для всех уровней каскада |
| AlignmentMapper | строит матрицу связей «цель — индикатор — KPI», считает покрытие |
| Reviewer | проверяет результат и выносит вердикт |

Подробнее: [docs/architecture.md](docs/architecture.md), [docs/agents.md](docs/agents.md).

## Установка

Нужен Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

## Настройка `.env`

| Переменная | Значение |
|---|---|
| `LLM_PROVIDER` | `mock` (по умолчанию, без интернета и затрат), `anthropic`, `openai`, `ollama` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | ключ выбранного провайдера |
| `ANTHROPIC_MODEL`, `OPENAI_MODEL`, `OLLAMA_MODEL` | модель провайдера |
| `MAX_STEPS`, `MAX_REVISIONS`, `TASK_TIMEOUT_SEC` | лимиты оркестратора (30 / 2 / 600) |
| `LLM_TIMEOUT_SEC`, `LLM_MAX_RETRIES` | таймаут и число попыток вызова LLM (60 / 3) |
| `EMBEDDING_BACKEND` | `auto`, `sentence-transformers` или `hashing` (офлайн) |
| `OPENALEX_ENABLED`, `OPENALEX_EMAIL` | использовать ли OpenAlex API для реалистичных целевых значений KPI; e-mail для «вежливого» пула (необязательно) |

Ключи API хранятся только в `.env`, который не попадает в git.

**Провайдер `mock`** возвращает заготовленные ответы из `llm/mock_responses/<агент>/*.json`.
Заготовка выбирается по ключевым словам в запросе (например, «Меридиан» / «Вектор»), поэтому mock работает
для двух учебных примеров из `data/samples/`. Для произвольных документов нужен настоящий LLM.

## Локальная модель (Ollama)

1. Установите Ollama: https://ollama.com/download (Windows / macOS / Linux).
2. Скачайте модель (≈4.7 ГБ, нужно ≥8 ГБ оперативной памяти):
   ```bash
   ollama pull qwen2.5:7b
   ```
   Если памяти 16 ГБ и больше — лучше `qwen2.5:14b` (качественнее, но медленнее).
3. В `.env`:
   ```
   LLM_PROVIDER=ollama
   OLLAMA_MODEL=qwen2.5:7b
   OLLAMA_NUM_CTX=16384
   LLM_TIMEOUT_SEC=600
   TASK_TIMEOUT_SEC=3600
   ```
4. Убедитесь, что Ollama запущена (значок в трее / `ollama serve`), и запускайте как обычно.

На процессоре без видеокарты полный прогон занимает от 10 до 40 минут. Маленькие модели ошибаются чаще
больших: агенты переспрашивают модель с текстом ошибки (до 2 раз), но задача может завершиться
статусом `failed` или `partial` с объяснением, на каком агенте и почему.

## Построение индекса методологий рейтингов

```bash
python scripts/build_index.py
```

Индексирует `data/rankings/qs.md` и `data/rankings/the.md` в ChromaDB (`data/chroma/`).
Если индекса нет, он строится автоматически при первом запуске.
При первом запуске скачивается модель `paraphrase-multilingual-MiniLM-L12-v2`; если интернета нет,
в режиме `auto` используется офлайн-эмбеддер `hashing`.

## Веб-интерфейс

```bash
streamlit run app.py
```

Откроется браузер (http://localhost:8501). В интерфейсе:
- выбор примера или загрузка своего PDF, выбор рейтингов и уровней каскада, провайдера LLM;
- прогресс по агентам во время работы;
- вкладки «Каскад KPI», «Матрица связей», «Цели и индикаторы», «Замечания проверяющего»,
  «Логи и нагрузка» (таблица и диаграмма нагрузки агентов, журнал событий);
- скачивание `report.md` и `result.json`.

## Запуск из командной строки

```bash
python main.py --input data/samples/univ_a.pdf --rankings QS THE
python main.py --input data/samples/univ_b.pdf --rankings QS THE
python main.py --input data/samples/univ_b.pdf --rankings QS --levels faculty department teacher
```

Пример вывода:

```
Задача dd13abda8449 | провайдер LLM: mock
  План: MissionAnalyst → RankingAnalyst → KPIDesigner → AlignmentMapper → Reviewer
  [шаг 1] MissionAnalyst: «Национальный исследовательский университет «Меридиан»»: целей извлечено — 5
  [шаг 2] RankingAnalyst: QS: 9 инд., Σ весов 100%; THE: 5 инд., Σ весов 100%
  [шаг 3] KPIDesigner: итерация 1, KPI всего 12 (Университет: 3, Факультет: 3, Кафедра: 3, Преподаватель: 3)
  [шаг 4] AlignmentMapper: связей: 12, покрытие целей 60%, покрытие веса индикаторов: QS 65%, THE 66%
  [шаг 5] Reviewer: вердикт: needs_revision, замечаний 6 (высокий приоритет: 5)
  Reviewer вернул KPI на доработку (1/2)
  [шаг 6] KPIDesigner: итерация 2, KPI всего 20 (Университет: 5, Факультет: 5, Кафедра: 5, Преподаватель: 5)
  [шаг 7] AlignmentMapper: связей: 20, покрытие целей 100%, покрытие веса индикаторов: QS 95%, THE 100%
  [шаг 8] Reviewer: вердикт: approved, замечаний 2 (высокий приоритет: 0)

Статус: completed — Каскад KPI одобрен проверяющим.
Результат: outputs/dd13abda8449/result.json
Отчёт:     outputs/dd13abda8449/report.md
Лог:       logs/dd13abda8449.jsonl
```

Результаты:
- `outputs/<task_id>/result.json` — полный `TaskResult`;
- `outputs/<task_id>/report.md` — читаемый отчёт: цели, индикаторы, каскад KPI по уровням, матрица связей, замечания;
- `logs/<task_id>.jsonl` — лог всех вызовов агентов, LLM и инструментов;
- `data/state.db` — SQLite: задачи, сообщения, артефакты агентов.

## Нагрузка агентов (правило ≤40%)

```bash
python scripts/load_report.py                    # последний лог
python scripts/load_report.py logs/<task_id>.jsonl
```

```
Агент                LLM  Инстр.  Всего    Доля
-----------------------------------------------
Orchestrator           1      10     11   28.2%
KPIDesigner            2       6      8   20.5%
AlignmentMapper        2       4      6   15.4%
Reviewer               2       4      6   15.4%
RankingAnalyst         1       4      5   12.8%
MissionAnalyst         1       2      3    7.7%
-----------------------------------------------
ИТОГО                  9      30     39

OK: ни один агент не превышает 40% вызовов.
```

## Тесты и сценарии

```bash
python -m pytest -q              # все тесты (51)
python scripts/run_scenarios.py  # 10 тестовых сценариев с таблицей «пройден / не пройден»
```

Тесты работают на провайдере `mock` во временных папках и без сети: схемы, `kpi_validator`,
`coverage_calculator`, `openalex_stats`, полный прогон пайплайна, сбои (битый PDF, пустой документ,
недоступный LLM, невалидный JSON, лимиты доработок, шагов и времени, зацикливание), веб-интерфейс.
Описание сценариев: [docs/test_scenarios.md](docs/test_scenarios.md).

## Данные

- `data/rankings/` — краткие описания методологий QS и THE (WUR 3.0). **Веса нужно сверять с официальными сайтами рейтингов.**
- `data/samples/` — два вымышленных стратегических плана: исследовательский университет «Меридиан» (`univ_a`)
  и технолого-педагогический университет «Вектор» (`univ_b`) в форматах `.md` и `.pdf`.
  PDF генерируются командой `python scripts/make_sample_pdfs.py`.

## Структура репозитория

```
main.py                 CLI
app.py                  веб-интерфейс (Streamlit)
core/                   схемы, состояние (SQLite), логгер, оркестратор, отчёт, настройки
agents/                 базовый агент и 5 агентов-исполнителей
tools/                  pdf_reader, vector_search, state_store, kpi_validator, coverage_calculator, openalex_stats
llm/                    клиент LLM с провайдерами и mock-ответы
prompts/                системные промпты агентов (на русском)
data/                   методологии рейтингов и примеры стратегий
scripts/                build_index.py, load_report.py, run_scenarios.py, make_sample_pdfs.py
tests/                  pytest; tests/scenarios/ — тестовые сценарии и документы для них
docs/                   архитектура, описание агентов, тестовые сценарии
```

## Этапы

- [x] **Этап 1** — все 6 агентов, CLI, логи, отчёт о нагрузке, тесты на mock.
- [x] **Этап 2** — Streamlit-интерфейс, тесты на сбои, `openalex_stats`, 10 тестовых сценариев.
- [ ] **Этап 3** — метрики качества, `docs/limitations.md`, финальная чистка.
