from __future__ import annotations

import subprocess
import shutil
import queue
import threading
import time
import os
import sys
import sysconfig
from pathlib import Path

from pydantic import BaseModel


class CommandResult(BaseModel):
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    timeout_kind: str | None = None
    duration_seconds: float | None = None


def command_exists(command: str) -> bool:
    return resolve_command(command) is not None


def resolve_command(command: str) -> str | None:
    """Use PATH first, then bounded Windows installation locations; never execute a probe."""
    if command in {"python", "python.exe"}:
        return sys.executable
    found = shutil.which(command)
    if found:
        return found
    if os.name != "nt" or Path(command).name != command:
        return None
    filename = command if command.lower().endswith(".exe") else command + ".exe"
    roots = [Path(sysconfig.get_path("scripts"))]
    local = os.environ.get("LOCALAPPDATA")
    appdata = os.environ.get("APPDATA")
    if local:
        roots.append(Path(local) / "Microsoft" / "WinGet" / "Links")
    if appdata:
        roots.append(Path(appdata) / "Python" / f"Python{sys.version_info.major}{sys.version_info.minor}" / "Scripts")
    for root in roots:
        candidate = root / filename
        if candidate.is_file():
            return str(candidate)
    return None


def run_command(
    args: list[str],
    *,
    hard_timeout_seconds: int | None = None,
    idle_timeout_seconds: int | None = None,
) -> CommandResult:
    started = time.monotonic()
    try:
        resolved_args = [resolve_command(args[0]) or args[0], *args[1:]]
        process = subprocess.Popen(
            resolved_args,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        return CommandResult(
            args=list(args),
            returncode=127,
            stdout="",
            stderr=str(exc),
            duration_seconds=round(time.monotonic() - started, 3),
        )

    stdout_queue: queue.Queue[str | None] = queue.Queue()
    stderr_queue: queue.Queue[str | None] = queue.Queue()
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    stdout_thread = _start_reader_thread(process.stdout, stdout_queue)
    stderr_thread = _start_reader_thread(process.stderr, stderr_queue)
    last_output_at = started
    timeout_kind: str | None = None

    while process.poll() is None:
        if _drain_queue(stdout_queue, stdout_chunks) or _drain_queue(stderr_queue, stderr_chunks):
            last_output_at = time.monotonic()

        now = time.monotonic()
        if hard_timeout_seconds is not None and now - started >= hard_timeout_seconds:
            timeout_kind = "hard"
            _kill_process(process)
            break
        if idle_timeout_seconds is not None and now - last_output_at >= idle_timeout_seconds:
            timeout_kind = "idle"
            _kill_process(process)
            break
        time.sleep(0.05)

    try:
        stdout_tail, stderr_tail = process.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        _kill_process(process)
        stdout_tail, stderr_tail = process.communicate()
    if stdout_tail:
        stdout_chunks.append(stdout_tail)
    if stderr_tail:
        stderr_chunks.append(stderr_tail)
    _drain_queue(stdout_queue, stdout_chunks)
    _drain_queue(stderr_queue, stderr_chunks)
    stdout_thread.join(timeout=0.2)
    stderr_thread.join(timeout=0.2)

    return CommandResult(
        args=list(args),
        returncode=process.returncode if process.returncode is not None else -9,
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
        timed_out=timeout_kind is not None,
        timeout_kind=timeout_kind,
        duration_seconds=round(time.monotonic() - started, 3),
    )


def _start_reader_thread(stream, output_queue: queue.Queue[str | None]) -> threading.Thread:
    def reader() -> None:
        if stream is None:
            output_queue.put(None)
            return
        try:
            for chunk in iter(stream.readline, ""):
                if chunk:
                    output_queue.put(chunk)
        finally:
            output_queue.put(None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    return thread


def _drain_queue(output_queue: queue.Queue[str | None], chunks: list[str]) -> bool:
    saw_output = False
    while True:
        try:
            item = output_queue.get_nowait()
        except queue.Empty:
            return saw_output
        if item is not None:
            chunks.append(item)
            saw_output = True


def _kill_process(process: subprocess.Popen[str]) -> None:
    try:
        process.kill()
    except OSError:
        pass
