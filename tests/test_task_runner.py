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
        self.addCleanup(self.runner.mp4._pool.shutdown, wait=True)
        self.addCleanup(self.runner.shutdown)

    def m3u8(self, name='hls', count=3, detect=False):
        return self.service.create_m3u8(self.root / name, 'https://example.com/index.m3u8',
            '#EXTM3U\n' + ''.join(f'#EXTINF:4,\n{i}.ts\n' for i in range(count)), detect_duration=detect)

    def mp4(self, name='direct'):
        return self.service.create_mp4(self.root / name, 'https://example.com/video.mp4')

    def write(self, task, sequence):
        path = task.save_dir / task.details.segments[sequence].relative_path
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
        self.service.finish_segment(hls.id, 1, str(hls.save_dir / hls.details.segments[1].relative_path), False, 'HTTP 500')
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
        (task.save_dir / task.details.segments[1].relative_path).unlink()
        changed = task.save_dir / task.details.segments[2].relative_path
        changed.write_bytes(b'bad')
        (other.save_dir / other.details.segments[0].relative_path).unlink()
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
        (task.save_dir / task.details.segments[1].relative_path).unlink()
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
            Engine.FileKey(task.save_dir / task.details.segments[index].relative_path) for index in (0, 1)})

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
        self.assertEqual(snapshot['paused_files'], {Engine.FileKey(other.save_dir / other.details.segments[0].relative_path)})
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
                            str(task.save_dir / segment.relative_path), False, 'HTTP 500')
                    Engine.Pause()
                    self.assertEqual(self.runner.activate(task.id, retry=retry), 1 if retry else 3)
                    snapshot = Engine.Snapshot()
                    self.assertFalse(snapshot['paused'])
                    selected = {Engine.FileKey(task.save_dir / s.relative_path)
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

    def test_delete_rechecks_busy_state_and_keeps_downloaded_files(self):
        task = self.m3u8()
        path = self.write(task, 0)
        Engine._pending.add(Engine.FileKey(task.save_dir / task.details.segments[1].relative_path))
        with self.assertRaisesRegex(ValueError, '不能删除'):
            self.runner.delete(task.id)
        Engine._pending.clear()
        self.runner.delete(task.id)
        self.assertIsNone(self.repo.get(task.id))
        self.assertTrue(path.is_file())

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
        output = task.save_dir / task.outputs[0].relative_path
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
        output = task.save_dir / task.outputs[0].relative_path
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
