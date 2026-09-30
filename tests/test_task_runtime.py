from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
import sqlite3
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import wx

from src.managers.converter import Converter
from src.managers.downloader import Downloader
from src.managers.sys_setting import SysSetting
from src.managers.task_repository import TaskRepository
from src.managers.task_service import TaskService
from src.schemas.task import FileStatus, TaskStatus
from src.views.main_frame import MainFrame


class TaskRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.GetApp() or wx.App(False)

    def setUp(self):
        temp = TemporaryDirectory(prefix='avdownloader-runtime-test-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.repository = TaskRepository(self.root / 'downloads.db')
        self.service = TaskService(self.repository)
        values = dict(SysSetting.Defaults(), download_dir=str(self.root / 'files'), request_interval=0)
        for patcher in (patch.object(SysSetting, '_values', values),
                        patch('src.models.tree_model.TaskService', return_value=self.service),
                        patch.object(Downloader, '_pending', set()),
                        patch.object(Downloader, '_requesting', set()),
                        patch.object(Downloader, '_paused_files', set()),
                        patch.object(Downloader, '_failed', set()),
                        patch.object(Downloader, '_errors', {}),
                        patch.object(Downloader, '_user_paused', threading.Event()),
                        patch.object(Downloader, '_shutdown', threading.Event()),
                        patch.object(Downloader, 'threadQueue', Queue()),
                        patch.object(Converter, '_outputs', set())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def create(self, name='video', count=2):
        content = '#EXTM3U\n' + ''.join(f'#EXTINF:4,\n{i}.ts\n' for i in range(count))
        return self.service.create_m3u8(self.root / name, 'https://example.com/index.m3u8', content)

    def frame(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test')
        self.addCleanup(self.app.ProcessPendingEvents)
        self.addCleanup(frame.Destroy)
        return frame

    def write_segment(self, task, sequence):
        path = task.save_dir / next(segment.relative_path for segment in task.details.segments
                                    if segment.sequence == sequence)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'12345')
        return path

    def enqueue(self, uri, filename, callback, context):
        self.assertEqual(self.repository.get(context[0]).status, TaskStatus.QUEUED)
        Downloader._pending.add(Downloader.FileKey(filename))
        return True

    def test_segment_success_failure_retry_survive_restart(self):
        task = self.create()
        self.service.begin_download(task.id, [0, 1])
        first = self.write_segment(task, 0)
        self.service.finish_segment(task.id, 0, str(first), True)
        second = task.save_dir / task.details.segments[1].relative_path
        failed = self.service.finish_segment(task.id, 1, str(second), False, 'HTTP 403')
        self.assertEqual(failed.status, TaskStatus.FAILED)
        self.assertEqual(failed.progress.completed_segments, 1)
        self.assertEqual(failed.progress.downloaded_bytes, 5)
        reopened = TaskService(TaskRepository(self.repository.path))
        reopened.load_tree()
        restored = reopened.repository.get(task.id)
        self.assertEqual(restored.details.segments[1].last_error, 'HTTP 403')
        self.assertEqual(restored.status, TaskStatus.FAILED)
        reopened.begin_download(task.id, [1])
        self.assertIsNone(reopened.repository.get(task.id).details.segments[1].last_error)
        self.write_segment(task, 1)
        done = reopened.finish_segment(task.id, 1, str(second), True)
        self.assertEqual(done.status, TaskStatus.WAITING_MERGE)
        self.assertEqual(done.progress.downloaded_bytes, 10)

    def test_recovery_marks_active_interrupted_and_preserves_pause(self):
        tasks = [self.create(str(index)) for index in range(4)]
        for task, status in zip(tasks, (TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.MERGING, TaskStatus.PAUSED)):
            self.service.runtime_status(task.id, status)
        self.write_segment(tasks[0], 0)  # 文件已写完，但退出前尚未来得及保存回调。
        partial = tasks[2].save_dir / 'output.mp4.part.mp4'
        partial.write_bytes(b'incomplete')
        with patch.object(Downloader, 'DownloadTSFile') as download:
            tree = TaskService(TaskRepository(self.repository.path)).load_tree()
        download.assert_not_called()
        self.assertEqual([item.task_status for item in tree.items],
                         [TaskStatus.INTERRUPTED] * 3 + [TaskStatus.PAUSED])
        self.assertEqual(self.repository.get(tasks[0].id).progress.completed_segments, 1)
        self.assertEqual(tree.items[2].outputs, [])

    def test_parallel_results_merge_without_lost_updates(self):
        task = self.create(count=12)
        paths = [self.write_segment(task, index) for index in range(12)]
        self.service.begin_download(task.id, range(12))
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda index: self.service.finish_segment(task.id, index, str(paths[index]), True), range(12)))
        restored = self.repository.get(task.id)
        self.assertEqual(restored.progress.completed_segments, 12)
        self.assertEqual(restored.progress.downloaded_bytes, 60)
        self.assertEqual(restored.status, TaskStatus.WAITING_MERGE)

    def test_mutation_failure_rolls_back_parent_and_child(self):
        task = self.create()
        with sqlite3.connect(self.repository.path) as connection:
            connection.execute("""CREATE TRIGGER fail_segment BEFORE UPDATE ON task_segments
                                  BEGIN SELECT RAISE(ABORT, 'write failed'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.finish_segment(task.id, 0, str(task.save_dir / task.details.segments[0].relative_path), False, 'failed')
        self.assertEqual(self.repository.get(task.id), task)

    def test_queue_pause_resume_persist_and_unchanged_poll_does_not_write(self):
        task = self.create()
        frame = self.frame()
        item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        with patch.object(Downloader, 'DownloadTSFile', side_effect=self.enqueue) as download:
            frame.OnTaskAction(item, 'start')
        self.assertEqual([call.args[3] for call in download.call_args_list], [(task.id, 0), (task.id, 1)])
        key = Downloader.FileKey(task.save_dir / task.details.segments[0].relative_path)
        Downloader._requesting.add(key)
        frame.OnTaskProgress(None)
        self.assertEqual(self.repository.get(task.id).status, TaskStatus.DOWNLOADING)
        frame.OnTaskAction(item, 'start')
        self.assertEqual(self.repository.get(task.id).status, TaskStatus.PAUSING)
        Downloader._requesting.clear()
        frame.OnTaskProgress(None)
        self.assertEqual(self.repository.get(task.id).status, TaskStatus.PAUSED)
        with patch.object(self.repository, 'mutate', wraps=self.repository.mutate) as mutate:
            frame.OnTaskProgress(None)
            frame.OnTaskProgress(None)
        mutate.assert_not_called()
        frame.OnTaskAction(item, 'start')
        self.assertEqual(self.repository.get(task.id).status, TaskStatus.QUEUED)

    def test_callback_uses_id_after_reorder_filter_and_deletion(self):
        first, second = self.create('first'), self.create('second')
        frame = self.frame()
        frame.model.fileTree.items.reverse()
        frame._SearchItems('second')
        path = self.write_segment(first, 0)
        frame._DownloadCall(True, str(path), (first.id, 0))
        self.assertEqual(self.repository.get(first.id).progress.completed_segments, 1)
        self.assertEqual(self.repository.get(second.id).progress.completed_segments, 0)
        self.assertEqual(frame.model.fileTree.items[1].download, 1)
        self.repository.delete(first.id)
        frame.OnRefresh(None)
        frame._DownloadCall(True, str(path), (first.id, 0))
        self.assertIsNone(self.repository.get(first.id))
        self.assertEqual(frame.model.fileTree.items[0].task_id, second.id)
        self.assertEqual(frame.model.fileTree.items[0].download, 0)

    def test_failed_callback_retains_error_and_retry_after_reopen(self):
        task = self.create()
        frame = self.frame()
        path = str(task.save_dir / task.details.segments[1].relative_path)
        Downloader._errors[Downloader.FileKey(path)] = 'HTTP 500'
        frame._DownloadCall(False, path, (task.id, 1))
        self.assertEqual(self.repository.get(task.id).last_error, 'HTTP 500')
        Downloader._errors.clear()
        frame.OnRefresh(None)
        info = frame.model.TaskInfo(0)
        self.assertEqual(info['failed'], {Downloader.FileKey(path)})
        item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        with patch.object(Downloader, 'DownloadTSFile', side_effect=self.enqueue) as download:
            frame.OnTaskAction(item, 'retry')
        self.assertEqual([call.args[3] for call in download.call_args_list], [(task.id, 1)])

    def test_all_resume_restores_paused_but_does_not_start_new_tasks(self):
        paused, untouched = self.create('paused'), self.create('new')
        self.service.runtime_status(paused.id, TaskStatus.PAUSED)
        frame = self.frame()
        self.assertTrue(frame._GlobalDownloadActions()[1])
        with patch.object(Downloader, 'DownloadTSFile', side_effect=self.enqueue) as download:
            frame.OnResumeAllDownloads(None)
        self.assertEqual({call.args[3][0] for call in download.call_args_list}, {paused.id})
        self.assertEqual(self.repository.get(untouched.id).status, TaskStatus.NEW)

    def test_database_failure_prevents_new_requests(self):
        self.create()
        frame = self.frame()
        item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        with patch.object(self.repository, 'mutate', side_effect=sqlite3.OperationalError('disk full')), \
             patch('wx.MessageBox') as message, patch.object(Downloader, 'DownloadTSFile') as download:
            frame.OnTaskAction(item, 'start')
        download.assert_not_called()
        message.assert_called_once()
        self.assertTrue(Downloader.IsPaused())

    def test_all_resume_keeps_existing_queue_scope(self):
        task = self.create()
        frame = self.frame()
        key = Downloader.FileKey(task.save_dir / task.details.segments[0].relative_path)
        Downloader._pending.add(key)
        Downloader.Pause()
        frame._SyncTaskRuntime()
        with patch.object(Downloader, 'DownloadTSFile') as enqueue:
            frame.OnResumeAllDownloads(None)
        enqueue.assert_not_called()  # 本来只下载第一片，继续不能擅自把第二片加入队列。
        self.assertEqual(self.repository.get(task.id).status, TaskStatus.QUEUED)

    def test_normal_close_persists_interruption_including_merge(self):
        task = self.create()
        for index in range(2):
            self.write_segment(task, index)
        frame = self.frame()
        record = self.service.begin_merge(task.id)
        frame.model.ApplyTaskRecord(record)
        frame.OnDestroy(SimpleNamespace(GetEventObject=lambda: frame, Skip=lambda: None))
        self.assertEqual(self.repository.get(task.id).status, TaskStatus.INTERRUPTED)
        restored = TaskService(TaskRepository(self.repository.path)).load_tree().items[0]
        self.assertEqual(restored.task_status, TaskStatus.INTERRUPTED)

    def test_completion_waits_for_all_callbacks_and_only_notifies_once(self):
        task = self.create()
        frame = self.frame()
        paths = [self.write_segment(task, index) for index in range(2)]
        Downloader._pending.add(Downloader.FileKey(paths[1]))
        with patch.object(frame.model, '_SendEvent') as event:
            frame._DownloadCall(True, str(paths[0]), (task.id, 0))
            event.assert_not_called()
            Downloader._pending.clear()
            frame._DownloadCall(True, str(paths[1]), (task.id, 1))
            frame._DownloadCall(True, str(paths[1]), (task.id, 1))
            event.assert_called_once()
            self.assertEqual(event.call_args.args[0]['task_id'], task.id)

    def test_missing_completed_segment_downgrades_saved_progress(self):
        task = self.create()
        path = self.write_segment(task, 0)
        self.service.finish_segment(task.id, 0, str(path), True)
        path.unlink()
        TaskService(TaskRepository(self.repository.path)).load_tree()
        restored = self.repository.get(task.id)
        self.assertEqual(restored.progress.completed_segments, 0)
        self.assertEqual(restored.details.segments[0].status, FileStatus.MISSING)
        self.assertEqual(restored.status, TaskStatus.INTERRUPTED)

    def test_late_segment_result_cannot_overwrite_merging_status(self):
        task = self.create()
        frame = self.frame()
        paths = [self.write_segment(task, index) for index in range(2)]
        frame.model.ApplyTaskRecord(self.service.begin_merge(task.id))
        Downloader._pending.add(Downloader.FileKey(paths[1]))
        with patch.object(frame.model, '_SendEvent'):
            frame._DownloadCall(True, str(paths[0]), (task.id, 0))
        frame.OnTaskProgress(None)
        self.assertEqual(self.repository.get(task.id).status, TaskStatus.MERGING)

    def test_status_change_does_not_stat_unchanged_segments(self):
        task = self.create(count=20)
        frame = self.frame()
        record = self.service.runtime_status(task.id, TaskStatus.QUEUED)
        with patch.object(self.service, '_file', wraps=self.service._file) as read_file:
            frame.model.ApplyTaskRecord(record)
        self.assertEqual(read_file.call_count, 1)  # 只更新任务清单的显示信息，不再检查全部 20 个分片。

    def test_merge_result_and_failure_survive_restart(self):
        task = self.create()
        for index in range(2):
            self.write_segment(task, index)
        self.service.begin_merge(task.id)
        output = task.save_dir / 'output.mp4'
        self.service.finish_merge(task.id, str(output), False)
        failed = TaskService(TaskRepository(self.repository.path)).load_tree().items[0]
        self.assertEqual(failed.task_status, TaskStatus.FAILED)
        self.assertEqual(failed.outputs, [])
        self.service.begin_merge(task.id)
        output.write_bytes(b'video')
        self.service.finish_merge(task.id, str(output), True)
        restored = TaskService(TaskRepository(self.repository.path)).load_tree().items[0]
        self.assertEqual(restored.task_status, TaskStatus.COMPLETED)
        self.assertEqual(len(restored.outputs), 1)
        self.assertEqual(self.repository.get(task.id).outputs[0].size_bytes, 5)

    def test_converter_publishes_only_successful_output(self):
        for returncode in (1, 0):
            output = self.root / f'out-{returncode}.mp4'
            temporary = Path(str(output) + '.part.mp4')

            def launch(*args, **kwargs):
                temporary.write_bytes(b'video')
                return SimpleNamespace(stdout=iter([]), wait=lambda: returncode)

            with patch('subprocess.Popen', side_effect=launch), patch('wx.CallAfter') as deliver:
                Converter._ConvertTSFile('playlist.txt', str(output), Mock(), None)
            self.assertEqual(output.exists(), returncode == 0)
            self.assertFalse(temporary.exists())
            self.assertEqual(deliver.call_args.args[1], returncode == 0)

    def test_deliver_exposes_real_error_after_clearing_pending(self):
        task = self.create()
        frame = self.frame()
        filename = str(task.save_dir / task.details.segments[0].relative_path)
        key = Downloader.FileKey(filename)
        Downloader._pending.add(key)
        with patch.object(Downloader, 'DownloadContent', return_value=(False, 'HTTP 403')):
            success, _ = Downloader._DownLoadFile('https://example.com/a.ts', filename)
        Downloader._Deliver(('url', filename, frame._DownloadCall, (task.id, 0), key), success)
        self.assertNotIn(key, Downloader.Snapshot()['pending'])
        self.assertEqual(self.repository.get(task.id).details.segments[0].last_error, 'HTTP 403')


if __name__ == '__main__':
    unittest.main()
