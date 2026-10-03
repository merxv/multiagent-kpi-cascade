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

Ключи API хранятся только в `.env`, который не попадает в git.

**Провайдер `mock`** возвращает заготовленные ответы из `llm/mock_responses/<агент>/*.json`.
Заготовка выбирается по ключевым словам в запросе (например, «Меридиан» / «Вектор»), поэтому mock работает
для двух учебных примеров из `data/samples/`. Для произвольных документов нужен настоящий LLM.

## Построение индекса методологий рейтингов

```bash
python scripts/build_index.py
```

Индексирует `data/rankings/qs.md` и `data/rankings/the.md` в ChromaDB (`data/chroma/`).
Если индекса нет, он строится автоматически при первом запуске.
При первом запуске скачивается модель `paraphrase-multilingual-MiniLM-L12-v2`; если интернета нет,
в режиме `auto` используется офлайн-эмбеддер `hashing`.

## Запуск

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
Orchestrator           1      10     11   29.7%
KPIDesigner            2       4      6   16.2%
AlignmentMapper        2       4      6   16.2%
Reviewer               2       4      6   16.2%
RankingAnalyst         1       4      5   13.5%
MissionAnalyst         1       2      3    8.1%
-----------------------------------------------
ИТОГО                  9      28     37

OK: ни один агент не превышает 40% вызовов.
```

## Тесты

```bash
python -m pytest -q
```

Тесты работают на провайдере `mock` во временных папках: валидация схем, `kpi_validator`,
`coverage_calculator`, полный прогон пайплайна на обоих примерах, цикл доработки, лимит доработок,
лимит шагов, отсутствующий файл.

## Данные

- `data/rankings/` — краткие описания методологий QS и THE (WUR 3.0). **Веса нужно сверять с официальными сайтами рейтингов.**
- `data/samples/` — два вымышленных стратегических плана: исследовательский университет «Меридиан» (`univ_a`)
  и технолого-педагогический университет «Вектор» (`univ_b`) в форматах `.md` и `.pdf`.
  PDF генерируются командой `python scripts/make_sample_pdfs.py`.

## Структура репозитория

```
main.py                 CLI
core/                   схемы, состояние (SQLite), логгер, оркестратор, отчёт, настройки
agents/                 базовый агент и 5 агентов-исполнителей
tools/                  pdf_reader, vector_search, state_store, kpi_validator, coverage_calculator
llm/                    клиент LLM с провайдерами и mock-ответы
prompts/                системные промпты агентов (на русском)
data/                   методологии рейтингов и примеры стратегий
scripts/                build_index.py, load_report.py, make_sample_pdfs.py
tests/                  pytest
docs/                   архитектура и описание агентов
```

## Этапы

- [x] **Этап 1** — все 6 агентов, CLI, логи, отчёт о нагрузке, тесты на mock.
- [ ] **Этап 2** — Streamlit-интерфейс, тесты на сбои, `openalex_stats`, тестовые сценарии.
- [ ] **Этап 3** — ≥10 сценариев, метрики качества, `docs/limitations.md`.
