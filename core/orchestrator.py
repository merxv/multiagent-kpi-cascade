"""Orchestrator — планирует выполнение и распределяет задачи между агентами.

Предметной работы оркестратор НЕ делает: он
  1) один раз спрашивает у LLM план (порядок агентов) и проверяет его;
  2) по очереди отправляет агентам сообщения-задания и получает результаты;
  3) по вердикту Reviewer решает, отправлять ли KPI на доработку;
  4) следит за лимитами: шаги, доработки, время, повторный вход (защита от зацикливания).
"""
import hashlib
import json
import time
from collections import deque

from agents.alignment_mapper import AlignmentMapper
from agents.base import AgentError, BaseAgent
from agents.kpi_designer import KPIDesigner
from agents.mission_analyst import MissionAnalyst
from agents.ranking_analyst import RankingAnalyst
from agents.reviewer import Reviewer
from core.config import Settings, get_settings
from core.logger import RunLogger
from core.schemas import (AgentMessage, AlignmentMatrix, ExecutionPlan, KPISet, PlanStep,
                          RankingIndicators, ReviewReport, StrategicGoals, TaskRequest, TaskResult)
from core.state import StateStore
from llm.client import LLMClient
from tools.coverage_calculator import CoverageCalculator
from tools.kpi_validator import KpiValidator
from tools.openalex_stats import OpenAlexStats
from tools.pdf_reader import PdfReader
from tools.state_store import StateStoreTool
from tools.vector_search import VectorSearch

# Какие агенты должны отработать раньше данного (зависимости по данным)
DEPENDENCIES = {
    "MissionAnalyst": [],
    "RankingAnalyst": ["MissionAnalyst"],  # ищет в методологиях то, что связано с целями
    "KPIDesigner": ["MissionAnalyst", "RankingAnalyst"],
    "AlignmentMapper": ["KPIDesigner"],
    "Reviewer": ["AlignmentMapper"],
}
DEFAULT_PLAN = list(DEPENDENCIES)
# Краткие роли агентов — передаются LLM при планировании
ROLES = {
    "MissionAnalyst": "извлекает миссию и стратегические цели из документа",
    "RankingAnalyst": "определяет индикаторы рейтингов, их веса и влияющую деятельность",
    "KPIDesigner": "формулирует SMART-KPI для всех уровней каскада",
    "AlignmentMapper": "строит матрицу связей цель — индикатор — KPI и считает покрытие",
    "Reviewer": "проверяет результат и выносит вердикт",
}
# Цикл доработки зависит от того, кому адресованы блокирующие замечания проверяющего
REVISION_STEPS = ["KPIDesigner", "AlignmentMapper", "Reviewer"]  # нужно менять сами KPI
RELINK_STEPS = ["AlignmentMapper", "Reviewer"]  # KPI в порядке, нужно поправить только связи


def revision_steps(review: dict) -> list[str]:
    """Выбирает цикл доработки по адресатам блокирующих замечаний (маршрутизация, не предметная работа)."""
    addressees = {i["addressee"] for i in review["issues"] if i.get("blocking")}
    return REVISION_STEPS if "KPIDesigner" in addressees or not addressees else RELINK_STEPS
# Под каким ключом результат агента хранится в контексте задачи
CONTEXT_KEY = {
    "MissionAnalyst": "goals",
    "RankingAnalyst": "indicators",
    "KPIDesigner": "kpis",
    "AlignmentMapper": "matrix",
    "Reviewer": "review",
}


# Служебные поля, которые меняются на каждой итерации, но не меняют суть входа агента
VOLATILE_KEYS = {"iteration", "validation"}


def _strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def fingerprint(agent: str, payload: dict) -> str:
    """SHA-256 входа агента без служебных полей (номер итерации и т.п.).

    Если KPIDesigner вернул те же KPI, что и в прошлый раз, AlignmentMapper получил бы
    тот же по существу вход — повторять такой шаг бессмысленно, это зацикливание.
    """
    text = agent + json.dumps(_strip_volatile(payload), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Orchestrator(BaseAgent):
    name = "Orchestrator"
    slug = "orchestrator"
    tool_names = ("state_store",)

    def __init__(self, request: TaskRequest, settings: Settings | None = None,
                 llm: LLMClient | None = None, on_progress=None):
        self.request = request
        settings = settings or get_settings()
        logger = RunLogger(request.task_id, settings.log_dir)
        llm = llm or LLMClient(settings)
        self.store = StateStore(settings.db_path)
        self.on_progress = on_progress or (lambda text: None)

        # Набор инструментов; каждому агенту передаются только его инструменты
        tools = {
            "pdf_reader": PdfReader(),
            "vector_search": VectorSearch(settings),
            "state_store": StateStoreTool(self.store, request.task_id),
            "kpi_validator": KpiValidator(),
            "coverage_calculator": CoverageCalculator(),
            "openalex_stats": OpenAlexStats(settings),
        }
        super().__init__(llm, logger, tools, settings)
        self.agents: dict[str, BaseAgent] = {
            cls.name: cls(llm, logger, tools, settings)
            for cls in (MissionAnalyst, RankingAnalyst, KPIDesigner, AlignmentMapper, Reviewer)
        }

    # --- планирование ---------------------------------------------------------
    def make_plan(self) -> ExecutionPlan:
        """Просит LLM составить план и проверяет его. Если план некорректен — берём план по умолчанию."""
        user = (
            f"Запрос: документ «{self.request.input_path}», рейтинги {', '.join(self.request.rankings)}, "
            f"уровни каскада {', '.join(self.request.levels)}.\n\n"
            f"Доступные агенты:\n" + "\n".join(f"- {n}: {r}" for n, r in ROLES.items())
            + f"\n\nЗависимости по данным:\n{json.dumps(DEPENDENCIES, ensure_ascii=False)}\n\n"
            "Составь план одного прохода. Верни JSON по формату из инструкции."
        )

        def check(plan: ExecutionPlan) -> list[str]:
            order = [s.agent for s in plan.steps]
            problems = [f"неизвестный агент {a}" for a in order if a not in DEPENDENCIES]
            if sorted(order) != sorted(DEPENDENCIES):
                problems.append(f"каждый агент должен встретиться ровно один раз: {', '.join(DEPENDENCIES)}")
            for i, a in enumerate(order):
                for dep in DEPENDENCIES.get(a, []):
                    if dep not in order[:i]:
                        problems.append(f"{a} должен идти после {dep}")
            return problems

        try:
            return self.ask_llm(user, ExecutionPlan, check)
        except AgentError as e:
            self.logger.log("info", self.name, message=f"план LLM не принят, используется стандартный: {e.message}")
            return ExecutionPlan(steps=[PlanStep(agent=a) for a in DEFAULT_PLAN],
                                 rationale="стандартный план (LLM не вернул корректный план)")

    # --- маршрутизация --------------------------------------------------------
    def build_payload(self, agent: str, ctx: dict, iteration: int) -> dict:
        """Собирает вход агента из результатов предыдущих шагов (только маршрутизация данных)."""
        req = self.request
        if agent == "MissionAnalyst":
            return {"input_path": req.input_path}
        if agent == "RankingAnalyst":
            return {"rankings": req.rankings, "goals": ctx["goals"]}
        if agent == "KPIDesigner":
            payload = {"goals": ctx["goals"], "indicators": ctx["indicators"],
                       "levels": req.levels, "iteration": iteration}
            if "review" in ctx:  # доработка: передаём замечания и прошлую версию
                payload.update(review=ctx["review"], previous_kpis=ctx["kpis"])
            return payload
        if agent == "AlignmentMapper":
            payload = {"goals": ctx["goals"], "indicators": ctx["indicators"], "kpis": ctx["kpis"],
                       "levels": req.levels}
            if "review" in ctx:  # доработка: передаём замечания проверяющего к связям
                payload["review"] = ctx["review"]
            return payload
        if agent == "Reviewer":
            return {"goals": ctx["goals"], "indicators": ctx["indicators"], "kpis": ctx["kpis"],
                    "matrix": ctx["matrix"], "levels": req.levels, "iteration": iteration}
        raise ValueError(f"неизвестный агент {agent}")

    def send(self, agent: str, payload: dict) -> AgentMessage:
        """Отправляет задание агенту и возвращает его ответ. Оба сообщения сохраняются в SQLite."""
        task_msg = AgentMessage(task_id=self.request.task_id, sender=self.name, receiver=agent,
                                type="task", payload=payload)
        self._record(task_msg)
        reply = self.agents[agent].handle(task_msg)
        self._record(reply)
        return reply

    def _record(self, msg: AgentMessage) -> None:
        # Журнал сообщений — это транспорт (как и лог), а не предметный вызов инструмента
        self.store.save_message(msg)
        self.logger.log("message", msg.sender, msg.type, input={"to": msg.receiver},
                        status="error" if msg.type == "error" else "ok")

    # --- основной цикл --------------------------------------------------------
    def run_task(self) -> TaskResult:
        """Выполняет задачу. Никогда не бросает исключений: любой сбой превращается в TaskResult."""
        started = time.monotonic()
        try:
            return self._run(started)
        except Exception as e:
            message = f"Непредвиденная ошибка оркестратора: {type(e).__name__}: {e}"
            self.logger.log("error", self.name, status="error", message=message)
            try:
                self.store.update_task(self.request.task_id, status="failed", message=message)
            except Exception:
                pass  # если недоступна сама БД — сообщение всё равно вернётся пользователю
            return TaskResult(task_id=self.request.task_id, status="failed", message=message,
                              request=self.request, elapsed_sec=round(time.monotonic() - started, 2),
                              log_path=str(self.logger.path))

    def _run(self, started: float) -> TaskResult:
        req, s = self.request, self.settings
        self.logger.log("agent_start", self.name, input=req)
        self.call_tool("state_store", "create_task", params=req.model_dump(mode="json"))

        plan = self.make_plan()
        self.on_progress(f"План: {' → '.join(step.agent for step in plan.steps)}")
        queue = deque(step.agent for step in plan.steps)
        ctx: dict = {}
        seen_inputs: set[str] = set()
        steps, revisions, iteration = 0, 0, 1
        status, message = None, ""

        while queue:
            agent = queue.popleft()
            # Лимиты: шаги и время
            if steps >= s.max_steps:
                status, message = "partial", f"Достигнут лимит шагов MAX_STEPS={s.max_steps} перед вызовом {agent}."
                break
            elapsed = time.monotonic() - started
            if elapsed > s.task_timeout_sec:
                status, message = "partial", (f"Превышено время выполнения TASK_TIMEOUT_SEC={s.task_timeout_sec:g} с "
                                              f"перед вызовом {agent} (шаг {steps + 1}).")
                break

            payload = self.build_payload(agent, ctx, iteration)
            # Защита от зацикливания: тот же агент с тем же по существу входом второй раз не вызывается
            digest = fingerprint(agent, payload)
            if digest in seen_inputs:
                status, message = "partial", (f"Обнаружено зацикливание: {agent} получил бы тот же по существу "
                                              f"вход, что и раньше (шаг {steps + 1}), — доработка не изменила "
                                              "результат. Выполнение остановлено, показана последняя версия.")
                break
            seen_inputs.add(digest)

            steps += 1
            self.on_progress(f"[шаг {steps}] {agent}: выполняется…")
            reply = self.send(agent, payload)
            if reply.type == "error":
                status = "partial" if "kpis" in ctx else "failed"
                message = f"Агент {agent} на шаге {steps} завершился с ошибкой: {reply.payload['error']}"
                self.on_progress(f"[шаг {steps}] {agent}: ОШИБКА — {reply.payload['error']}")
                break
            ctx[CONTEXT_KEY[agent]] = reply.payload
            self.on_progress(f"[шаг {steps}] {agent}: {self._summary(agent, reply.payload)}")
            self.call_tool("state_store", "update_task", status="running", current_step=f"{steps}:{agent}")

            if agent == "Reviewer":
                if reply.payload["verdict"] == "approved":
                    status, message = "completed", "Каскад KPI одобрен проверяющим."
                    break
                if revisions < s.max_revisions:
                    revisions += 1
                    iteration += 1
                    steps_next = revision_steps(reply.payload)
                    self.on_progress(f"Reviewer вернул на доработку ({revisions}/{s.max_revisions}): "
                                     f"{' → '.join(steps_next)}")
                    queue.extend(steps_next)
                else:
                    status, message = "partial", (f"Лимит доработок MAX_REVISIONS={s.max_revisions} исчерпан, "
                                                  "проверяющий не одобрил результат. Показана последняя версия.")
                    break

        if status is None:  # план закончился без вердикта проверяющего
            status, message = "partial", "План выполнен, но вердикт проверяющего не получен."

        self.call_tool("state_store", "update_task", status=status, message=message)
        result = TaskResult(
            task_id=req.task_id, status=status, message=message, request=req, plan=plan,
            goals=StrategicGoals.model_validate(ctx["goals"]) if "goals" in ctx else None,
            indicators=RankingIndicators.model_validate(ctx["indicators"]) if "indicators" in ctx else None,
            kpis=KPISet.model_validate(ctx["kpis"]) if "kpis" in ctx else None,
            matrix=AlignmentMatrix.model_validate(ctx["matrix"]) if "matrix" in ctx else None,
            review=ReviewReport.model_validate(ctx["review"]) if "review" in ctx else None,
            revisions=revisions, steps_used=steps,
            elapsed_sec=round(time.monotonic() - started, 2), log_path=str(self.logger.path),
        )
        self.logger.log("agent_end", self.name, output={"status": status, "message": message},
                        status="ok" if status == "completed" else status)
        return result

    @staticmethod
    def _summary(agent: str, payload: dict) -> str:
        if agent == "Reviewer":
            return ReviewReport.model_validate(payload).summary_text()
        model = {"MissionAnalyst": StrategicGoals, "RankingAnalyst": RankingIndicators,
                 "KPIDesigner": KPISet, "AlignmentMapper": AlignmentMatrix}[agent]
        return model.model_validate(payload).summary()


def run_pipeline(request: TaskRequest, settings: Settings | None = None,
                 llm: LLMClient | None = None, on_progress=None) -> TaskResult:
    """Удобная точка входа: создать оркестратор и выполнить задачу."""
    return Orchestrator(request, settings, llm, on_progress).run_task()
