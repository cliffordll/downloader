from src.models import tree_model
from src.models.tree_model import load_tree
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue, SimpleQueue
import sqlite3
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import wx

from src.media.m3u8.ffmpeg_converter import FFmpegConverter
from src.media.m3u8.m3u8_downloader import M3U8Downloader
from src.core.sys_setting import SysSetting
from src.storage.task_repository import TaskRepository
from src.core.task_service import TaskService
from src.schemas.task import FileStatus, TaskStatus
from src.views.main_frame import MainFrame
from src.views.dialogs.settings_dialog import SettingsDialog
from src.views.dialogs.m3u8_dialog import DownloadDialogMU


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
                        patch.object(SysSetting, 'ConfigPath', return_value=self.root / 'settings.json'),
                        patch('src.models.tree_model.TaskService', return_value=self.service),
                        patch.object(M3U8Downloader, '_jobs', {}),
                        patch.object(M3U8Downloader, '_changes', SimpleQueue()),
                        patch.object(M3U8Downloader, 'errors', SimpleQueue()),
                        patch.object(M3U8Downloader, '_unsaved', {}),
                        patch.object(M3U8Downloader, '_storage_error', ''),
                        patch.object(M3U8Downloader, '_pending', set()),
                        patch.object(M3U8Downloader, '_requesting', set()),
                        patch.object(M3U8Downloader, '_paused_files', set()),
                        patch.object(M3U8Downloader, '_failed', set()),
                        patch.object(M3U8Downloader, '_errors', {}),
                        patch.object(M3U8Downloader, '_user_paused', threading.Event()),
                        patch.object(M3U8Downloader, '_shutdown', threading.Event()),
                        patch.object(M3U8Downloader, 'threadQueue', Queue()),
                        patch.object(FFmpegConverter, '_outputs', set())):
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
        M3U8Downloader._pending.add(M3U8Downloader.FileKey(filename))
        return True

    def sync(self, frame):
        # 手动驱动后台调度阶段，再模拟 GUI 定时读取；UI 本身不再负责写状态。
        M3U8Downloader._SyncRuntime()
        frame._SyncTaskRuntime()

    def deliver(self, frame, success, filename, context):
        task_id, _ = context
        if task_id not in M3U8Downloader._jobs:
            record = self.repository.get(task_id)
            if record is None:
                return
            keys = {M3U8Downloader.FileKey(record.save_dir / s.relative_path) for s in record.details.segments}
            M3U8Downloader._jobs[task_id] = [self.service, keys, record.status]
        key = M3U8Downloader.FileKey(filename)
        M3U8Downloader._Deliver(('url', filename, M3U8Downloader._SaveResult, context, key), success)
        self.sync(frame)

    def save_default_directory(self, frame, directory):
        dialog = SettingsDialog(frame)
        try:
            dialog.form.controls['download_dir'].SetPath(str(directory))
            with patch.object(dialog, 'EndModal') as end, patch('wx.MessageBox') as message:
                dialog.form.OnSave(None)
            end.assert_called_once_with(wx.ID_OK)
            message.assert_not_called()
        finally:
            dialog.Destroy()

    def test_switch_default_while_downloading_preserves_callbacks_and_retry_paths(self):
        task = self.create()
        frame = self.frame()
        item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        with patch.object(M3U8Downloader, 'DownloadTSFile', side_effect=self.enqueue):
            frame.OnTaskAction(item, 'start')
        first_path = task.save_dir / task.details.segments[0].relative_path
        key = M3U8Downloader.FileKey(first_path)
        M3U8Downloader._requesting.add(key)
        self.sync(frame)
        before = self.repository.get(task.id)
        pending = M3U8Downloader.Snapshot()['pending']
        new_default = self.root / 'new-default'
        self.save_default_directory(frame, new_default)
        self.assertEqual(self.repository.get(task.id), before)
        self.assertEqual(M3U8Downloader.Snapshot()['pending'], pending)
        self.assertFalse(M3U8Downloader.IsPaused())
        SysSetting._values = None  # 从设置文件重新读取，验证默认目录确实已保存。
        self.assertEqual(Path(SysSetting.GetWorkPath()), new_default)
        self.write_segment(task, 0)
        M3U8Downloader._requesting.clear()
        M3U8Downloader._pending.discard(key)
        self.deliver(frame, True, str(first_path), (task.id, 0))
        second_path = task.save_dir / task.details.segments[1].relative_path
        M3U8Downloader._pending.clear()
        self.deliver(frame, False, str(second_path), (task.id, 1))
        with patch.object(M3U8Downloader, 'DownloadTSFile', side_effect=self.enqueue) as retry:
            frame.OnTaskAction(item, 'retry')
        self.assertEqual(Path(retry.call_args.args[1]), second_path)
        self.assertEqual(self.repository.get(task.id).save_dir, task.save_dir)
        self.assertFalse((new_default / 'segments').exists())

        # 从实际添加入口取得新默认目录，并通过表单创建新任务。
        dialog = DownloadDialogMU(frame, 'test', SysSetting.GetWorkPath(), task_service=self.service)
        try:
            dialog.downPath.tcDown.SetValue('new-task')
            dialog.downEdit.tcURI.SetValue('https://example.com/new.m3u8')
            dialog.downEdit.tsList.SetValue('#EXTM3U\n#EXTINF:4,\na.ts\n')
            with patch.object(dialog, 'EndModal') as end:
                dialog.OnDownBtnClicked(None)
            end.assert_called_once_with(wx.OK)
        finally:
            dialog.Destroy()
        self.assertEqual({record.save_dir for record in self.repository.list_tasks()},
                         {task.save_dir, new_default / 'new-task'})

    def test_switch_default_while_merging_preserves_output_and_folder_actions(self):
        task = self.create()
        for sequence in range(2):
            self.write_segment(task, sequence)
        frame = self.frame()
        item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        with patch.object(FFmpegConverter, 'ConvertTSFile') as convert:
            frame.OnTaskAction(item, 'merge')
        output = Path(convert.call_args.args[1])
        FFmpegConverter._outputs.add(str(output))
        self.assertTrue(FFmpegConverter.IsBusy())
        new_default = self.root / 'new-default'
        self.save_default_directory(frame, new_default)
        self.assertEqual(output, task.save_dir / 'output.mp4')
        self.assertEqual(self.repository.get(task.id).status, TaskStatus.MERGING)
        output.write_bytes(b'video')
        frame._CreateMP4Call(True, str(output), task.id)
        FFmpegConverter._outputs.clear()
        frame.OnRefresh(None)
        self.assertEqual(self.repository.get(task.id).save_dir, task.save_dir)
        self.assertFalse((new_default / 'output.mp4').exists())

        def choose_folder(menu):
            entry = next(entry for entry in menu.GetMenuItems() if entry.GetItemLabelText() == '打开文件夹')
            event = wx.CommandEvent(wx.EVT_MENU.typeId, entry.GetId())
            menu.ProcessEvent(event)

        with patch.object(frame, '_OpenLocalPath') as open_path:
            frame.OnOpen(None)
            self.assertEqual(Path(open_path.call_args.args[0]), new_default)
            with patch.object(frame.mcTree, 'PopupMenu', side_effect=choose_folder):
                frame.OnTaskMenu(frame.model.ObjectToItem(frame.model._BuildKey((0,))))
            self.assertEqual(Path(open_path.call_args.args[0]), task.save_dir)

    def test_invalid_default_directory_does_not_disrupt_running_task(self):
        task = self.create()
        frame = self.frame()
        item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        with patch.object(M3U8Downloader, 'DownloadTSFile', side_effect=self.enqueue):
            frame.OnTaskAction(item, 'start')
        original_settings = SysSetting.GetAll()
        original_task = self.repository.get(task.id)
        pending = M3U8Downloader.Snapshot()['pending']
        invalid = self.root / 'not-a-directory'
        invalid.write_bytes(b'keep')
        dialog = SettingsDialog(frame)
        try:
            dialog.form.controls['download_dir'].SetPath(str(invalid))
            with patch.object(dialog, 'EndModal') as end, patch('wx.MessageBox') as message:
                dialog.form.OnSave(None)
            end.assert_not_called()
            message.assert_called_once()
        finally:
            dialog.Destroy()
        self.assertEqual(SysSetting.GetAll(), original_settings)
        self.assertEqual(self.repository.get(task.id), original_task)
        self.assertEqual(M3U8Downloader.Snapshot()['pending'], pending)
        self.assertFalse(M3U8Downloader.IsPaused())

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
        load_tree(reopened)
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
        with patch.object(M3U8Downloader, 'DownloadTSFile') as download:
            tree = load_tree(TaskService(TaskRepository(self.repository.path)))
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
        with patch.object(M3U8Downloader, 'DownloadTSFile', side_effect=self.enqueue) as download:
            frame.OnTaskAction(item, 'start')
        self.assertEqual([call.args[3] for call in download.call_args_list], [(task.id, 0), (task.id, 1)])
        key = M3U8Downloader.FileKey(task.save_dir / task.details.segments[0].relative_path)
        M3U8Downloader._requesting.add(key)
        self.sync(frame)
        frame.OnTaskProgress(None)
        self.assertEqual(self.repository.get(task.id).status, TaskStatus.DOWNLOADING)
        frame.OnTaskAction(item, 'start')
        self.sync(frame)
        self.assertEqual(self.repository.get(task.id).status, TaskStatus.PAUSING)
        M3U8Downloader._requesting.clear()
        self.sync(frame)
        frame.OnTaskProgress(None)
        self.assertEqual(self.repository.get(task.id).status, TaskStatus.PAUSED)
        with patch.object(self.repository, 'mutate', wraps=self.repository.mutate) as mutate:
            self.sync(frame)
            frame.OnTaskProgress(None)
            self.sync(frame)
            frame.OnTaskProgress(None)
        mutate.assert_not_called()
        frame.OnTaskAction(item, 'start')
        self.sync(frame)
        self.assertEqual(self.repository.get(task.id).status, TaskStatus.QUEUED)

    def test_gui_progress_poll_only_reads_persisted_notifications(self):
        task = self.create()
        frame = self.frame()
        self.service.runtime_status(task.id, TaskStatus.DOWNLOADING)
        M3U8Downloader._changes.put(task.id)
        with patch.object(self.repository, 'mutate', wraps=self.repository.mutate) as mutate:
            frame.OnTaskProgress(None)
            frame.OnTaskProgress(None)
        mutate.assert_not_called()
        self.assertEqual(frame.model.fileTree.items[0].task_status, TaskStatus.DOWNLOADING)

    def test_callback_uses_id_after_reorder_filter_and_deletion(self):
        first, second = self.create('first'), self.create('second')
        frame = self.frame()
        frame.model.fileTree.items.reverse()
        frame._SearchItems('second')
        path = self.write_segment(first, 0)
        self.deliver(frame, True, str(path), (first.id, 0))
        self.assertEqual(self.repository.get(first.id).progress.completed_segments, 1)
        self.assertEqual(self.repository.get(second.id).progress.completed_segments, 0)
        self.assertEqual(frame.model.fileTree.items[1].download, 1)
        self.repository.delete(first.id)
        frame.OnRefresh(None)
        self.deliver(frame, True, str(path), (first.id, 0))
        self.assertIsNone(self.repository.get(first.id))
        self.assertEqual(frame.model.fileTree.items[0].task_id, second.id)
        self.assertEqual(frame.model.fileTree.items[0].download, 0)

    def test_failed_callback_retains_error_and_retry_after_reopen(self):
        task = self.create()
        frame = self.frame()
        path = str(task.save_dir / task.details.segments[1].relative_path)
        M3U8Downloader._errors[M3U8Downloader.FileKey(path)] = 'HTTP 500'
        self.deliver(frame, False, path, (task.id, 1))
        self.assertEqual(self.repository.get(task.id).last_error, 'HTTP 500')
        M3U8Downloader._errors.clear()
        frame.OnRefresh(None)
        info = frame.model.TaskInfo(0)
        self.assertEqual(info['failed'], {M3U8Downloader.FileKey(path)})
        item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        with patch.object(M3U8Downloader, 'DownloadTSFile', side_effect=self.enqueue) as download:
            frame.OnTaskAction(item, 'retry')
        self.assertEqual([call.args[3] for call in download.call_args_list], [(task.id, 1)])

    def test_all_resume_restores_paused_but_does_not_start_new_tasks(self):
        paused, untouched = self.create('paused'), self.create('new')
        self.service.runtime_status(paused.id, TaskStatus.PAUSED)
        frame = self.frame()
        self.assertTrue(frame._GlobalDownloadActions()[1])
        with patch.object(M3U8Downloader, 'DownloadTSFile', side_effect=self.enqueue) as download:
            frame.OnResumeAllDownloads(None)
        self.assertEqual({call.args[3][0] for call in download.call_args_list}, {paused.id})
        self.assertEqual(self.repository.get(untouched.id).status, TaskStatus.NEW)

    def test_database_failure_prevents_new_requests(self):
        self.create()
        frame = self.frame()
        item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        with patch.object(self.repository, 'mutate', side_effect=sqlite3.OperationalError('disk full')), \
             patch('wx.MessageBox') as message, patch.object(M3U8Downloader, 'DownloadTSFile') as download:
            frame.OnTaskAction(item, 'start')
        download.assert_not_called()
        message.assert_called_once()
        self.assertTrue(M3U8Downloader.IsPaused())

    def test_all_resume_keeps_existing_queue_scope(self):
        task = self.create()
        frame = self.frame()
        key = M3U8Downloader.FileKey(task.save_dir / task.details.segments[0].relative_path)
        M3U8Downloader._pending.add(key)
        M3U8Downloader.Pause()
        M3U8Downloader._jobs[task.id] = [self.service, {key}, task.status]
        self.sync(frame)
        with patch.object(M3U8Downloader, 'DownloadTSFile') as enqueue:
            frame.OnResumeAllDownloads(None)
        enqueue.assert_not_called()  # 本来只下载第一片，继续不能擅自把第二片加入队列。
        self.sync(frame)
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
        restored = load_tree(TaskService(TaskRepository(self.repository.path))).items[0]
        self.assertEqual(restored.task_status, TaskStatus.INTERRUPTED)

    def test_redownload_starts_new_completion_cycle_and_auto_merge(self):
        task = self.create(count=1)
        frame = self.frame()
        item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        SysSetting._values['auto_merge'] = True
        with patch.object(frame, '_CreateMP4File') as merge:
            for cycle in range(2):
                with patch.object(M3U8Downloader, 'DownloadTSFile', side_effect=self.enqueue):
                    frame.OnTaskAction(item, 'start')
                path = self.write_segment(task, 0)
                M3U8Downloader._pending.clear()
                self.deliver(frame, True, str(path), (task.id, 0))
                self.deliver(frame, True, str(path), (task.id, 0))
                self.app.ProcessPendingEvents()
                self.assertEqual(merge.call_count, cycle + 1)
                path.unlink()
                frame.OnRefresh(None)

    def test_completion_waits_for_all_callbacks_and_only_notifies_once(self):
        task = self.create()
        frame = self.frame()
        paths = [self.write_segment(task, index) for index in range(2)]
        M3U8Downloader._pending.add(M3U8Downloader.FileKey(paths[1]))
        with patch.object(frame.model, '_SendEvent') as event:
            self.deliver(frame, True, str(paths[0]), (task.id, 0))
            event.assert_not_called()
            M3U8Downloader._pending.clear()
            self.deliver(frame, True, str(paths[1]), (task.id, 1))
            self.deliver(frame, True, str(paths[1]), (task.id, 1))
            event.assert_called_once()
            self.assertEqual(event.call_args.args[0]['task_id'], task.id)

    def test_duration_detection_defers_completion_and_notifies_once(self):
        task = self.create(count=1)
        task = self.repository.mutate(task.id, lambda current: setattr(current.details, 'detect_duration', True))
        frame = self.frame()
        path = self.write_segment(task, 0)
        with patch.object(frame, '_QueueDurationCheck') as queue, patch.object(frame.model, '_SendEvent') as event:
            self.deliver(frame, True, str(path), (task.id, 0))
            queue.assert_called_once()
            event.assert_not_called()
            self.assertEqual(frame.model.TaskInfo(0)['status'], '检测时长')
            with patch('src.core.task_service.FFmpegConverter.ProbeDuration', return_value=3.5):
                record = self.service.detect_durations(task.id)
            frame._DurationChecked(task.id, record, None)
            frame._DurationChecked(task.id, record, None)
            event.assert_called_once()
            self.assertEqual(frame.model.TaskInfo(0)['status'], '待合并')
            self.assertIn('#EXTINF:3.5,', (task.save_dir / 'download.m3u8').read_text())

    def test_restored_task_passes_saved_headers_to_segment_queue(self):
        task = self.create(count=1)
        headers = {'Referer': 'https://example.com/watch', 'Cookie': 'session=test'}
        self.repository.mutate(task.id, lambda record: setattr(record.details, 'request_headers', headers))
        frame = self.frame()
        with patch.object(M3U8Downloader, 'DownloadTSFile', return_value=True) as enqueue:
            frame._DownloadFiles(frame.model.fileTree.items[0].parent.fileName, [(0, None)])
        self.assertEqual(enqueue.call_args.kwargs['headers'], headers)

    def test_mp4_row_and_global_actions_use_direct_engine(self):
        task = self.service.create_mp4(self.root / 'direct', 'https://example.com/video.mp4')
        frame = self.frame()
        item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        with patch.object(frame, '_StartMP4') as start, patch.object(frame.mp4, 'busy', return_value=False):
            frame.OnTaskAction(item, 'start')
            start.assert_called_once_with(task.id)
        record = self.repository.mutate(task.id, lambda r: setattr(r, 'status', TaskStatus.DOWNLOADING))
        frame.model.ApplyTaskRecord(record)
        self.assertTrue(frame._GlobalDownloadActions()[0])
        with patch.object(frame.mp4, 'pause') as pause, patch.object(frame.mp4, 'busy', return_value=True):
            frame.OnTaskAction(item, 'start')
            pause.assert_called_once_with(task.id)
            pause.reset_mock()
            frame.OnPauseAllDownloads(None)
            pause.assert_called_once_with(task.id)
        record = self.repository.mutate(task.id, lambda r: setattr(r, 'status', TaskStatus.PAUSED))
        frame.model.ApplyTaskRecord(record)
        with patch.object(frame, '_StartMP4') as start:
            frame.OnResumeAllDownloads(None)
            start.assert_called_once_with(task.id)
        self.assertFalse(frame.model.IsContainer(item))

    def test_missing_completed_segment_downgrades_saved_progress(self):
        task = self.create()
        path = self.write_segment(task, 0)
        self.service.finish_segment(task.id, 0, str(path), True)
        path.unlink()
        load_tree(TaskService(TaskRepository(self.repository.path)))
        restored = self.repository.get(task.id)
        self.assertEqual(restored.progress.completed_segments, 0)
        self.assertEqual(restored.details.segments[0].status, FileStatus.MISSING)
        self.assertEqual(restored.status, TaskStatus.INTERRUPTED)

    def test_late_segment_result_cannot_overwrite_merging_status(self):
        task = self.create()
        frame = self.frame()
        paths = [self.write_segment(task, index) for index in range(2)]
        frame.model.ApplyTaskRecord(self.service.begin_merge(task.id))
        M3U8Downloader._pending.add(M3U8Downloader.FileKey(paths[1]))
        with patch.object(frame.model, '_SendEvent'):
            self.deliver(frame, True, str(paths[0]), (task.id, 0))
        self.sync(frame)
        frame.OnTaskProgress(None)
        self.assertEqual(self.repository.get(task.id).status, TaskStatus.MERGING)

    def test_status_change_does_not_stat_unchanged_segments(self):
        task = self.create(count=20)
        frame = self.frame()
        record = self.service.runtime_status(task.id, TaskStatus.QUEUED)
        with patch('src.models.tree_model._file', wraps=tree_model._file) as read_file:
            frame.model.ApplyTaskRecord(record)
        self.assertEqual(read_file.call_count, 1)  # 只更新任务清单的显示信息，不再检查全部 20 个分片。

    def test_merge_result_and_failure_survive_restart(self):
        task = self.create()
        for index in range(2):
            self.write_segment(task, index)
        self.service.begin_merge(task.id)
        output = task.save_dir / 'output.mp4'
        self.service.finish_merge(task.id, str(output), False)
        failed = load_tree(TaskService(TaskRepository(self.repository.path))).items[0]
        self.assertEqual(failed.task_status, TaskStatus.FAILED)
        self.assertEqual(failed.outputs, [])
        self.service.begin_merge(task.id)
        output.write_bytes(b'video')
        self.service.finish_merge(task.id, str(output), True)
        restored = load_tree(TaskService(TaskRepository(self.repository.path))).items[0]
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
                FFmpegConverter._ConvertTSFile('playlist.txt', str(output), Mock(), None)
            self.assertEqual(output.exists(), returncode == 0)
            self.assertFalse(temporary.exists())
            self.assertEqual(deliver.call_args.args[1], returncode == 0)

    def test_deliver_exposes_real_error_after_clearing_pending(self):
        task = self.create()
        frame = self.frame()
        filename = str(task.save_dir / task.details.segments[0].relative_path)
        key = M3U8Downloader.FileKey(filename)
        M3U8Downloader._pending.add(key)
        with patch.object(M3U8Downloader, 'DownloadContent', return_value=(False, 'HTTP 403')):
            success, _ = M3U8Downloader._DownLoadFile('https://example.com/a.ts', filename)
        self.deliver(frame, success, filename, (task.id, 0))
        self.assertNotIn(key, M3U8Downloader.Snapshot()['pending'])
        self.assertEqual(self.repository.get(task.id).details.segments[0].last_error, 'HTTP 403')


if __name__ == '__main__':
    unittest.main()
