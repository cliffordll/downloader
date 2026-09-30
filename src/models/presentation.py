"""任务展示数据：进度及状态文案，不创建窗口或执行下载。"""
from typing import TypedDict
from src.schemas.task import TaskStatus, TaskType


class TaskSummary(TypedDict):
    """任务状态的字段类型，避免混合字典让 status 被推断成多种类型。"""
    total: int
    done: int
    percent: int | None
    status: str
    failed: set[str]
    pending: set[str]
    merging: bool
    paused: bool
    progress: str


def single_file_info(task) -> TaskSummary:
    """MP4 按字节计进度；直播只展示时长和体积，不伪造完成百分比。"""
    progress = task.progress
    def size(value):
        amount = float(value)
        for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
            if amount < 1024 or unit == 'TB':
                return f'{amount:.1f} {unit}'
            amount /= 1024
    live = task.task_type == TaskType.RTMP
    total, done = progress.total_bytes or 0, progress.downloaded_bytes
    percent = int(done * 100 / total) if total and not live else None
    if live:
        seconds = int(progress.recorded_seconds)
        label = f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d} · {size(done)}'
    else:
        label = f'{size(done)} / {size(total) if total else "未知"}'
        if percent is not None:
            label = f'{percent}% · {label}'
    status = {
        TaskStatus.NEW: '未开始', TaskStatus.QUEUED: '等待下载',
        TaskStatus.DOWNLOADING: '下载中', TaskStatus.PAUSING: '暂停中',
        TaskStatus.PAUSED: '已暂停', TaskStatus.INTERRUPTED: '已中断',
        TaskStatus.RECORDING: '录制中', TaskStatus.STOPPING: '停止中',
        TaskStatus.COMPLETED: '已完成',
        TaskStatus.FAILED: '录制失败' if live else '下载失败',
    }.get(task.task_status, '未开始')
    # 显式构造 TypedDict，让类型检查器逐字段检查，避免推断成普通混合类型 dict。
    return TaskSummary(total=total, done=done, percent=percent, status=status, progress=label,
                       failed=set(), pending=set(), merging=False,
                       paused=task.task_status == TaskStatus.PAUSED)
