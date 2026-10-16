"""按任务记录回收本地文件，不递归删除保存目录或未知文件。"""
import errno

from send2trash import send2trash

from src.core.path_manager import PathManager
from src.schemas.task import M3U8Task, MP4Task


def _files(task):
    paths = {task.save_dir / output.relative_path for output in task.outputs}
    for output in task.outputs:
        if output.kind == 'merged':
            paths.add(task.save_dir / (output.relative_path + '.part.mp4'))
    if isinstance(task, M3U8Task):
        for segment in task.details.segments:
            paths.add(task.save_dir / segment.relative_path)
            paths.add(task.save_dir / (segment.relative_path + '.part'))
        if task.details.playlist_path:
            playlist = task.save_dir / task.details.playlist_path
            paths.update((playlist, playlist.parent / 'playlist.txt'))
    elif isinstance(task, MP4Task):
        paths.update(task.save_dir / path for path in
                     (task.details.target_path, task.details.temporary_path))
    return paths


def trash_task_files(task, other_tasks):
    """先校验全部路径再移入回收站；失败时由调用方保留数据库记录。"""
    root = task.save_dir.resolve()
    paths = _files(task)
    protected = {PathManager.GetPathKey(path) for other in other_tasks for path in _files(other)}
    existing, directories = [], set()
    for path in sorted(paths):
        # 不跟随符号链接/目录联接，也不将被替换成目录的预期文件递归回收。
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or resolved == root:
            raise ValueError(f'文件路径超出任务保存目录，未删除：{path}')
        if PathManager.GetPathKey(path) in protected:
            raise ValueError(f'文件也被其他任务使用，未删除：{path}')
        current = path
        while current != task.save_dir:
            if current.is_symlink() or current.resolve() != current.absolute():
                raise ValueError(f'文件路径包含链接，未删除：{path}')
            current = current.parent
        if path.is_dir():
            raise ValueError(f'预期文件已变成目录，未删除：{path}')
        if path.exists():
            existing.append(str(path))
        directories.update(parent for parent in path.parents if parent != root and parent.is_relative_to(root))
    if existing:
        try:
            send2trash(existing)
        except OSError as error:
            raise OSError(f'本地文件未全部移入回收站，任务记录已保留，可重试：{error}') from error
    # 只移除空目录，未知文件和另存为副本会令目录保留。
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        try:
            directory.rmdir()
        except FileNotFoundError:
            pass
        except OSError as error:
            if error.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                raise
