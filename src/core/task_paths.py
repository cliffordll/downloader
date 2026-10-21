"""Resolve task-owned files from their persisted relative paths."""

from pathlib import Path

from src.schemas.task import M3U8Task, Task


def work_dir(task: M3U8Task) -> Path:
    """The M3U8 working directory is named after the extensionless task name."""
    return task.save_dir / task.name


def segment_path(task: M3U8Task, segment) -> Path:
    return work_dir(task) / segment.segment_path


def playlist_path(task: M3U8Task) -> Path:
    return work_dir(task) / (task.details.playlist_path or 'download.m3u8')


def output_path(task: Task, output) -> Path:
    return task.save_dir / output.output_path


def output_for(task: Task, kind: str):
    return next((output for output in task.outputs if output.kind == kind), None)
