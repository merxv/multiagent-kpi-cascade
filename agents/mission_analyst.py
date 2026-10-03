"""MissionAnalyst — извлекает из стратегического документа миссию и стратегические цели."""
import re

from agents.base import BaseAgent
from core.schemas import StrategicGoals

MAX_DOC_CHARS = 40000  # сколько символов документа отдаём LLM
MIN_GOALS = 3


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower().replace("ё", "е"))


def evidence_found(quote: str, text: str) -> bool:
    """Проверяет, что цитата действительно есть в документе.

    Сравниваем по словам (а не символам), т.к. при извлечении из PDF меняются
    переносы строк и пробелы. Допускаем, что ≥80% слов цитаты есть в тексте.
    """
    q = _words(quote)
    if not q:
        return False
    t = _words(text)
    if " ".join(q) in " ".join(t):
        return True
    vocab = set(t)
    return sum(w in vocab for w in q) / len(q) >= 0.8


class MissionAnalyst(BaseAgent):
    name = "MissionAnalyst"
    slug = "mission_analyst"
    tool_names = ("pdf_reader", "state_store")

    def run(self, payload: dict) -> StrategicGoals:
        doc = self.call_tool("pdf_reader", "read", path=payload["input_path"])
        text = doc["text"]
        truncated = len(text) > MAX_DOC_CHARS
        user = (
            f"Стратегический документ ({doc['pages']} стр.)"
            + (" — показано начало, документ обрезан" if truncated else "")
            + f":\n<<<\n{text[:MAX_DOC_CHARS]}\n>>>\n\n"
            "Извлеки миссию, видение и стратегические цели. Верни JSON по формату из инструкции."
        )

        def check(goals: StrategicGoals) -> list[str]:
            # Критерий завершения: ≥3 целей, у каждой есть основание из текста
            problems = []
            if len(goals.goals) < MIN_GOALS:
                problems.append(f"извлечено {len(goals.goals)} целей, нужно не менее {MIN_GOALS}")
            ids = [g.id for g in goals.goals]
            if len(set(ids)) != len(ids):
                problems.append("id целей повторяются")
            for g in goals.goals:
                if not evidence_found(g.evidence, text):
                    problems.append(f"цитата-основание цели {g.id} не найдена в тексте документа")
            return problems

        goals = self.ask_llm(user, StrategicGoals, check)
        self.save("strategic_goals", goals)
        return goals
