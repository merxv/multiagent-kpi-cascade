"""Логирование всех вызовов агентов и инструментов в формате JSON Lines.

Файл: logs/<task_id>.jsonl, одна строка — одно событие.
Типы событий: agent_start, agent_end, llm_call, tool_call, error, retry, message, info.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

EVENT_TYPES = {"agent_start", "agent_end", "llm_call", "tool_call", "error", "retry", "message", "info"}


def _default(o):
    """Сериализация «нестандартных» объектов (Pydantic-моделей и т.п.) для json.dumps."""
    return o.model_dump(mode="json") if hasattr(o, "model_dump") else str(o)


def short(obj, limit: int = 300) -> str:
    """Краткое текстовое представление входа/выхода для лога."""
    if obj is None:
        return ""
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(mode="json")
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=_default)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


class RunLogger:
    def __init__(self, task_id: str, log_dir: Path, echo=None):
        self.task_id = task_id
        self.path = Path(log_dir) / f"{task_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.echo = echo  # необязательная функция для вывода событий в консоль

    def log(self, event: str, agent: str, name: str | None = None, *,
            duration_ms: float | None = None, tokens_in: int | None = None,
            tokens_out: int | None = None, input=None, output=None,
            status: str = "ok", message: str | None = None) -> None:
        assert event in EVENT_TYPES, f"неизвестный тип события: {event}"
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "task_id": self.task_id,
            "agent": agent,
            "event": event,
            "name": name,  # имя инструмента или модели
            "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "input": short(input),
            "output": short(output),
            "status": status,
            "message": message,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if self.echo:
            self.echo(record)
