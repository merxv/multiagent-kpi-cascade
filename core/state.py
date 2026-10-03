"""Явное хранилище состояния задачи в SQLite.

Таблицы:
  tasks     — задачи: статус, входные параметры, текущий шаг, время;
  messages  — все AgentMessage, которыми обменялись агенты;
  artifacts — промежуточные результаты агентов (по task_id, виду и версии).
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from core.schemas import AgentMessage

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    params       TEXT NOT NULL,
    current_step TEXT,
    message      TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id        TEXT PRIMARY KEY,
    task_id   TEXT NOT NULL,
    sender    TEXT NOT NULL,
    receiver  TEXT NOT NULL,
    type      TEXT NOT NULL,
    payload   TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    agent      TEXT NOT NULL,
    kind       TEXT NOT NULL,
    version    INTEGER NOT NULL,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (task_id, kind, version)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        """Открывает соединение, коммитит и закрывает его после блока with."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- задачи -------------------------------------------------------------
    def create_task(self, task_id: str, params: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tasks (id, status, params, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, "created", json.dumps(params, ensure_ascii=False), _now(), _now()),
            )

    def update_task(self, task_id: str, status: str | None = None,
                    current_step: str | None = None, message: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET status = COALESCE(?, status), current_step = COALESCE(?, current_step), "
                "message = COALESCE(?, message), updated_at = ? WHERE id = ?",
                (status, current_step, message, _now(), task_id),
            )

    def get_task(self, task_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        task = dict(row)
        task["params"] = json.loads(task["params"])
        return task

    # --- сообщения ----------------------------------------------------------
    def save_message(self, msg: AgentMessage) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?)",
                (msg.message_id, msg.task_id, msg.sender, msg.receiver, msg.type,
                 json.dumps(msg.payload, ensure_ascii=False), msg.timestamp.isoformat()),
            )

    def get_messages(self, task_id: str) -> list[AgentMessage]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE task_id = ? ORDER BY timestamp", (task_id,)
            ).fetchall()
        return [
            AgentMessage(message_id=r["id"], task_id=r["task_id"], sender=r["sender"],
                         receiver=r["receiver"], type=r["type"],
                         payload=json.loads(r["payload"]), timestamp=r["timestamp"])
            for r in rows
        ]

    # --- артефакты ----------------------------------------------------------
    def save_artifact(self, task_id: str, agent: str, kind: str, payload: dict) -> int:
        """Сохраняет новую версию артефакта и возвращает её номер (1, 2, ...)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM artifacts WHERE task_id = ? AND kind = ?",
                (task_id, kind),
            ).fetchone()
            version = row[0] + 1
            conn.execute(
                "INSERT INTO artifacts (task_id, agent, kind, version, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, agent, kind, version, json.dumps(payload, ensure_ascii=False), _now()),
            )
        return version

    def get_artifact(self, task_id: str, kind: str, version: int | None = None) -> dict | None:
        """Возвращает артефакт указанной версии (по умолчанию — последней)."""
        sql = "SELECT payload FROM artifacts WHERE task_id = ? AND kind = ?"
        args: tuple = (task_id, kind)
        if version is not None:
            sql += " AND version = ?"
            args += (version,)
        sql += " ORDER BY version DESC LIMIT 1"
        with self._conn() as conn:
            row = conn.execute(sql, args).fetchone()
        return json.loads(row[0]) if row else None
