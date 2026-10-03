"""state_store — инструмент агентов для чтения/записи состояния задачи в SQLite.

Это тонкая обёртка над core.state.StateStore, привязанная к одной задаче (task_id).
"""
from core.state import StateStore


class StateStoreTool:
    name = "state_store"

    def __init__(self, store: StateStore, task_id: str):
        self.store = store
        self.task_id = task_id

    def create_task(self, params: dict) -> None:
        self.store.create_task(self.task_id, params)

    def update_task(self, status: str | None = None, current_step: str | None = None,
                    message: str | None = None) -> None:
        self.store.update_task(self.task_id, status=status, current_step=current_step, message=message)

    def save_artifact(self, agent: str, kind: str, payload: dict) -> int:
        return self.store.save_artifact(self.task_id, agent, kind, payload)

    def load_artifact(self, kind: str, version: int | None = None) -> dict | None:
        return self.store.get_artifact(self.task_id, kind, version)
