"""Shared task-state rules used by services, coordinators and projections."""

from src.schemas.task import M3U8Task, Task, TaskStatus


_RENAME_BLOCKED_STATUSES = {
    TaskStatus.QUEUED,
    TaskStatus.DOWNLOADING,
    TaskStatus.PAUSING,
    TaskStatus.MERGING,
    TaskStatus.RECORDING,
    TaskStatus.STOPPING,
}


def can_rename(task: Task) -> bool:
    """Rename only when persisted task state says no work is active."""
    return (task.status not in _RENAME_BLOCKED_STATUSES
            and not (isinstance(task, M3U8Task) and task.details.detecting))
