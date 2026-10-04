# Агенты системы KPI Cascade

## Сводная таблица

| Агент | Роль | Входы | Выходы | Инструменты | Критерий завершения |
|---|---|---|---|---|---|
| **Orchestrator** (`core/orchestrator.py`) | Строит план выполнения, вызывает агентов в нужном порядке, следит за лимитами, решает, отправлять ли KPI на доработку. Предметную работу не делает. | `TaskRequest` | `TaskResult` | `state_store` | Reviewer одобрил результат, или исчерпан лимит доработок/шагов/времени, или агент завершился с ошибкой |
| **MissionAnalyst** (`agents/mission_analyst.py`) | Извлекает из стратегического документа миссию и стратегические цели | путь к документу | `StrategicGoals` | `pdf_reader`, `state_store` | Извлечено ≥3 целей, у каждой есть цитата-основание, найденная в тексте документа |
| **RankingAnalyst** (`agents/ranking_analyst.py`) | Определяет индикаторы выбранных рейтингов, их веса и влияющую деятельность сотрудников | список рейтингов, `StrategicGoals` | `RankingIndicators` | `vector_search`, `state_store` | Для каждого выбранного рейтинга найдены индикаторы, сумма весов = 100% ± 1.5 |
| **KPIDesigner** (`agents/kpi_designer.py`) | Формулирует SMART-KPI для всех уровней каскада **одним вызовом LLM** | `StrategicGoals`, `RankingIndicators`, уровни, замечания Reviewer (на доработке) | `KPISet` | `kpi_validator`, `openalex_stats`, `state_store` | На каждом уровне ≥3 KPI, все KPI проходят `kpi_validator` |
| **AlignmentMapper** (`agents/alignment_mapper.py`) | Строит матрицу связей «цель — индикатор — KPI» (сила 1–3 с обоснованием) и считает покрытие | `StrategicGoals`, `RankingIndicators`, `KPISet` | `AlignmentMatrix` | `coverage_calculator`, `state_store` | Каждый KPI есть в матрице, ссылки только на существующие цели/индикаторы, покрытие посчитано |
| **Reviewer** (`agents/reviewer.py`) | Независимо проверяет результат: KPI без целей, цели без KPI, неучтённые индикаторы с большим весом, разрывы каскада, перекосы | `StrategicGoals`, `RankingIndicators`, `KPISet`, `AlignmentMatrix` | `ReviewReport` | `coverage_calculator`, `state_store` | Вынесен вердикт `approved` / `needs_revision` |

Ни у одного агента нет более 5 инструментов (проверяется `assert` в `BaseAgent.__init__`);
агенту передаются только его инструменты, вызвать чужой нельзя.

## Как агенты используют LLM

Все агенты наследуют `BaseAgent` (`agents/base.py`):

1. Системный промпт агента — файл `prompts/<agent>.md` (на русском), в нём описан формат JSON-ответа.
2. `ask_llm(user, Schema, check)` вызывает LLM, извлекает JSON, валидирует его Pydantic-моделью,
   затем выполняет предметную проверку `check` (критерий завершения).
3. Если JSON невалиден или проверка нашла проблемы — LLM получает свой ответ и список ошибок
   и отвечает заново (не более 2 доработок). Иначе агент возвращает сообщение `type="error"`.

**KPIDesigner: дерево вместо ссылок.** LLM возвращает KPI деревом (`KPITree`): KPI верхнего уровня,
в поле `children` — вложенные KPI следующего уровня. id (`U-01`, `F-01`, `D-01`, `T-01`) и `parent_id`
проставляет код (`flatten` в `agents/kpi_designer.py`) по вложенности. Так модель не может ошибиться
в ссылках на родителя — небольшие локальные модели путают id, а вложенность держат хорошо.
На доработке модель видит предыдущую версию тоже деревом, с id — чтобы понимать замечания проверяющего.

### Ответ LLM у KPIDesigner — `KPITree`

```json
{"kpis": [
  {"name": "Доля публикаций в журналах Q1–Q2", "method": "публикации Q1–Q2 / все публикации × 100",
   "unit": "%", "baseline": 32, "target": 45, "period": "2030 г.", "rationale": "цель G1",
   "children": [
     {"name": "Число публикаций Q1–Q2 факультета на одного НПР", "unit": "публикаций на НПР", "target": 0.6,
      "method": "...", "period": "год", "children": [ "... кафедра → преподаватель ..." ]}
   ]}
]}
```

**RankingAnalyst: факты из базы знаний, интерпретация от LLM.** При индексации из заголовков разделов
методологий («Academic Reputation (QS-AR), вес 30%») извлекаются id, название, вес и влияющая деятельность.
LLM получает этот справочник и описывает индикаторы применительно к университету. Затем ответ сверяется
со справочником: повторы удаляются, веса берутся из базы знаний, пропущенные индикаторы дополняются.
Каждое исправление пишется в лог (событие `info`, имя `reconcile`). Так небольшие локальные модели,
которые путают подындикаторы THE с индикаторами, не ломают пайплайн.

Детерминированные инструменты (`kpi_validator`, `coverage_calculator`) не используют LLM, поэтому
их результатам можно доверять как фактам. Reviewer добавляет «автоматические» замечания по расчёту
покрытия и не может одобрить результат, если среди замечаний есть замечание высокого приоритета.

## Инструменты

| Инструмент | Модуль | Методы | Кто использует |
|---|---|---|---|
| `pdf_reader` | `tools/pdf_reader.py` | `read(path)` → текст, число страниц, фрагменты | MissionAnalyst |
| `vector_search` | `tools/vector_search.py` | `search(query, ranking, k)` → фрагменты методологий | RankingAnalyst |
| `state_store` | `tools/state_store.py` | `create_task`, `update_task`, `save_artifact`, `load_artifact` | все агенты |
| `kpi_validator` | `tools/kpi_validator.py` | `validate(kpi_set, levels)` → отчёт о полноте KPI | KPIDesigner |
| `coverage_calculator` | `tools/coverage_calculator.py` | `calculate(goals, indicators, kpi_set, links, levels)` → `CoverageReport` | AlignmentMapper, Reviewer |
| `openalex_stats` | `tools/openalex_stats.py` | `benchmarks(country_code, year)` → публикационные ориентиры OpenAlex или `{"available": false, "error": ...}` | KPIDesigner |

## Формат сообщений

Все сообщения — модель `AgentMessage` (`core/schemas.py`), предметные данные лежат в `payload`.

### Задание (Orchestrator → MissionAnalyst)

```json
{
  "message_id": "6f1c2a9e-3b0f-4a43-9d0e-2a51f4f1d6b7",
  "task_id": "dd13abda8449",
  "sender": "Orchestrator",
  "receiver": "MissionAnalyst",
  "type": "task",
  "payload": {"input_path": "data/samples/univ_a.pdf"},
  "timestamp": "2026-10-02T22:20:11.512Z"
}
```

### Результат MissionAnalyst — `StrategicGoals`

```json
{
  "type": "result", "sender": "MissionAnalyst", "receiver": "Orchestrator",
  "payload": {
    "university": "Национальный исследовательский университет «Меридиан»",
    "mission": "Создавать новое знание мирового уровня и готовить лидеров...",
    "vision": "К 2030 году — признанный в мире исследовательский университет...",
    "goals": [
      {"id": "G1", "title": "Исследования мирового уровня",
       "description": "Рост доли публикаций в журналах Q1–Q2 до 45% и нормализованной цитируемости.",
       "evidence": "Наша цель — к 2030 году увеличить долю публикаций в журналах первого и второго квартилей до 45%..."}
    ]
  }
}
```

### Результат RankingAnalyst — `RankingIndicators`

```json
{"indicators": [
  {"id": "QS-CPF", "ranking": "QS", "name": "Citations per Faculty", "weight": 20,
   "description": "Цитирования публикаций за 5 лет на одного преподавателя (Scopus, с нормализацией).",
   "influencing_activities": ["публикации в журналах Q1–Q2", "открытый доступ", "международное соавторство"]}
]}
```

### Задание KPIDesigner на доработке

```json
{
  "goals": {"...": "StrategicGoals"},
  "indicators": {"...": "RankingIndicators"},
  "levels": ["university", "faculty", "department", "teacher"],
  "iteration": 2,
  "review": {"...": "ReviewReport предыдущей итерации"},
  "previous_kpis": {"...": "KPISet предыдущей итерации"}
}
```

### Результат KPIDesigner — `KPISet`

```json
{
  "university": "Национальный исследовательский университет «Меридиан»",
  "iteration": 2,
  "kpis": [
    {"id": "U-01", "name": "Доля публикаций в журналах Q1–Q2 (Scopus/WoS)", "level": "university",
     "method": "число публикаций Q1–Q2 / общее число публикаций университета × 100", "unit": "%",
     "baseline": 32, "target": 45, "period": "2030 г. (ежегодный мониторинг)", "parent_id": null,
     "rationale": "Прямо следует из цели G1"},
    {"id": "F-01", "name": "Число публикаций Q1–Q2 факультета на одного НПР", "level": "faculty",
     "method": "публикации Q1–Q2 факультета за год / число НПР факультета (в ставках)",
     "unit": "публикаций на НПР", "baseline": 0.4, "target": 0.6, "period": "год", "parent_id": "U-01",
     "rationale": "Вклад факультета в рост доли Q1–Q2"}
  ],
  "validation": {"total": 20, "valid": 20, "invalid": {}, "set_problems": [],
                 "per_level": {"university": 5, "faculty": 5, "department": 5, "teacher": 5}}
}
```

### Результат AlignmentMapper — `AlignmentMatrix`

```json
{
  "links": [
    {"kpi_id": "U-01", "goal_ids": ["G1"], "indicator_ids": ["QS-CPF", "QS-AR", "THE-QUAL", "THE-ENV"],
     "strength": 3, "rationale": "Публикации Q1–Q2 напрямую влияют на цитируемость и академическую репутацию"}
  ],
  "coverage": {
    "goal_coverage": 1.0, "uncovered_goals": [],
    "indicator_weight_coverage": {"QS": 95.0, "THE": 100.0},
    "uncovered_indicators": ["QS-ISR"], "orphan_kpis": [], "unlinked_kpis": [], "cascade_gaps": []
  }
}
```

### Результат Reviewer — `ReviewReport` (сообщение `type="review"`)

```json
{
  "verdict": "needs_revision",
  "summary": "Каскад сильно смещён в сторону исследовательской деятельности...",
  "iteration": 1,
  "issues": [
    {"addressee": "KPIDesigner", "priority": "high", "category": "цели без KPI",
     "description": "Нет KPI для стратегических целей: G2, G4", "related_ids": ["G2", "G4"]},
    {"addressee": "KPIDesigner", "priority": "high", "category": "перекос",
     "description": "KPI преподавателя (T-01…T-03) стимулируют только исследования...",
     "related_ids": ["T-01", "T-02", "T-03"]}
  ]
}
```

### Сообщение об ошибке

```json
{
  "type": "error", "sender": "MissionAnalyst", "receiver": "Orchestrator",
  "payload": {"agent": "MissionAnalyst", "error": "инструмент сообщил об ошибке: Файл не найден: data/samples/missing.pdf"}
}
```

### Итог задачи — `TaskResult`

```json
{
  "task_id": "dd13abda8449", "status": "completed", "message": "Каскад KPI одобрен проверяющим.",
  "request": {"input_path": "data/samples/univ_a.pdf", "rankings": ["QS", "THE"], "levels": ["..."]},
  "plan": {"steps": [{"agent": "MissionAnalyst", "purpose": "..."}], "rationale": "..."},
  "goals": {}, "indicators": {}, "kpis": {}, "matrix": {}, "review": {},
  "revisions": 1, "steps_used": 8, "elapsed_sec": 1.2, "log_path": "logs/dd13abda8449.jsonl"
}
```

## Запись лога (`logs/<task_id>.jsonl`)

```json
{"ts": "2026-10-02T22:20:11.6Z", "task_id": "dd13abda8449", "agent": "KPIDesigner", "event": "tool_call",
 "name": "kpi_validator.validate", "duration_ms": 0.4, "tokens_in": null, "tokens_out": null,
 "input": "{\"kpi_set\": ...}", "output": "{\"total\": 12, \"valid\": 12, ...}", "status": "ok", "message": null}
```

Типы событий: `agent_start`, `agent_end`, `llm_call`, `tool_call`, `error`, `retry`, `message`, `info`.
