"""Базовый класс агента: общая логика для всех агентов.

Что умеет BaseAgent:
  * загружает свой системный промпт из prompts/<slug>.md;
  * вызывает инструменты только из своего списка (не более 5) и логирует каждый вызов;
  * вызывает LLM, требует JSON, валидирует его Pydantic-моделью и при ошибке
    повторяет запрос с текстом ошибки (не более 2 доработок);
  * оборачивает работу в handle(): AgentMessage(task) -> AgentMessage(result | error).
"""
import json
import re
import time
from pathlib import Path
from typing import Callable, TypeVar

from pydantic import BaseModel, ValidationError

from core.logger import RunLogger
from core.schemas import AgentMessage
from llm.client import LLMClient, LLMError
from tools import ToolError

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
MAX_TOOLS = 5
MAX_FIX_ATTEMPTS = 2  # сколько раз можно переспросить LLM после невалидного ответа

T = TypeVar("T", bound=BaseModel)


class AgentError(Exception):
    """Агент не смог выполнить задачу. Сообщение — понятное, на русском."""

    def __init__(self, agent: str, message: str):
        super().__init__(f"{agent}: {message}")
        self.agent = agent
        self.message = message


def extract_json(text: str) -> dict:
    """Достаёт JSON-объект из ответа LLM (в т.ч. если он обёрнут в ```json ... ```)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("в ответе нет JSON-объекта")
    return json.loads(text[start:end + 1])


def to_json(obj) -> str:
    """Удобная сериализация моделей/словарей для вставки в промпт."""
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json")
    return json.dumps(obj, ensure_ascii=False, indent=1)


class BaseAgent:
    name = "BaseAgent"  # имя в сообщениях и логах
    slug = "base"  # имя файла промпта и папки mock-ответов
    tool_names: tuple[str, ...] = ()  # какие инструменты разрешены агенту
    result_type = "result"  # тип ответного сообщения

    def __init__(self, llm: LLMClient, logger: RunLogger, tools: dict, settings):
        assert len(self.tool_names) <= MAX_TOOLS, f"{self.name}: больше {MAX_TOOLS} инструментов"
        missing = set(self.tool_names) - set(tools)
        assert not missing, f"{self.name}: не переданы инструменты {missing}"
        self.tools = {n: tools[n] for n in self.tool_names}  # чужие инструменты агенту не видны
        self.llm = llm
        self.logger = logger
        self.settings = settings
        self.system_prompt = (PROMPTS_DIR / f"{self.slug}.md").read_text(encoding="utf-8")

    # --- инструменты --------------------------------------------------------
    def call_tool(self, tool: str, method: str, **kwargs):
        """Вызывает метод инструмента и пишет в лог событие tool_call."""
        if tool not in self.tools:
            raise AgentError(self.name, f"инструмент '{tool}' не разрешён этому агенту")
        started = time.perf_counter()
        try:
            result = getattr(self.tools[tool], method)(**kwargs)
        except Exception as e:
            self.logger.log("tool_call", self.name, f"{tool}.{method}", input=kwargs,
                            duration_ms=(time.perf_counter() - started) * 1000,
                            status="error", message=str(e))
            raise
        self.logger.log("tool_call", self.name, f"{tool}.{method}", input=kwargs, output=result,
                        duration_ms=(time.perf_counter() - started) * 1000)
        return result

    # --- LLM ----------------------------------------------------------------
    def ask_llm(self, user: str, schema: type[T],
                check: Callable[[T], list[str]] | None = None) -> T:
        """Запрашивает у LLM JSON, валидирует его схемой и предметной проверкой check.

        check(obj) возвращает список проблем; пустой список — ответ принят.
        При ошибке LLM получает свой ответ и список ошибок и пробует снова.
        """
        prompt = user
        problems: list[str] = []
        for attempt in range(1 + MAX_FIX_ATTEMPTS):
            started = time.perf_counter()
            try:
                resp = self.llm.complete(self.system_prompt, prompt, agent=self.slug,
                                         on_retry=self._log_llm_retry)
            except LLMError as e:
                self.logger.log("llm_call", self.name, self.llm.model_name, input=prompt,
                                duration_ms=(time.perf_counter() - started) * 1000,
                                status="error", message=str(e))
                raise AgentError(self.name, str(e)) from e
            self.logger.log("llm_call", self.name, resp.model, input=prompt, output=resp.text,
                            duration_ms=(time.perf_counter() - started) * 1000,
                            tokens_in=resp.tokens_in, tokens_out=resp.tokens_out)
            try:
                obj = schema.model_validate(extract_json(resp.text))
                problems = check(obj) if check else []
            except (ValueError, ValidationError) as e:  # json.JSONDecodeError — подкласс ValueError
                problems = [f"ответ не соответствует формату: {e}"]
            if not problems:
                return obj
            self.logger.log("retry", self.name, "llm_validation", status="error",
                            message=f"попытка {attempt + 1}: " + "; ".join(problems)[:500])
            # Повторный запрос: исходное задание + предыдущий ответ + список ошибок
            prompt = (f"{user}\n\n---\nТвой предыдущий ответ:\n{resp.text[:12000]}\n\n"
                      f"В нём есть ошибки, исправь их и верни полный JSON заново:\n- "
                      + "\n- ".join(problems))
        raise AgentError(self.name, "LLM не вернул корректный результат после "
                         f"{1 + MAX_FIX_ATTEMPTS} попыток: " + "; ".join(problems)[:500])

    def _log_llm_retry(self, attempt: int, error: Exception) -> None:
        self.logger.log("retry", self.name, self.llm.model_name, status="error",
                        message=f"сбой LLM, попытка {attempt}: {type(error).__name__}: {error}")

    # --- обработка сообщения --------------------------------------------------
    def handle(self, message: AgentMessage) -> AgentMessage:
        """Принимает задание, выполняет run() и возвращает результат или сообщение об ошибке."""
        self.logger.log("agent_start", self.name, input=message.payload)
        started = time.perf_counter()
        try:
            result = self.run(message.payload)
            payload, msg_type = result.model_dump(mode="json"), self.result_type
            self.logger.log("agent_end", self.name, output=payload,
                            duration_ms=(time.perf_counter() - started) * 1000)
        except Exception as e:
            if isinstance(e, AgentError):
                text = e.message
            elif isinstance(e, ToolError):
                text = f"инструмент сообщил об ошибке: {e}"
            else:
                text = f"непредвиденная ошибка {type(e).__name__}: {e}"
            payload, msg_type = {"agent": self.name, "error": text}, "error"
            self.logger.log("error", self.name, status="error", message=text,
                            duration_ms=(time.perf_counter() - started) * 1000)
        return AgentMessage(task_id=message.task_id, sender=self.name, receiver=message.sender,
                            type=msg_type, payload=payload)

    def run(self, payload: dict) -> BaseModel:
        """Предметная работа агента. Переопределяется в наследниках."""
        raise NotImplementedError

    def save(self, kind: str, obj: BaseModel) -> int:
        """Сохраняет результат агента как артефакт в SQLite (через инструмент state_store)."""
        return self.call_tool("state_store", "save_artifact", agent=self.name, kind=kind,
                              payload=obj.model_dump(mode="json"))
