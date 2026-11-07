from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class TaskStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_task(self, url: str, backend: str, dedupe_key: str | None = None) -> int:
        now = _utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tasks (url, backend, status, returncode, dedupe_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (url, backend, "pending", None, dedupe_key, now, now),
            )
            return int(cursor.lastrowid)

    def get_or_create_task(self, url: str, backend: str, dedupe_key: str) -> tuple[int, bool]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM tasks WHERE dedupe_key = ? ORDER BY id LIMIT 1",
                (dedupe_key,),
            ).fetchone()
            if row is not None:
                return int(row["id"]), False
        try:
            return self.create_task(url, backend, dedupe_key=dedupe_key), True
        except sqlite3.IntegrityError:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT id FROM tasks WHERE dedupe_key = ? ORDER BY id LIMIT 1",
                    (dedupe_key,),
                ).fetchone()
            if row is None:
                raise
            return int(row["id"]), False

    def mark_finished(self, task_id: int, returncode: int) -> None:
        self._mark_done(task_id, "finished", returncode)

    def mark_failed(self, task_id: int, returncode: int) -> None:
        self._mark_done(task_id, "failed", returncode)

    def mark_retryable(self, task_id: int) -> None:
        self._mark_done(task_id, "retryable", None)

    def mark_accepted(self, task_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                ("accepted", _utc_now(), task_id),
            )

    def add_artifact(self, task_id: int, kind: str, path: Path | str) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_artifacts (task_id, kind, path, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (task_id, kind, str(path), now),
            )

    def list_artifacts(self, task_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT task_id, kind, path, created_at
                FROM task_artifacts
                WHERE task_id = ?
                ORDER BY id
                """,
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_task(self, task_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, url, backend, status, returncode, dedupe_key, created_at, updated_at
                FROM tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()

        if row is None:
            raise KeyError(f"task not found: {task_id}")

        return dict(row)

    def list_tasks(self, problem_only: bool = False) -> list[dict[str, Any]]:
        query = """
            SELECT id, url, backend, status, returncode, dedupe_key, created_at, updated_at
            FROM tasks
        """
        params: tuple[Any, ...] = ()
        if problem_only:
            query += " WHERE status NOT IN (?, ?)"
            params = ("finished", "accepted")
        query += " ORDER BY id DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_retryable_tasks(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, url, backend, status, returncode, dedupe_key, created_at, updated_at
                FROM tasks
                WHERE status = ?
                ORDER BY id DESC
                """,
                ("retryable",),
            ).fetchall()
        return [dict(row) for row in rows]

    def _mark_done(self, task_id: int, status: str, returncode: int | None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = ?, returncode = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, returncode, _utc_now(), task_id),
            )

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  url TEXT NOT NULL,
                  backend TEXT NOT NULL,
                  status TEXT NOT NULL,
                  returncode INTEGER,
                  dedupe_key TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_artifacts (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  task_id INTEGER NOT NULL,
                  kind TEXT NOT NULL,
                  path TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(task_id) REFERENCES tasks(id)
                )
                """
            )
            columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "dedupe_key" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN dedupe_key TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_dedupe_key ON tasks(dedupe_key) WHERE dedupe_key IS NOT NULL"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
