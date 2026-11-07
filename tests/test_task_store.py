from pathlib import Path
import sqlite3

from video_orchestrator.runtime.task_store import TaskStore


def test_task_store_creates_and_updates_task(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    task_id = store.create_task(url="https://example.com/video", backend="yt-dlp")

    store.mark_finished(task_id, returncode=0)
    task = store.get_task(task_id)

    assert task["url"] == "https://example.com/video"
    assert task["backend"] == "yt-dlp"
    assert task["status"] == "finished"
    assert task["returncode"] == 0


def test_task_store_records_artifacts_and_acceptance(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    task_id = store.create_task(url="https://example.com/video", backend="yt-dlp")

    store.add_artifact(task_id, "report", tmp_path / ".vdo" / "reports" / str(task_id) / "report.md")
    store.mark_accepted(task_id)

    task = store.get_task(task_id)
    artifacts = store.list_artifacts(task_id)

    assert task["status"] == "accepted"
    assert artifacts[0]["kind"] == "report"
    assert artifacts[0]["path"].endswith("report.md")


def test_task_store_lists_problem_tasks(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    failed_id = store.create_task(url="https://example.com/a", backend="yt-dlp")
    finished_id = store.create_task(url="https://example.com/b", backend="yt-dlp")
    store.mark_failed(failed_id, returncode=1)
    store.mark_finished(finished_id, returncode=0)

    problem_tasks = store.list_tasks(problem_only=True)

    assert [task["id"] for task in problem_tasks] == [failed_id]


def test_task_store_marks_retryable(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    task_id = store.create_task(url="https://example.com/watch", backend="browser-sniffer")

    store.mark_retryable(task_id)
    task = store.get_task(task_id)

    assert task["status"] == "retryable"
    assert task["returncode"] is None
    assert store.list_tasks(problem_only=True)[0]["id"] == task_id


def test_task_store_lists_retryable_tasks(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    retryable_id = store.create_task(url="https://example.com/watch", backend="browser-sniffer")
    finished_id = store.create_task(url="https://example.com/video", backend="yt-dlp")
    store.mark_retryable(retryable_id)
    store.mark_finished(finished_id, returncode=0)

    retryable = store.list_retryable_tasks()

    assert [task["id"] for task in retryable] == [retryable_id]


def test_task_store_get_or_create_deduplicates_stable_key(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.sqlite")

    first_id, first_created = store.get_or_create_task(
        "https://example.com/video",
        "fixture",
        "fixture:content:quality",
    )
    second_id, second_created = store.get_or_create_task(
        "https://example.com/video?rotated=signature",
        "fixture",
        "fixture:content:quality",
    )

    assert first_created is True
    assert second_created is False
    assert second_id == first_id
    assert store.get_task(first_id)["dedupe_key"] == "fixture:content:quality"


def test_task_store_migrates_existing_database_without_data_loss(tmp_path: Path):
    db_path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, backend TEXT NOT NULL, "
            "status TEXT NOT NULL, returncode INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE task_artifacts (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, "
            "kind TEXT NOT NULL, path TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO tasks (url, backend, status, returncode, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("https://example.com/legacy", "fixture", "finished", 0, "before", "before"),
        )

    store = TaskStore(db_path)

    assert store.get_task(1)["url"] == "https://example.com/legacy"
    assert store.get_task(1)["dedupe_key"] is None
    new_id, created = store.get_or_create_task("https://example.com/new", "fixture", "fixture:new")
    assert created is True
    assert new_id == 2
