import errno
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from src.core.task_service import TaskService, _move_group_artifact
from src.core.task_files import work_dir
from src.storage.task_repository import TaskRepository


def simulate_cross_volume(monkeypatch, *, windows=False):
    rename = Path.rename

    def cross_volume(source, destination):
        # 临时副本发布位于目标盘内，只有原文件搬运触发跨盘错误。
        if not source.parent.name.startswith('.task-move-'):
            error = OSError(errno.EINVAL if windows else errno.EXDEV, 'cross device')
            if windows:
                error.winerror = 17
            raise error
        return rename(source, destination)

    monkeypatch.setattr(Path, 'rename', cross_volume)


@pytest.mark.parametrize('directory', [False, True])
@pytest.mark.parametrize('windows', [False, True])
def test_cross_volume_move_preserves_contents(tmp_path, monkeypatch, directory, windows):
    source, destination = tmp_path / 'source', tmp_path / 'destination'
    if directory:
        source.mkdir()
        (source / 'segment.ts').write_bytes(b'video')
    else:
        source.write_bytes(b'video')
    simulate_cross_volume(monkeypatch, windows=windows)
    _move_group_artifact(source, destination)
    assert not source.exists()
    assert (destination / 'segment.ts' if directory else destination).read_bytes() == b'video'
    assert not list(tmp_path.glob('.task-move-*'))


def test_permission_error_does_not_trigger_copy(tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'video')
    with patch.object(Path, 'rename', side_effect=PermissionError(errno.EACCES, 'denied')), patch(
            'src.core.task_service.shutil.copy2') as copy:
        with pytest.raises(PermissionError):
            _move_group_artifact(source, tmp_path / 'destination')
    copy.assert_not_called()
    assert source.read_bytes() == b'video'


def test_incomplete_copy_preserves_source_and_cleans_staging(tmp_path, monkeypatch):
    source, destination = tmp_path / 'source', tmp_path / 'destination'
    source.write_bytes(b'video')
    simulate_cross_volume(monkeypatch)

    def incomplete_copy(_source, staged):
        staged.write_bytes(b'partial')
        raise OSError(errno.ENOSPC, 'disk full')

    with patch('src.core.task_service.shutil.copy2', side_effect=incomplete_copy):
        with pytest.raises(OSError, match='disk full'):
            _move_group_artifact(source, destination)
    assert source.read_bytes() == b'video'
    assert not destination.exists()
    assert not list(tmp_path.glob('.task-move-*'))


@pytest.mark.parametrize('fail_database', [False, True])
def test_group_move_and_database_rollback(tmp_path, monkeypatch, fail_database):
    repository = TaskRepository(tmp_path / 'tasks.db')
    service = TaskService(repository)
    task = service.create_m3u8(tmp_path / 'source', 'https://example.com/index.m3u8',
                              '#EXTM3U\n#EXTINF:4,\na.ts\n', filename='video')
    source = work_dir(task)
    source.mkdir(parents=True, exist_ok=True)
    (source / 'a.ts').write_bytes(b'video')
    group = repository.create_group('target', tmp_path / 'target')
    simulate_cross_volume(monkeypatch)
    if fail_database:
        with patch.object(repository, 'update', side_effect=RuntimeError('database failed')):
            with pytest.raises(RuntimeError, match='database failed'):
                service.move_task_to_group(task.id, group.id)
        assert (source / 'a.ts').read_bytes() == b'video'
        assert not (group.folder_path / task.name).exists()
        assert repository.get(task.id).group_id is None
    else:
        moved = service.move_task_to_group(task.id, group.id)
        assert not source.exists()
        assert (work_dir(moved) / 'a.ts').read_bytes() == b'video'
        assert repository.get(task.id).group_id == group.id


def test_same_volume_uses_rename_without_copy(tmp_path):
    source, destination = tmp_path / 'source', tmp_path / 'destination'
    source.write_bytes(b'video')
    with patch('src.core.task_service.shutil.copy2') as copy:
        _move_group_artifact(source, destination)
    copy.assert_not_called()
    assert not source.exists()
    assert destination.read_bytes() == b'video'


@pytest.mark.parametrize('rollback_error', [PermissionError('rollback denied'),
                                           RuntimeError('rollback copy incomplete')])
def test_database_and_rollback_failures_report_both_paths(tmp_path, monkeypatch, rollback_error):
    repository = TaskRepository(tmp_path / 'tasks.db')
    service = TaskService(repository)
    task = service.create_m3u8(tmp_path / 'source', 'https://example.com/index.m3u8',
                              '#EXTM3U\n#EXTINF:4,\na.ts\n', filename='video')
    source = work_dir(task)
    source.mkdir(parents=True, exist_ok=True)
    (source / 'a.ts').write_bytes(b'video')
    group = repository.create_group('target', tmp_path / 'target')
    destination = group.folder_path / task.name
    database_error = RuntimeError('database failed')
    simulate_cross_volume(monkeypatch)

    def move_or_fail_rollback(old, new):
        if old == destination:
            raise rollback_error
        _move_group_artifact(old, new)

    with patch('src.core.task_service._move_group_artifact', side_effect=move_or_fail_rollback), patch.object(
            repository, 'update', side_effect=database_error):
        with pytest.raises(RuntimeError, match='部分文件未能回滚') as result:
            service.move_task_to_group(task.id, group.id)
    message = str(result.value)
    assert str(source) in message
    assert str(destination) in message
    assert str(database_error) in message
    assert str(rollback_error) in message
    assert result.value.__cause__ is database_error
    assert not source.exists()
    assert (destination / 'a.ts').read_bytes() == b'video'
    assert repository.get(task.id).save_dir == task.save_dir


def test_source_delete_failure_keeps_complete_destination(tmp_path, monkeypatch):
    source, destination = tmp_path / 'source', tmp_path / 'destination'
    source.write_bytes(b'video')
    simulate_cross_volume(monkeypatch)
    unlink = Path.unlink

    def deny_source_delete(path, *args, **kwargs):
        if path == source:
            raise PermissionError('source locked')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', deny_source_delete)
    with pytest.raises(RuntimeError, match='任务记录未更新'):
        _move_group_artifact(source, destination)
    assert source.read_bytes() == b'video'
    assert destination.read_bytes() == b'video'


@pytest.mark.parametrize('directory', [False, True])
def test_real_cross_volume_when_available(tmp_path, directory):
    # Windows 测试临时目录与代码仓库在不同盘时，直接验证实际跨盘复制。
    repo_root = Path(__file__).resolve().parents[1]
    if repo_root.drive.casefold() == tmp_path.drive.casefold():
        pytest.skip('No second volume available')
    with TemporaryDirectory(prefix='.cross-volume-test-', dir=repo_root) as target_root:
        source = tmp_path / 'source'
        destination = Path(target_root) / 'destination'
        if directory:
            source.mkdir()
            (source / 'a.ts').write_bytes(b'video')
        else:
            source.write_bytes(b'video')
        _move_group_artifact(source, destination)
        assert not source.exists()
        assert (destination / 'a.ts' if directory else destination).read_bytes() == b'video'
