"""Resolve task-owned paths and manage task files without touching shared folders."""
import errno
import os
from pathlib import Path

from send2trash import send2trash

from src.core.path_manager import PathManager
from src.schemas.task import M3U8Task, MP4Task, Task


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


def _files(task, scope='all'):
    if scope not in ('all', 'work', 'output'):
        raise ValueError('无效的文件删除范围。')
    if scope == 'work' and not isinstance(task, M3U8Task):
        raise ValueError('此任务没有分片工作目录。')
    paths = {output_path(task, output) for output in task.outputs}
    for output in task.outputs:
        if output.kind == 'merged':
            paths.add(task.save_dir / (output.output_path + '.part.mp4'))
    if isinstance(task, MP4Task):
        paths.add(task.save_dir / task.details.temporary_path)
    if scope == 'output':
        return paths
    if scope == 'work':
        paths.clear()
    if isinstance(task, M3U8Task) and not (
            scope == 'all' and task.details.playlist_path):
        # Group/task deletion trashes a managed task's dedicated work directory
        # as one item; enumerating each recorded segment is redundant.
        for segment in task.details.segments:
            path = segment_path(task, segment)
            paths.add(path)
            paths.add(path.with_name(path.name + '.part'))
        if task.details.playlist_path:
            playlist = playlist_path(task)
            paths.update((playlist, playlist.parent / 'playlist.txt'))
    return paths


def collect_task_trash_paths(task, other_tasks, *, scope='all'):
    """Validate task-owned paths and return existing files plus empty dirs to clean."""
    root = task.save_dir.resolve()
    paths = _files(task, scope)
    other_tasks = list(other_tasks)
    protected = set()
    protected_directories = set()
    for other in other_tasks:
        other_paths = _files(other, 'output')
        if isinstance(other, M3U8Task) and other.details.playlist_path:
            # A managed M3U8 work directory owns its segment and playlist paths.
            protected_directories.add(PathManager.GetPathKey(work_dir(other)))
        elif isinstance(other, M3U8Task):
            other_paths.update(_files(other, 'work'))
        protected.update(PathManager.GetPathKey(path) for path in other_paths)
    work = None
    if isinstance(task, M3U8Task) and task.details.playlist_path and scope != 'output':
        candidate = work_dir(task)
        # 兼容旧记录将清单直接放在公共保存目录的情况，绝不能回收公共目录。
        if candidate.resolve() != root:
            _check_path(candidate, root, task.save_dir)
            work_key = PathManager.GetPathKey(candidate)
            def inside(path):
                key = PathManager.GetPathKey(path)
                return key == work_key or key.startswith(work_key + os.sep)
            if (any(key == work_key or key.startswith(work_key + os.sep)
                    for key in protected | protected_directories)
                    or any(inside(other.save_dir) for other in other_tasks)):
                raise ValueError(f'工作文件夹也被其他任务使用，未删除：{candidate}')
            for other in other_tasks:
                if isinstance(other, M3U8Task) and other.details.playlist_path:
                    other_work = work_dir(other)
                    other_key = PathManager.GetPathKey(other_work)
                    if other_work.resolve() != other.save_dir.resolve() and work_key.startswith(other_key + os.sep):
                        raise ValueError(f'工作文件夹位于其他任务目录内，未删除：{candidate}')
            # 旧记录的 MP4 可能在工作目录内，单删分片时仍需保留它。
            if scope != 'work' or not any(inside(output_path(task, o)) for o in task.outputs):
                if candidate.exists() and not candidate.is_dir():
                    raise ValueError(f'工作文件夹路径已变成文件，未删除：{candidate}')
                work = candidate
    existing, directories = [], set()
    for path in sorted(paths):
        # The validated, task-owned work directory is moved as one item. Walking
        # every recorded segment beneath it adds thousands of redundant stat/resolve calls.
        if work is not None and path.is_relative_to(work):
            continue
        # 不跟随符号链接/目录联接，也不将被替换成目录的预期文件递归回收。
        _check_path(path, root, task.save_dir)
        path_key = PathManager.GetPathKey(path)
        if (path_key in protected
                or any(path_key == directory or path_key.startswith(directory + os.sep)
                       for directory in protected_directories)):
            raise ValueError(f'文件也被其他任务使用，未删除：{path}')
        if path.is_dir():
            raise ValueError(f'预期文件已变成目录，未删除：{path}')
        if path.exists() and (work is None or not path.is_relative_to(work)):
            existing.append(str(path))
        directories.update(parent for parent in path.parents if parent != root and parent.is_relative_to(root))
    if work is not None and work.exists():
        existing.insert(0, str(work))
    return existing, directories


def trash_task_files(task, other_tasks, *, scope='all'):
    """先校验全部路径再移入回收站；失败时由调用方保留数据库记录。"""
    existing, directories = collect_task_trash_paths(task, other_tasks, scope=scope)
    if existing:
        try:
            send2trash(existing)
        except OSError as error:
            raise OSError(f'本地文件未全部移入回收站，任务记录已保留，可重试：{error}') from error
    # 工作目录已整体回收；其余路径只清理空目录，不影响公共目录内的其他文件。
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        try:
            directory.rmdir()
        except FileNotFoundError:
            pass
        except OSError as error:
            if error.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                raise


def _check_path(path, root, save_dir):
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError(f'文件路径超出任务保存目录，未删除：{path}')
    current = path
    while current != save_dir:
        if current.is_symlink() or current.resolve() != current.absolute():
            raise ValueError(f'文件路径包含链接，未删除：{path}')
        current = current.parent
