"""协调对象使用真实临时任务库；不创建窗口或运行 wx 事件循环。"""
from pathlib import Path
from queue import Queue, SimpleQueue
import sqlite3
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.core.sys_setting import SysSetting
from src.core.task_runner import TaskRunner
from src.core.task_service import TaskService
from src.media.m3u8.ffmpeg_converter import FFmpegConverter
from src.media.m3u8.m3u8_downloader import M3U8Downloader as Engine
from src.media.downloader import Downloader
from src.schemas.task import FileStatus, TaskStatus
from src.storage.task_repository import TaskRepository


class TaskRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = TaskRepository(self.root / 'downloads.db')
        self.service = TaskService(self.repo)
        for name, value in dict(_pending=set(), _requesting=set(), _failed=set(), _errors={}, _jobs={},
                                _paused_files=set(), _shutdown=threading.Event(), _user_paused=threading.Event(),
                                _changes=SimpleQueue(), errors=SimpleQueue(), _unsaved={}, _storage_error='',
                                threadQueue=Queue()).items():
            patcher = patch.object(Engine, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        settings = patch.object(SysSetting, '_values', dict(SysSetting.Defaults(), auto_merge=False))
        settings.start()
        self.addCleanup(settings.stop)
        self.runner = TaskRunner(self.service)
        self.addCleanup(self.runner._duration_pool.shutdown, wait=True)
        self.addCleanup(self.runner.mp4.shutdown, wait_for_jobs=True)
        self.addCleanup(self.runner.shutdown)

    def m3u8(self, name='hls', count=3, detect=False):
        return self.service.create_m3u8(self.root / name, 'https://example.com/index.m3u8',
            '#EXTM3U\n' + ''.join(f'#EXTINF:4,\n{i}.ts\n' for i in range(count)), detect_duration=detect)

    def test_update_segment_info_persists_and_retry_uses_new_address(self):
        from src.models.tree_model import to_tree_item
        task = self.m3u8(count=2)
        path = task.save_dir / task.name / task.details.segments[1].segment_path
        self.service.finish_segment(task.id, 1, path, False, 'old URL failed')
        old = self.repo.get(task.id)
        previous = to_tree_item(old)
        url = 'https://cdn.example.com/fixed.ts?token=a%2Bb&part=2'
        updated = self.runner.update_segment_info(task.id, 1, ' ' + url + ' ')
        reopened = TaskRepository(self.repo.path).get(task.id)
        self.assertEqual(reopened.details.segments[1].source_url, url)
        self.assertEqual(reopened.details.segments[0], old.details.segments[0])
        self.assertEqual(reopened.details.segments[1].segment_path, old.details.segments[1].segment_path)
        self.assertEqual(reopened.details.segments[1].status, FileStatus.FAILED)
        self.assertEqual(to_tree_item(updated, previous).childs[1].absUri, url)
        with patch.object(Engine, 'DownloadTSFile', side_effect=self.enqueue) as download:
            self.assertEqual(self.runner.start(task.id, sequences=[1], retry=True), 1)
            self.assertEqual(download.call_args.args[0], url)

    def test_edit_duration_persists_rebuilds_playlist_and_validates_atomically(self):
        task = self.m3u8(count=2)
        original = self.repo.get(task.id)
        url = 'https://example.com/new.ts?token=123'
        for duration in ('bad', '0', '-1', 'nan', 'inf'):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                self.runner.update_segment_info(task.id, 0, url, duration)
            self.assertEqual(self.repo.get(task.id).details.segments, original.details.segments)
        updated = self.runner.update_segment_info(task.id, 0, url, '5.125')
        reloaded = TaskRepository(self.repo.path).get(task.id)
        self.assertEqual(reloaded.details.segments[0].source_url, url)
        self.assertEqual(reloaded.details.segments[0].duration, 5.125)
        self.assertEqual(reloaded.details.segments[0].duration_status, 'detected')
        self.assertEqual(reloaded.details.segments[1], original.details.segments[1])
        playlist = updated.save_dir / updated.name / updated.details.playlist_path
        self.assertIn('#EXTINF:5.125,', playlist.read_text(encoding='utf-8'))
        self.service.finish_segment(task.id, 0, self.write(task, 0), True)
        updated = self.runner.update_segment_info(task.id, 0, url, '6.25')
        self.assertEqual(updated.details.segments[0].status, FileStatus.COMPLETED)
        self.assertEqual(updated.details.segments[0].duration, 6.25)
        with self.assertRaises(ValueError):
            self.runner.update_segment_info(task.id, 0, 'https://example.com/other.ts', '7')
        self.assertEqual(self.repo.get(task.id).details.segments[0].duration, 6.25)
        self.runner._duration_jobs.add(task.id)
        with self.assertRaises(ValueError):
            self.runner.update_segment_info(task.id, 0, url, '7')
        self.runner._duration_jobs.clear()

    def test_update_segment_info_rejects_invalid_completed_and_queued_segments(self):
        task = self.m3u8(count=2)
        original = task.details.segments[0].source_url
        for url in ('', '/relative.ts', 'ftp://example.com/a.ts', 'https://example.com/a b.ts'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.runner.update_segment_info(task.id, 0, url)
            self.assertEqual(self.repo.get(task.id).details.segments[0].source_url, original)
        key = Engine.FileKey(task.save_dir / task.name / task.details.segments[0].segment_path)
        for state in (Engine._pending, Engine._requesting):
            state.add(key)
            with self.assertRaises(ValueError):
                self.runner.update_segment_info(task.id, 0, 'https://example.com/new.ts')
            state.clear()
        self.service.finish_segment(task.id, 0, self.write(task, 0), True)
        with self.assertRaises(ValueError):
            self.runner.update_segment_info(task.id, 0, 'https://example.com/new.ts')
        with self.assertRaises(ValueError):
            self.runner.update_segment_info(task.id, 99, 'https://example.com/new.ts')

    def mp4(self, name='direct'):
        return self.service.create_mp4(self.root / name, 'https://example.com/video.mp4')

    def test_rename_m3u8_moves_work_folder_and_preserves_relative_segment_paths(self):
        task = self.m3u8(name='rename-hls', count=2)
        old_work = task.save_dir / task.name
        old_segments = [self.write(task, segment.sequence).read_bytes() for segment in task.details.segments]
        self.complete_segments(task)
        renamed = self.runner.rename(task.id, 'renamed')
        new_work = task.save_dir / 'renamed'
        self.assertEqual(renamed.name, 'renamed')
        self.assertEqual(renamed.details.playlist_path, task.details.playlist_path)
        self.assertEqual([s.segment_path for s in renamed.details.segments],
                         [s.segment_path for s in task.details.segments])
        self.assertFalse(old_work.exists())
        self.assertTrue((new_work / task.details.playlist_path).is_file())
        for segment, old_bytes in zip(renamed.details.segments, old_segments):
            new_path = new_work / segment.segment_path
            self.assertEqual(new_path.read_bytes(), old_bytes)
        self.assertEqual((task.save_dir / 'renamed.mp4').name, 'renamed.mp4')

    def test_rename_mp4_moves_output_and_partial_and_persists_name(self):
        task = self.mp4()
        target = task.save_dir / task.outputs[0].output_path
        partial = task.save_dir / task.details.temporary_path
        target.write_bytes(b'finished')
        partial.write_bytes(b'partial')
        renamed = self.runner.rename(task.id, 'renamed')
        self.assertEqual(renamed.name, 'renamed')
        self.assertEqual(renamed.outputs[0].output_path, 'renamed.mp4')
        self.assertEqual(renamed.details.temporary_path, 'renamed.mp4.part')
        self.assertEqual((task.save_dir / 'renamed.mp4').read_bytes(), b'finished')
        self.assertEqual((task.save_dir / 'renamed.mp4.part').read_bytes(), b'partial')
        self.assertFalse(target.exists())
        self.assertFalse(partial.exists())
        self.assertEqual(self.repo.get(task.id), renamed)

    def test_encrypted_mp4_start_receives_current_encryption_context(self):
        task = self.mp4(name='encrypted-direct')
        self.repo.mutate(task.id, lambda record: setattr(record, 'encrypted', True))
        encrypted_task = self.repo.get(task.id)
        context = {'wrapping_key': b'key', 'salt': b'salt', 'iterations': 1}
        self.runner.set_encryption_context_provider(lambda: context)
        with patch.object(self.runner, 'check_files', return_value=encrypted_task), \
             patch.object(self.runner.mp4, 'start', return_value=True) as start:
            self.assertTrue(self.runner.start(task.id))
        start.assert_called_once_with(task.id, encryption_context=context)

    def test_encrypted_m3u8_start_receives_current_encryption_context(self):
        task = self.m3u8(name='encrypted-hls', count=1)
        task = self.service.set_task_encrypted(task.id, True)
        context = {'wrapping_key': b'k' * 32, 'salt': b's' * 16, 'iterations': 1000}
        self.runner.set_encryption_context_provider(lambda: context)
        with patch.object(self.runner, 'check_files', return_value=task), \
             patch.object(Engine, 'StartTask', return_value=1) as start:
            self.assertEqual(self.runner.start(task.id), 1)
        start.assert_called_once_with(self.service, task.id, [0], encryption_context=context)

    def test_encrypted_m3u8_start_requires_unlocked_context(self):
        task = self.m3u8(name='encrypted-hls', count=1)
        self.service.set_task_encrypted(task.id, True)
        with self.assertRaisesRegex(ValueError, '先解锁加密任务'):
            self.runner.start(task.id)

    def test_rename_avoids_other_task_names_and_rejects_active_records(self):
        task = self.service.create_m3u8(self.root, 'https://example.com/index.m3u8',
                                        '#EXTM3U\n#EXTINF:4,\na.ts\n', filename='first')
        self.service.create_m3u8(self.root, 'https://example.com/index.m3u8',
                                 '#EXTM3U\n#EXTINF:4,\nb.ts\n', filename='taken')
        renamed = self.runner.rename(task.id, 'taken')
        self.assertEqual(renamed.name, 'taken (1)')
        self.repo.mutate(task.id, lambda record: setattr(record, 'status', TaskStatus.DOWNLOADING))
        with self.assertRaisesRegex(ValueError, '正在下载'):
            self.runner.rename(task.id, 'blocked')
        self.assertEqual(self.repo.get(task.id).name, 'taken (1)')
        self.repo.mutate(task.id, lambda record: setattr(record, 'status', TaskStatus.PAUSED))
        self.repo.mutate(task.id, lambda record: setattr(record.details, 'detecting', True))
        with self.assertRaisesRegex(ValueError, '正在下载'):
            self.runner.rename(task.id, 'blocked')

    def write(self, task, sequence):
        path = task.save_dir / task.name / task.details.segments[sequence].segment_path
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_bytes(b'media')
        return path

    def complete_segments(self, task):
        for segment in task.details.segments:
            path = self.write(task, segment.sequence)
            self.service.finish_segment(task.id, segment.sequence, str(path), True)

    def enqueue(self, url, filename, callback, context, **kwargs):
        Engine._pending.add(Engine.FileKey(filename))
        return True

    def wait(self, condition):
        deadline = time.monotonic() + 4
        while not condition():
            if time.monotonic() > deadline:
                self.fail('后台处理超时')
            time.sleep(0.01)

    def test_engine_selection_and_retry_only_missing_failed_segments(self):
        direct, hls = self.mp4(), self.m3u8()
        self.write(hls, 0)
        self.service.finish_segment(hls.id, 1, str(hls.save_dir / hls.name / hls.details.segments[1].segment_path), False, 'HTTP 500')
        with patch.object(self.runner.mp4, 'start', return_value=True) as mp4, \
             patch.object(Engine, 'DownloadTSFile', side_effect=self.enqueue) as segment:
            self.assertTrue(self.runner.start(direct.id))
            self.assertEqual(self.runner.start(hls.id, retry=True), 1)
            mp4.assert_called_once_with(direct.id)
            self.assertEqual([call.args[3][1] for call in segment.call_args_list], [1])

    def test_single_segment_uses_stable_sequence_and_validates_before_start(self):
        task = self.m3u8()
        with patch.object(Engine, 'DownloadTSFile', side_effect=self.enqueue) as segment:
            self.runner.start(task.id, sequences=[2])
            self.assertEqual(segment.call_args.args[3], (task.id, 2))
            with self.assertRaisesRegex(ValueError, '序号无效'):
                self.runner.start(task.id, sequences=[99])
            self.assertEqual(segment.call_count, 1)

    def test_start_checks_only_selected_task_and_redownloads_missing_or_changed_segments(self):
        task, other = self.m3u8(), self.m3u8('other')
        self.complete_segments(task)
        self.complete_segments(other)
        (task.save_dir / task.name / task.details.segments[1].segment_path).unlink()
        changed = task.save_dir / task.name / task.details.segments[2].segment_path
        changed.write_bytes(b'bad')
        (other.save_dir / other.name / other.details.segments[0].segment_path).unlink()
        self.service.load_tasks()
        self.assertEqual(self.repo.get(task.id).progress.completed_segments, 3)
        with patch.object(Engine, 'DownloadTSFile', side_effect=self.enqueue) as download:
            self.assertEqual(self.runner.start(task.id), 2)
        self.assertEqual([call.args[3][1] for call in download.call_args_list], [1, 2])
        self.assertEqual(self.repo.get(task.id).progress.completed_segments, 1)
        self.assertEqual(self.repo.get(other.id).progress.completed_segments, 3)
        # 已核对为异常的现存分片也应重新下载，而不是仅因存在就当作成功。
        def consume(url, **kwargs):
            response = SimpleNamespace(headers={}, iter_content=lambda **kw: [b'media'])
            return True, kwargs['consume'](response)
        with patch.object(Engine, 'DownloadContent', side_effect=consume):
            success, _ = Engine._SaveFile('https://example.com/2.ts', str(changed))
        self.assertTrue(success)
        self.assertEqual(changed.read_bytes(), b'media')

    def test_merge_missing_file_persists_correction_before_rejecting(self):
        task = self.m3u8()
        self.complete_segments(task)
        (task.save_dir / task.name / task.details.segments[1].segment_path).unlink()
        with patch.object(FFmpegConverter, 'ConvertTSFile') as convert:
            with self.assertRaisesRegex(ValueError, '分片尚未全部下载'):
                self.runner.merge(task.id)
        convert.assert_not_called()
        saved = self.repo.get(task.id)
        self.assertEqual(saved.progress.completed_segments, 2)
        self.assertEqual(saved.details.segments[1].status, FileStatus.MISSING)
        self.assertIn(task.id, self.runner.changes())

    def test_resume_repairs_deleted_completed_segment_without_starting_unselected_files(self):
        task = self.m3u8()
        path = self.write(task, 0)
        self.service.finish_segment(task.id, 0, str(path), True)
        with patch.object(Engine, 'DownloadTSFile', side_effect=self.enqueue) as download:
            self.runner.start(task.id, sequences=[1])
            Engine.Pause()
            path.unlink()
            download.reset_mock()
            self.runner.activate(task.id)
        self.assertEqual([call.args[3][1] for call in download.call_args_list], [0])
        self.assertEqual(Engine.Snapshot()['pending'], {
            Engine.FileKey(task.save_dir / task.name / task.details.segments[index].segment_path) for index in (0, 1)})

    def test_mp4_missing_output_is_repaired_only_when_starting(self):
        task = self.mp4()
        def complete(current):
            current.status = TaskStatus.COMPLETED
            current.details.verified_bytes = 5
            current.progress.downloaded_bytes = 5
            current.outputs[0].status = FileStatus.COMPLETED
            current.outputs[0].size_bytes = 5
        self.repo.mutate(task.id, complete)
        self.service.load_tasks()
        self.assertEqual(self.repo.get(task.id).status, TaskStatus.COMPLETED)
        with patch.object(self.runner.mp4, 'start', return_value=True) as download:
            self.assertTrue(self.runner.start(task.id))
        download.assert_called_once_with(task.id)
        self.assertEqual(self.repo.get(task.id).status, TaskStatus.INTERRUPTED)
        self.assertEqual(self.repo.get(task.id).progress.downloaded_bytes, 0)

    def test_row_resume_preserves_selected_queue_and_other_tasks_pause(self):
        task, other = self.m3u8(), self.m3u8('other')
        with patch.object(Engine, 'DownloadTSFile', side_effect=self.enqueue):
            self.runner.start(task.id, sequences=[1])
            self.runner.start(other.id, sequences=[0])
        Engine.Pause()
        with patch.object(Engine, 'DownloadTSFile') as download:
            self.runner.activate(task.id)
            download.assert_not_called()
        snapshot = Engine.Snapshot()
        self.assertEqual(snapshot['paused_files'], {Engine.FileKey(other.save_dir / other.name / other.details.segments[0].segment_path)})
        self.assertEqual(len(snapshot['pending']), 2)
        self.runner.activate(task.id)
        self.assertEqual(Engine.Snapshot()['pending'], Engine.Snapshot()['paused_files'])

    def test_start_and_retry_after_global_pause_only_resume_selected_files(self):
        other = self.m3u8('other')
        with patch.object(Engine, 'DownloadTSFile', side_effect=self.enqueue):
            self.runner.start(other.id, sequences=[0])
            for retry in (False, True):
                with self.subTest(retry=retry):
                    task = self.m3u8(f'new-{retry}')
                    if retry:
                        segment = task.details.segments[1]
                        self.service.finish_segment(task.id, 1,
                            str(task.save_dir / task.name / segment.segment_path), False, 'HTTP 500')
                    Engine.Pause()
                    self.assertEqual(self.runner.activate(task.id, retry=retry), 1 if retry else 3)
                    snapshot = Engine.Snapshot()
                    self.assertFalse(snapshot['paused'])
                    selected = {Engine.FileKey(task.save_dir / task.name / s.segment_path)
                                for s in task.details.segments if not retry or s.sequence == 1}
                    self.assertTrue(selected <= snapshot['pending'])
                    self.assertFalse(selected & snapshot['paused_files'])
                    self.assertEqual(snapshot['paused_files'], snapshot['pending'] - selected)

    def test_global_resume_uses_database_and_leaves_new_failed_tasks_alone(self):
        paused, interrupted, new, failed = [self.mp4(name) for name in ('paused', 'interrupted', 'new', 'failed')]
        for task, status in ((paused, TaskStatus.PAUSED), (interrupted, TaskStatus.INTERRUPTED), (failed, TaskStatus.FAILED)):
            self.service.runtime_status(task.id, status)
        with patch.object(self.runner.mp4, 'start', return_value=True) as start:
            resumed = self.runner.resume_all()
        self.assertEqual(resumed, {paused.id, interrupted.id})
        self.assertEqual({call.args[0] for call in start.call_args_list}, resumed)
        self.assertEqual(self.repo.get(new.id).status, TaskStatus.NEW)
        self.assertEqual(self.repo.get(failed.id).status, TaskStatus.FAILED)

    def test_global_resume_includes_partial_tasks(self):
        task = self.m3u8()
        self.service.runtime_status(task.id, TaskStatus.PARTIAL)
        with patch.object(self.runner, 'start', return_value=1) as start:
            self.assertEqual(self.runner.resume_all(), {task.id})
        start.assert_called_once_with(task.id)

    def test_delete_rechecks_busy_state_and_keeps_downloaded_files(self):
        task = self.m3u8()
        path = self.write(task, 0)
        Engine._pending.add(Engine.FileKey(task.save_dir / task.name / task.details.segments[1].segment_path))
        with patch('src.core.task_files.send2trash') as trash:
            for delete_files in (False, True):
                with self.assertRaisesRegex(ValueError, '不能删除'):
                    self.runner.delete(task.id, delete_files=delete_files)
            trash.assert_not_called()
        Engine._pending.clear()
        self.runner.delete(task.id)
        self.assertIsNone(self.repo.get(task.id))
        self.assertTrue(path.is_file())

    def recycle(self, paths):
        # 测试只移动临时目录中的文件，不访问用户回收站。
        trash = self.root / 'trash'
        trash.mkdir(exist_ok=True)
        for index, name in enumerate(paths):
            Path(name).replace(trash / f'{len(list(trash.iterdir()))}-{index}')

    def test_delete_m3u8_recycles_known_files_before_removing_record(self):
        task, other = self.m3u8(), self.m3u8('other')
        segment = self.write(task, 0)
        other_file = self.write(other, 0)
        playlist = task.save_dir / task.name / task.details.playlist_path
        concat = playlist.parent / 'playlist.txt'
        partial = Path(str(segment) + '.part')
        output = task.save_dir / task.outputs[0].output_path
        merge_partial = Path(str(output) + '.part.mp4')
        exported = self.root / 'exported.mp4'
        for path in (concat, partial, output, merge_partial, exported):
            path.write_bytes(b'media')

        def recycle(paths):
            self.assertIsNotNone(self.repo.get(task.id))
            self.recycle(paths)

        with patch('src.core.task_files.send2trash', side_effect=recycle) as trash:
            self.runner.delete(task.id, delete_files=True)
        self.assertEqual(set(trash.call_args.args[0]),
                         {str(path) for path in (playlist.parent, output, merge_partial)})
        self.assertIsNone(self.repo.get(task.id))
        self.assertFalse(playlist.parent.exists())
        self.assertTrue(other_file.exists())
        self.assertTrue(exported.exists())
        self.assertTrue(task.save_dir.exists())

    def test_delete_recycles_entire_work_directory_including_hidden_files(self):
        task = self.m3u8()
        self.write(task, 0)
        directory = (task.save_dir / task.name / task.details.playlist_path).parent
        unknown = directory / 'notes.txt'
        unknown.write_text('keep', encoding='utf-8')
        hidden = directory / '.DS_Store'
        hidden.write_bytes(b'finder metadata')
        with patch('src.core.task_files.send2trash', side_effect=self.recycle):
            self.runner.delete(task.id, delete_files=True)
        self.assertFalse(directory.exists())
        self.assertEqual(sorted(path.name for path in (self.root / 'trash').iterdir()), ['0-0'])
        recycled = next((self.root / 'trash').iterdir())
        self.assertTrue((recycled / '.DS_Store').exists())
        self.assertEqual((recycled / 'notes.txt').read_text(encoding='utf-8'), 'keep')
        self.assertIsNone(self.repo.get(task.id))

    def test_delete_mp4_recycles_output_and_partial_and_allows_missing_files(self):
        for name, create_files in (('direct', True), ('missing', False)):
            task = self.mp4(name)
            target = task.save_dir / next(o.output_path for o in task.outputs if o.kind == 'download')
            partial = task.save_dir / task.details.temporary_path
            if create_files:
                target.write_bytes(b'media')
                partial.write_bytes(b'partial')
            else:
                partial.unlink()
            with patch('src.core.task_files.send2trash', side_effect=self.recycle) as trash:
                self.runner.delete(task.id, delete_files=True)
            self.assertEqual(trash.call_count, int(create_files))
            self.assertIsNone(self.repo.get(task.id))
            self.assertFalse(target.exists())
            self.assertFalse(partial.exists())

    def test_delete_partial_recycle_failure_keeps_record_and_can_retry(self):
        task = self.mp4()
        target = task.save_dir / next(o.output_path for o in task.outputs if o.kind == 'download')
        target.write_bytes(b'media')

        def fail(paths):
            self.recycle(paths[:1])
            raise PermissionError('locked file')

        with patch('src.core.task_files.send2trash', side_effect=fail):
            with self.assertRaisesRegex(OSError, '任务记录已保留'):
                self.runner.delete(task.id, delete_files=True)
        self.assertIsNotNone(self.repo.get(task.id))
        with patch('src.core.task_files.send2trash', side_effect=self.recycle):
            self.runner.delete(task.id, delete_files=True)
        self.assertIsNone(self.repo.get(task.id))

    def test_delete_rejects_directory_replacing_expected_file_before_recycling(self):
        task = self.m3u8()
        segment = task.save_dir / task.name / task.details.segments[0].segment_path
        segment.mkdir(parents=True)
        (segment / 'keep.txt').write_bytes(b'keep')
        with patch('src.core.task_files.send2trash') as trash:
            with self.assertRaisesRegex(ValueError, '变成目录'):
                self.runner.delete(task.id, delete_files=True)
        trash.assert_not_called()
        self.assertIsNotNone(self.repo.get(task.id))

    def test_delete_rejects_paths_shared_with_another_task(self):
        task, other = self.mp4(), self.mp4('other')
        def share(record):
            record.save_dir = task.save_dir
            record.outputs[0].output_path = task.outputs[0].output_path
        self.repo.mutate(other.id, share)
        with patch('src.core.task_files.send2trash') as trash:
            with self.assertRaisesRegex(ValueError, '其他任务'):
                self.runner.delete(task.id, delete_files=True)
        trash.assert_not_called()
        self.assertIsNotNone(self.repo.get(task.id))

    def test_delete_refuses_linked_segment_directory(self):
        task = self.m3u8()
        outside = self.root / 'outside'
        outside.mkdir()
        original = outside / '000000.ts'
        original.write_bytes(b'keep')
        directory = (task.save_dir / task.name / task.details.segments[0].segment_path).parent
        try:
            directory.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('当前环境不允许创建符号链接')
        with patch('src.core.task_files.send2trash') as trash:
            with self.assertRaises(ValueError):
                self.runner.delete(task.id, delete_files=True)
        trash.assert_not_called()
        self.assertEqual(original.read_bytes(), b'keep')
        self.assertIsNotNone(self.repo.get(task.id))

    def test_delete_work_files_keeps_mp4_and_task_record(self):
        task = self.m3u8(count=2)
        self.complete_segments(task)
        output = task.save_dir / task.outputs[0].output_path
        output.write_bytes(b'video')
        self.service.finish_merge(task.id, output, True)
        ((task.save_dir / task.name / task.details.playlist_path).parent / '.DS_Store').write_bytes(b'metadata')
        with patch('src.core.task_files.send2trash', side_effect=self.recycle):
            self.runner.delete(task.id, scope='work')
        saved = self.repo.get(task.id)
        self.assertEqual(saved.status, TaskStatus.COMPLETED)
        self.assertTrue(saved.details.work_dir_missing)
        self.assertTrue(TaskRepository(self.repo.path).get(task.id).details.work_dir_missing)
        self.assertEqual(saved.progress.completed_segments, 0)
        self.assertTrue(all(s.status == FileStatus.MISSING for s in saved.details.segments))
        self.assertEqual(output.read_bytes(), b'video')
        self.assertFalse((task.save_dir / task.name / task.details.playlist_path).parent.exists())

    def test_delete_work_directory_rejects_another_tasks_nested_files(self):
        task, other = self.m3u8(), self.mp4('other')
        directory = (task.save_dir / task.name / task.details.playlist_path).parent
        def relocate(record):
            record.save_dir = directory / 'nested'
        self.repo.mutate(other.id, relocate)
        with patch('src.core.task_files.send2trash') as trash:
            with self.assertRaisesRegex(ValueError, '其他任务'):
                self.runner.delete(task.id, delete_files=True)
        trash.assert_not_called()
        self.assertTrue(directory.exists())
        self.assertIsNotNone(self.repo.get(task.id))

    def test_legacy_playlist_in_save_directory_never_recycles_public_directory(self):
        task = self.m3u8(count=1)
        self.write(task, 0)
        self.repo.mutate(task.id, lambda record: setattr(record.details, 'playlist_path', 'download.m3u8'))
        self.service.write_playlist(task.id)
        unknown = task.save_dir / 'keep.txt'
        unknown.write_bytes(b'keep')
        with patch('src.core.task_files.send2trash', side_effect=self.recycle) as trash:
            self.runner.delete(task.id, delete_files=True)
        self.assertNotIn(str(task.save_dir), trash.call_args.args[0])
        self.assertEqual(unknown.read_bytes(), b'keep')
        self.assertTrue(task.save_dir.exists())

    def test_delete_mp4_keeps_segments_and_allows_merge_again(self):
        task = self.m3u8(count=2)
        self.complete_segments(task)
        output = task.save_dir / task.outputs[0].output_path
        output.write_bytes(b'video')
        self.service.finish_merge(task.id, output, True)
        with patch('src.core.task_files.send2trash', side_effect=self.recycle):
            self.runner.delete(task.id, scope='output')
        saved = self.repo.get(task.id)
        self.assertEqual(saved.status, TaskStatus.WAITING_MERGE)
        self.assertFalse(saved.details.work_dir_missing)
        self.assertEqual(saved.outputs[0].status, FileStatus.MISSING)
        self.assertEqual(saved.progress.completed_segments, 2)
        self.assertTrue(all((task.save_dir / task.name / s.segment_path).exists() for s in saved.details.segments))
        self.assertFalse(output.exists())
        with patch.object(FFmpegConverter, 'ConvertTSFile') as convert:
            self.runner.merge(task.id)
        convert.assert_called_once()

    def test_delete_direct_mp4_resets_saved_progress_for_redownload(self):
        task = self.mp4()
        output = task.save_dir / next(o.output_path for o in task.outputs if o.kind == 'download')
        output.write_bytes(b'video')
        def complete(record):
            record.status = TaskStatus.COMPLETED
            record.details.verified_bytes = 5
            record.progress.downloaded_bytes = 5
            record.outputs[0].status = FileStatus.COMPLETED
            record.outputs[0].size_bytes = 5
        self.repo.mutate(task.id, complete)
        with patch('src.core.task_files.send2trash', side_effect=self.recycle):
            self.runner.delete(task.id, scope='output')
        saved = self.repo.get(task.id)
        self.assertEqual(saved.status, TaskStatus.INTERRUPTED)
        self.assertEqual(saved.progress.downloaded_bytes, 0)
        self.assertIsNone(saved.details.verified_bytes)
        self.assertTrue(saved.details.file_missing)
        from concurrent.futures import Future
        future = Future()
        with patch.object(Downloader, 'Submit', return_value=future):
            self.assertTrue(self.runner.start(task.id))
        self.assertFalse(self.repo.get(task.id).details.file_missing)
        future.cancel()

    def test_file_delete_failure_reconciles_partial_changes_and_keeps_record(self):
        task = self.m3u8(count=1)
        self.complete_segments(task)
        output = task.save_dir / task.outputs[0].output_path
        output.write_bytes(b'video')
        self.service.finish_merge(task.id, output, True)
        def fail(paths):
            self.recycle(paths)
            raise PermissionError('trash failed')
        with patch('src.core.task_files.send2trash', side_effect=fail):
            with self.assertRaises(OSError):
                self.runner.delete(task.id, scope='output')
        self.assertEqual(self.repo.get(task.id).status, TaskStatus.WAITING_MERGE)

    def test_file_delete_rechecks_merge_and_duration_activity(self):
        task = self.m3u8()
        for scope in ('work', 'output'):
            self.runner._merging.add(task.id)
            with patch('src.core.task_files.send2trash') as trash:
                with self.assertRaises(ValueError):
                    self.runner.delete(task.id, scope=scope)
            trash.assert_not_called()
            self.runner._merging.clear()
        self.runner._duration_jobs.add(task.id)
        with self.assertRaises(ValueError):
            self.runner.delete(task.id, scope='work')
        self.runner._duration_jobs.clear()

    def test_delete_work_discards_only_paused_queue_and_does_not_redownload(self):
        task = self.m3u8(count=2)
        with patch.object(Engine, 'isStop', True), patch.object(Engine, '_master', None), \
             patch.object(Engine, '_scheduled', {}), patch.object(Engine, '_DownLoadFile') as download:
            try:
                Engine.Pause()
                Engine.StartTask(self.service, task.id, [0, 1])
                self.wait(lambda: self.repo.get(task.id).status == TaskStatus.PAUSED)
                with patch('src.core.task_files.send2trash', side_effect=self.recycle):
                    self.runner.delete(task.id, scope='work')
                Engine.Resume()
                self.wait(lambda: Engine.isStop)
                Engine._master.join(2)
                download.assert_not_called()
                self.assertFalse(Engine.Snapshot()['pending'])
                self.assertIsNotNone(self.repo.get(task.id))
                with patch.object(Engine, 'DownloadTSFile', side_effect=self.enqueue) as enqueue:
                    self.assertEqual(self.runner.start(task.id), 2)
                self.assertEqual(enqueue.call_count, 2)
            finally:
                Engine.Shutdown()
                if Engine._master:
                    Engine._master.join(3)
                scheduler = Downloader._thread
                if scheduler:
                    scheduler.join(3)
                    self.assertFalse(scheduler.is_alive())

    def test_duration_detection_notifies_without_gui_and_blocks_merge_until_done(self):
        task = self.m3u8(count=1, detect=True)
        self.complete_segments(task)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def probe(path):
            entered.set()
            release.wait(3)
            return 2.5

        with patch.object(FFmpegConverter, 'ProbeDuration', side_effect=probe) as probe_call:
            self.runner.queue_duration(task.id)
            self.assertTrue(entered.wait(2))
            self.runner.queue_duration(task.id)
            with self.assertRaisesRegex(ValueError, '下载、检测或合并'):
                self.runner.merge(task.id)
            release.set()
            self.wait(lambda: not self.runner.duration_results.empty())
        probe_call.assert_called_once()
        self.assertEqual(self.runner.duration_results.get_nowait(), (task.id, None))
        self.assertEqual(self.repo.get(task.id).details.segments[0].duration, 2.5)

    def test_manual_duration_detects_completed_segments_without_enabling_auto_detect(self):
        task = self.m3u8(count=3)
        for sequence in (0, 1):
            self.service.finish_segment(task.id, sequence, self.write(task, sequence), True)
        with patch.object(SysSetting, 'GetFFprobe', return_value='ffprobe'), \
             patch.object(FFmpegConverter, 'ProbeDuration', side_effect=[2.5, ValueError('bad media')]) as probe, \
             patch.object(self.runner, 'merge') as merge:
            self.runner.detect_durations(task.id)
            self.wait(lambda: not self.runner.duration_results.empty())
            self.assertEqual(self.runner.duration_results.get_nowait(), (task.id, None))
            self.assertEqual(probe.call_count, 2)
            merge.assert_not_called()
        record = self.repo.get(task.id)
        self.assertFalse(record.details.detect_duration)
        self.assertEqual(record.details.segments[0].duration, 2.5)
        self.assertEqual(record.details.segments[1].duration, 4)
        self.assertEqual(record.details.segments[1].duration_status, 'failed')
        self.assertEqual(record.details.segments[2].duration_status, 'pending')
        with patch.object(SysSetting, 'GetFFprobe', return_value='ffprobe'), \
             patch.object(FFmpegConverter, 'ProbeDuration', return_value=3.5) as probe:
            self.runner.detect_durations(task.id)
            self.wait(lambda: not self.runner.duration_results.empty())
            self.runner.duration_results.get_nowait()
            self.assertEqual(probe.call_count, 2)
        playlist = self.runner.write_playlist(task.id).read_text(encoding='utf-8')
        self.assertEqual(playlist.count('#EXTINF:3.5,'), 2)
        with self.assertRaises(ValueError):
            self.runner.detect_durations(self.mp4().id)
        with self.assertRaises(ValueError):
            self.runner.detect_durations(self.m3u8('empty').id)
        with patch.object(SysSetting, 'GetFFprobe', return_value=''):
            with self.assertRaises(ValueError):
                self.runner.detect_durations(task.id)

    def test_duration_save_failure_requires_refresh_before_retry(self):
        task = self.m3u8(count=1, detect=True)
        with patch.object(self.service, 'detect_durations', side_effect=sqlite3.OperationalError('disk full')) as detect:
            self.runner.queue_duration(task.id)
            self.wait(lambda: not self.runner.duration_results.empty())
            self.assertIn('disk full', self.runner.duration_results.get_nowait()[1])
            self.runner.queue_duration(task.id)
            self.assertEqual(detect.call_count, 1)
            self.runner.reset_duration_failures()
            self.runner.queue_duration(task.id)
            self.wait(lambda: detect.call_count == 2)

    def test_duration_probe_does_not_block_downloading_remaining_segments(self):
        task = self.m3u8(count=2, detect=True)
        path = self.write(task, 0)
        self.service.finish_segment(task.id, 0, str(path), True)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def probe(path):
            entered.set()
            release.wait(3)
            return 2.5

        with patch.object(FFmpegConverter, 'ProbeDuration', side_effect=probe), \
             patch.object(Engine, 'DownloadTSFile', side_effect=self.enqueue) as download:
            self.runner.queue_duration(task.id)
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.runner.start(task.id), 1)
            self.assertEqual(download.call_args.args[3], (task.id, 1))
            release.set()
            self.wait(lambda: not self.runner.duration_results.empty())

    def test_merge_success_saves_result_in_background_without_gui(self):
        task = self.m3u8(count=1)
        self.complete_segments(task)
        output = task.save_dir / task.outputs[0].output_path
        threads = []
        original = self.service.finish_merge

        def save(*args):
            threads.append(threading.get_ident())
            return original(*args)

        def launch(*args, **kwargs):
            Path(str(output) + '.part.mp4').write_bytes(b'video')
            return SimpleNamespace(stdout=iter([]), wait=lambda: 0)

        with patch('subprocess.Popen', side_effect=launch), patch.object(self.service, 'finish_merge', side_effect=save):
            self.runner.merge(task.id)
            self.wait(lambda: not FFmpegConverter.IsConverting(str(output)))
        self.assertEqual(self.repo.get(task.id).status, TaskStatus.COMPLETED)
        self.assertEqual(output.read_bytes(), b'video')
        self.assertEqual(len(threads), 1)
        self.assertNotEqual(threads[0], threading.get_ident())
        self.assertIn(task.id, self.runner.changes())

    def test_auto_merge_setting_and_duration_order_are_respected(self):
        task = self.m3u8(count=1)
        self.complete_segments(task)
        with patch.object(self.runner, 'merge') as merge:
            self.runner.complete(task.id)
            merge.assert_not_called()
            SysSetting._values['auto_merge'] = True
            self.runner.complete(task.id)
            merge.assert_called_once_with(task.id)
        pending = self.m3u8('duration', count=1, detect=True)
        self.complete_segments(pending)
        with patch.object(self.runner, 'merge') as merge, patch.object(self.runner, 'queue_duration') as detect:
            self.runner.complete(pending.id)
            detect.assert_called_once_with(pending.id)
            merge.assert_not_called()

    def test_shutdown_preserves_late_merge_result(self):
        task = self.m3u8(count=1)
        self.complete_segments(task)
        with patch.object(FFmpegConverter, 'ConvertTSFile') as convert:
            self.runner.merge(task.id)
        self.runner.shutdown()
        self.assertEqual(self.repo.get(task.id).status, TaskStatus.INTERRUPTED)
        output = task.save_dir / task.outputs[0].output_path
        output.write_bytes(b'finished-after-close')
        callback = convert.call_args.args[2]
        callback(True, str(output), task.id)
        self.assertEqual(self.repo.get(task.id).status, TaskStatus.COMPLETED)

    def test_failed_merge_launch_reports_once_and_does_not_auto_retry(self):
        task = self.m3u8(count=1)
        self.complete_segments(task)
        SysSetting._values['auto_merge'] = True
        with patch.object(FFmpegConverter, 'ConvertTSFile', side_effect=RuntimeError('cannot start thread')) as convert:
            self.runner.complete(task.id)
            self.assertEqual(self.repo.get(task.id).status, TaskStatus.FAILED)
            self.runner.complete(task.id)  # 迟到的完成事件不能再次自动合并。
            convert.assert_called_once()
        self.assertIn('cannot start thread', self.runner.errors.get_nowait())
        self.assertTrue(self.runner.errors.empty())
        self.assertFalse(self.runner.busy_processing(task.id))

    def test_merge_database_failure_does_not_launch_converter(self):
        task = self.m3u8(count=1)
        self.complete_segments(task)
        with patch.object(self.service, 'begin_merge', side_effect=sqlite3.OperationalError('disk full')), \
             patch.object(FFmpegConverter, 'ConvertTSFile') as convert:
            with self.assertRaisesRegex(sqlite3.OperationalError, 'disk full'):
                self.runner.merge(task.id)
            convert.assert_not_called()
        self.assertTrue(Engine.IsPaused())
        self.assertEqual(self.repo.get(task.id).status, TaskStatus.WAITING_MERGE)


if __name__ == '__main__':
    unittest.main()
