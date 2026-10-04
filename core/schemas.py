"""Pydantic-модели: сообщения между агентами и предметные данные.

Все модели сериализуются в JSON (model_dump(mode="json")), поэтому их можно
хранить в SQLite, писать в логи и передавать между агентами.
"""
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

# Уровни каскада KPI сверху вниз
LEVELS = ["university", "faculty", "department", "teacher"]
LEVEL_NAMES_RU = {
    "university": "Университет",
    "faculty": "Факультет",
    "department": "Кафедра",
    "teacher": "Преподаватель",
}
RANKINGS = ["QS", "THE"]


# ---------------------------------------------------------------------------
# Сообщение между агентами
# ---------------------------------------------------------------------------
class AgentMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    sender: str  # имя агента-отправителя
    receiver: str  # имя агента-получателя
    type: Literal["task", "result", "review", "error"]
    payload: dict  # сериализованная предметная модель (StrategicGoals, KPISet и т.д.)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Вход задачи
# ---------------------------------------------------------------------------
class TaskRequest(BaseModel):
    task_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    input_path: str  # путь к PDF (или .md/.txt) стратегического документа
    rankings: list[Literal["QS", "THE"]] = Field(min_length=1)
    levels: list[Literal["university", "faculty", "department", "teacher"]] = Field(
        default_factory=lambda: list(LEVELS)
    )

    @field_validator("rankings", "levels")
    @classmethod
    def _unique(cls, v):
        # Убираем повторы, сохраняя порядок
        return list(dict.fromkeys(v))

    @field_validator("levels")
    @classmethod
    def _ordered(cls, v):
        if not v:
            raise ValueError("нужен хотя бы один уровень каскада")
        # Уровни всегда идут сверху вниз, независимо от порядка ввода
        return sorted(v, key=LEVELS.index)


# ---------------------------------------------------------------------------
# MissionAnalyst
# ---------------------------------------------------------------------------
class StrategicGoal(BaseModel):
    id: str  # например, "G1"
    title: str
    description: str
    evidence: str  # дословная цитата-основание из документа


class StrategicGoals(BaseModel):
    university: str
    mission: str
    vision: str = ""
    goals: list[StrategicGoal]

    def summary(self) -> str:
        return f"«{self.university}»: целей извлечено — {len(self.goals)}"


# ---------------------------------------------------------------------------
# RankingAnalyst
# ---------------------------------------------------------------------------
class RankingIndicator(BaseModel):
    id: str  # например, "QS-AR"
    ranking: Literal["QS", "THE"]
    name: str
    weight: float = Field(ge=0, le=100)  # вес в процентах
    description: str = ""
    influencing_activities: list[str] = Field(default_factory=list)


class RankingIndicators(BaseModel):
    indicators: list[RankingIndicator]

    def weight_sum(self, ranking: str) -> float:
        return sum(i.weight for i in self.indicators if i.ranking == ranking)

    def summary(self) -> str:
        rankings = sorted({i.ranking for i in self.indicators})
        parts = [f"{r}: {sum(1 for i in self.indicators if i.ranking == r)} инд., "
                 f"Σ весов {self.weight_sum(r):g}%" for r in rankings]
        return "; ".join(parts)


# ---------------------------------------------------------------------------
# KPIDesigner
# ---------------------------------------------------------------------------
class KPI(BaseModel):
    """Один KPI. Поля намеренно необязательные: полноту проверяет kpi_validator,
    чтобы агент получил понятный список недочётов, а не ошибку схемы."""
    id: str
    name: str
    level: str = ""
    method: str = ""  # формула / метод расчёта
    unit: str = ""
    target: int | float | str | None = None
    baseline: int | float | str | None = None
    period: str = ""
    parent_id: str | None = None
    rationale: str = ""


class KPISet(BaseModel):
    university: str = ""
    iteration: int = 1
    kpis: list[KPI]
    validation: dict | None = None  # отчёт kpi_validator (заполняет агент)

    def by_level(self, level: str) -> list[KPI]:
        return [k for k in self.kpis if k.level == level]

    def summary(self) -> str:
        counts = {}
        for k in self.kpis:
            counts[k.level] = counts.get(k.level, 0) + 1
        per_level = ", ".join(f"{LEVEL_NAMES_RU.get(l, l)}: {n}" for l, n in counts.items())
        return f"итерация {self.iteration}, KPI всего {len(self.kpis)} ({per_level})"


class KPINode(BaseModel):
    """KPI в виде узла дерева — так его возвращает LLM.

    Дочерние KPI (уровнем ниже) вложены в поле children, поэтому модели не нужно
    придумывать id и ссылаться на родителя: id и parent_id проставляет код (KPIDesigner.flatten).
    """
    name: str
    method: str = ""
    unit: str = ""
    target: int | float | str | None = None
    baseline: int | float | str | None = None
    period: str = ""
    rationale: str = ""
    children: list["KPINode"] = Field(default_factory=list)


class KPITree(BaseModel):
    """Ответ LLM у KPIDesigner: список KPI верхнего уровня с вложенными дочерними."""
    kpis: list[KPINode]


# ---------------------------------------------------------------------------
# AlignmentMapper
# ---------------------------------------------------------------------------
class AlignmentLink(BaseModel):
    """Связь одного KPI со стратегическими целями и рейтинговыми индикаторами."""
    kpi_id: str
    goal_ids: list[str] = Field(default_factory=list)
    indicator_ids: list[str] = Field(default_factory=list)
    strength: int = Field(ge=1, le=3)  # 1 — слабая, 3 — сильная
    rationale: str = ""


class AlignmentDraft(BaseModel):
    """То, что возвращает LLM у AlignmentMapper (покрытие считает инструмент)."""
    links: list[AlignmentLink]


class CoverageReport(BaseModel):
    goal_coverage: float  # доля целей, у которых есть хотя бы один KPI (0..1)
    uncovered_goals: list[str] = Field(default_factory=list)
    indicator_weight_coverage: dict[str, float] = Field(default_factory=dict)  # рейтинг -> % веса
    uncovered_indicators: list[str] = Field(default_factory=list)
    orphan_kpis: list[str] = Field(default_factory=list)  # KPI без связи с целями
    unlinked_kpis: list[str] = Field(default_factory=list)  # KPI, которых нет в матрице
    cascade_gaps: list[str] = Field(default_factory=list)  # разрывы каскада (текстом)


class AlignmentMatrix(BaseModel):
    links: list[AlignmentLink]
    coverage: CoverageReport | None = None

    def summary(self) -> str:
        if not self.coverage:
            return f"связей: {len(self.links)}"
        cov = self.coverage
        ind = ", ".join(f"{r} {v:.0f}%" for r, v in cov.indicator_weight_coverage.items())
        return (f"связей: {len(self.links)}, покрытие целей {cov.goal_coverage:.0%}, "
                f"покрытие веса индикаторов: {ind}")


# ---------------------------------------------------------------------------
# Reviewer
# ---------------------------------------------------------------------------
class ReviewIssue(BaseModel):
    addressee: Literal["KPIDesigner", "AlignmentMapper", "MissionAnalyst", "RankingAnalyst"]
    priority: Literal["high", "medium", "low"]
    category: str
    description: str
    related_ids: list[str] = Field(default_factory=list)


class ReviewReport(BaseModel):
    verdict: Literal["approved", "needs_revision"]
    summary: str
    issues: list[ReviewIssue] = Field(default_factory=list)
    iteration: int = 1

    def summary_text(self) -> str:
        high = sum(1 for i in self.issues if i.priority == "high")
        return f"вердикт: {self.verdict}, замечаний {len(self.issues)} (высокий приоритет: {high})"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
class PlanStep(BaseModel):
    agent: str
    purpose: str = ""


class ExecutionPlan(BaseModel):
    steps: list[PlanStep]
    rationale: str = ""


class TaskResult(BaseModel):
    task_id: str
    status: Literal["completed", "partial", "failed"]
    message: str  # понятное сообщение на русском
    request: TaskRequest
    plan: ExecutionPlan | None = None
    goals: StrategicGoals | None = None
    indicators: RankingIndicators | None = None
    kpis: KPISet | None = None
    matrix: AlignmentMatrix | None = None
    review: ReviewReport | None = None
    revisions: int = 0
    steps_used: int = 0
    elapsed_sec: float = 0.0
    log_path: str = ""
