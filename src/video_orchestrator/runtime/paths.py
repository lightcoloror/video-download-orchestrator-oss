from __future__ import annotations

from pathlib import Path


def vdo_dir(output_dir: Path) -> Path:
    return output_dir / ".vdo"


def log_path(output_dir: Path, task_id: int | str) -> Path:
    return vdo_dir(output_dir) / "logs" / f"{task_id}.log"


def report_dir(output_dir: Path, task_id: int | str) -> Path:
    return vdo_dir(output_dir) / "reports" / str(task_id)
