from src.models.tree_model import load_tree
from pathlib import Path
import sqlite3
import threading
from queue import SimpleQueue
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import wx

from src.media.m3u8.m3u8_downloader import M3U8Downloader
from src.core.sys_setting import SysSetting
from src.storage.task_repository import TaskRepository
from src.core.task_service import TaskService
from src.schemas.task import SourceType, TaskStatus
from src.views.main_frame import MainFrame
from src.views.dialogs.m3u8_dialog import DownloadDialogMU
from src.views.dialogs.ts_dialog import DownloadDialogTS
from src.views.dialogs.mp4_dialog import DownloadDialogMP4


CONTENT = '#EXTM3U\n#EXTINF:4,\na.ts?token=1\n#EXTINF:5,\na.ts?token=2\n#EXT-X-ENDLIST\n'


class TaskServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.GetApp() or wx.App(False)

    def setUp(self):
        temp = TemporaryDirectory(prefix='avdownloader-service-test-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.repository = TaskRepository(self.root / 'app' / 'downloads.db')
        self.service = TaskService(self.repository)
        self.directory = self.root / 'download' / 'video'
        config = dict(SysSetting.Defaults(), download_dir=str(self.root / 'download'))
        for patcher in (patch.object(M3U8Downloader, '_shutdown', threading.Event()),
                        patch.object(M3U8Downloader, '_jobs', {}),
                        patch.object(M3U8Downloader, '_changes', SimpleQueue()),
                        patch.object(M3U8Downloader, 'errors', SimpleQueue()),
                        patch.object(SysSetting, '_values', config),):
            patcher.start()
            self.addCleanup(patcher.stop)

    def create(self):
        return self.service.create_m3u8(self.directory, 'https://example.com/media/index.m3u8', CONTENT, filename='video.mp4')

    def test_task_lock_marker_persists_without_changing_output_paths(self):
        task = self.create()
        paths = [output.output_path for output in task.outputs]

        locked = self.service.set_task_encrypted(task.id, True)
        self.assertTrue(locked.encrypted)
        self.assertEqual([output.output_path for output in locked.outputs], paths)
        self.assertTrue(self.repository.get(task.id).encrypted)

        unlocked = self.service.set_task_encrypted(task.id, False)
        self.assertFalse(unlocked.encrypted)

    def test_encrypted_mp4_updates_persisted_paths_and_renames_artifacts(self):
        task = self.service.create_mp4(self.root, 'https://example.com/movie.mp4')
        output = self.root / task.outputs[0].output_path
        partial = self.root / task.details.temporary_path
        output.write_bytes(b'container')
        partial.write_bytes(b'partial container')

        locked = self.service.set_task_encrypted(task.id, True)

        self.assertEqual(locked.outputs[0].output_path, 'movie.mp4.enc')
        self.assertEqual(locked.details.temporary_path, 'movie.mp4.enc.part')
        self.assertFalse(output.exists())
        self.assertFalse(partial.exists())
        self.assertEqual((self.root / 'movie.mp4.enc').read_bytes(), b'container')
        self.assertEqual((self.root / 'movie.mp4.enc.part').read_bytes(), b'partial container')

        unlocked = self.service.set_task_encrypted(task.id, False)
        self.assertEqual(unlocked.outputs[0].output_path, 'movie.mp4')
        self.assertEqual(unlocked.details.temporary_path, 'movie.mp4.part')
        self.assertEqual(output.read_bytes(), b'container')
        self.assertEqual(partial.read_bytes(), b'partial container')

    def test_task_lock_marker_rejects_active_tasks(self):
        task = self.create()
        self.service.begin_download(task.id, [0])
        with self.assertRaisesRegex(ValueError, '正在下载'):
            self.service.set_task_encrypted(task.id, True)

    def test_mp4_dialog_creates_record_and_returns_task_id(self):
        dialog = DownloadDialogMP4(None, str(self.root), self.service)
        try:
            dialog.downEdit.tcURI.SetValue('https://example.com/movie.mp4')
            dialog.downPath.tcDown.SetValue('direct-video')
            with patch.object(dialog, 'EndModal') as end:
                dialog.OnDownBtnClicked(None)
                end.assert_called_once_with(wx.OK)
            task = self.repository.get(dialog.task_id)
            self.assertEqual(task.source_url, 'https://example.com/movie.mp4')
            self.assertEqual(task.save_dir, self.root)
            self.assertEqual(task.name, 'direct-video')
            target = task.save_dir / task.outputs[0].output_path
            self.assertEqual(target.name, 'direct-video.mp4')
            self.assertFalse(target.exists())
        finally:
            dialog.Destroy()
        self.app.ProcessPendingEvents()

    def test_all_dialogs_share_directory_and_preserve_edited_filename(self):
        dialogs = [DownloadDialogMP4(None, str(self.root), self.service),
                   DownloadDialogMU(None, str(self.root), self.service),
                   DownloadDialogTS(None, str(self.root), self.service)]
        try:
            for dialog in dialogs:
                dialog.Layout()
                dialog.downPath.Layout()
                dialog.downEdit.tcURI.SetValue('https://example.com/movie.mp4?token=123')
                self.assertEqual(dialog.downPath.GetDownPath(), str(self.root))
                self.assertEqual(dialog.downPath.GetFileName(), 'movie.mp4')
                dialog.downPath.tcDown.SetValue('我的视频')
                dialog.downEdit.tcURI.SetValue('https://example.com/other.m3u8')
                self.assertEqual(dialog.downPath.GetFileName(), '我的视频.mp4')
                # 新保存区域的两行都在对话框内部，右侧留有外边距。
                panel = dialog.downPath
                self.assertLess(panel.GetRect().right, dialog.GetClientSize().width)
                self.assertLessEqual(panel.GetRect().bottom, dialog.GetClientSize().height)
                self.assertGreater(panel.tcDown.GetSize().width, 100)
        finally:
            for dialog in dialogs:
                dialog.Destroy()
            self.app.ProcessPendingEvents()

    def frame(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, load_tree(self.service))
        self.addCleanup(self.app.ProcessPendingEvents)
        self.addCleanup(frame.Destroy)
        return frame

    def test_redownload_restores_native_tree_container_after_all_files_deleted(self):
        task = self.create()
        for segment in task.details.segments:
            path = task.save_dir / task.name / segment.segment_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'media')
            self.service.finish_segment(task.id, segment.sequence, path, True)
        work = (task.save_dir / task.name / task.details.playlist_path).parent
        work.replace(self.root / 'removed-work')
        self.service.check_files(task.id)
        frame = self.frame()
        frame.Show()
        self.app.Yield()
        root = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        self.assertFalse(frame.model.IsContainer(root))
        frame.mcTree.Expand(root)
        self.assertFalse(frame.mcTree.IsExpanded(root))

        def start(*args, **kwargs):
            self.service.begin_download(task.id, [0, 1])
            return True

        with patch.object(frame.runner, 'activate', side_effect=start), \
             patch.object(frame.runner, 'changes', return_value={task.id}):
            frame.OnTaskAction(root, 'start')
        root = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        self.assertTrue(frame.model.IsContainer(root))
        children = []
        self.assertEqual(frame.model.GetChildren(root, children), 1)
        self.assertEqual(frame.model.GetValue(children[0], 1), 'video/')
        self.app.ProcessPendingEvents()
        self.assertTrue(frame.mcTree.IsExpanded(root))
        self.assertGreater(frame.mcTree.GetItemRect(children[0]).height, 0)

    def test_creation_persists_resolved_urls_and_local_playlist(self):
        task = self.create()
        self.assertEqual(task.source_type, SourceType.M3U8)
        self.assertEqual([segment.source_url for segment in task.details.segments], [
            'https://example.com/media/a.ts?token=1', 'https://example.com/media/a.ts?token=2'])
        self.assertEqual(len({segment.segment_path for segment in task.details.segments}), 2)
        self.assertFalse((self.directory / 'download.seed').exists())
        content = (self.directory / 'video/download.m3u8').read_text(encoding='utf-8')
        self.assertIn('segments/000001.ts', content)
        self.assertNotIn('token=', content)
        self.assertEqual(self.repository.get(task.id), task)

    def test_ts_source_without_reference_and_path_rewrite(self):
        task = self.service.create_m3u8(self.directory, '', CONTENT.replace('a.ts?token=', 'https://a.test/a.ts?token='),
                                        source_type=SourceType.TS_PATTERN, ts_pattern='a{1}.ts')
        self.assertEqual(task.source_type, SourceType.TS_PATTERN)
        self.assertIsNone(task.source_url)
        other = self.service.create_m3u8(self.root / 'other', 'https://example.com/media/index.m3u8',
                                         CONTENT, '/chunks')
        self.assertEqual(other.details.segments[0].source_url, 'https://example.com/chunks/a.ts?token=1')

    def test_rejects_empty_unresolved_unsupported_and_existing_directory(self):
        invalid = ['', '#EXTM3U\n', 'garbage']
        for content in invalid:
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.service.create_m3u8(self.directory, 'https://example.com/index.m3u8', content)
        with self.assertRaises(ValueError):
            self.service.create_m3u8(self.directory, '', CONTENT, source_type=SourceType.TS_PATTERN)
        self.assertFalse(self.directory.exists())
        aes_directory = self.root / 'encrypted'
        encrypted = self.service.create_m3u8(
            aes_directory, 'https://example.com/index.m3u8',
            '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXTINF:4,\na.ts\n')
        self.assertEqual([entry.entry_type for entry in encrypted.details.segments], ['key', 'segment'])
        self.directory.mkdir(parents=True)
        existing = self.directory / 'keep.txt'
        existing.write_text('keep')
        task = self.create()
        self.assertEqual(existing.read_text(), 'keep')
        self.assertEqual(task.outputs[0].output_path, 'video.mp4')

    def test_aes128_key_rotation_and_iv_are_saved_per_playlist_entry(self):
        content = ('#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:20\n'
                   '#EXT-X-KEY:METHOD=AES-128,URI="key-a",IV=0x1\n'
                   '#EXTINF:4,\na.ts\n'
                   '#EXT-X-KEY:METHOD=AES-128,URI="key-a",IV=0x2\n'
                   '#EXTINF:4,\nb.ts\n'
                   '#EXT-X-KEY:METHOD=AES-128,URI="key-b"\n'
                   '#EXTINF:4,\nc.ts\n'
                   '#EXT-X-KEY:METHOD=NONE\n#EXTINF:4,\nd.ts\n')
        task = self.service.create_m3u8(self.root / 'rotated', 'https://example.com/hls/index.m3u8', content)
        keys = task.details.key_entries
        media = task.details.media_segments
        self.assertEqual([key.source_url for key in keys], [
            'https://example.com/hls/key-a', 'https://example.com/hls/key-b'])
        self.assertEqual([segment.encryption['iv'] for segment in media[:3]], [
            '0x' + '0' * 31 + '1', '0x' + '0' * 31 + '2', '0x' + f'{22:032x}'])
        self.assertEqual(media[0].encryption['key_sequence'], media[1].encryption['key_sequence'])
        self.assertNotEqual(media[1].encryption['key_sequence'], media[2].encryption['key_sequence'])
        self.assertEqual(media[3].encryption, {'method': 'NONE'})

    def test_sample_aes_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '暂不支持此加密方式'):
            self.service.create_m3u8(self.root / 'sample-aes', 'https://example.com/index.m3u8',
                '#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="key"\n#EXTINF:4,\na.ts\n')

    def test_recovery_clears_stale_key_and_decryption_states_even_for_partial_task(self):
        from src.schemas.task import SegmentStatus, TaskStatus
        task = self.service.create_m3u8(self.root / 'recover', 'https://example.com/index.m3u8',
            '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXTINF:4,\na.ts\n')
        def leave_in_flight(record):
            record.status = TaskStatus.PARTIAL
            record.details.segments[0].status = SegmentStatus.DOWNLOADING
            record.details.segments[1].status = SegmentStatus.DECRYPTING
        self.repository.mutate(task.id, leave_in_flight)

        restored = TaskService(self.repository).load_tasks()[0]

        self.assertEqual(restored.status, TaskStatus.PARTIAL)
        self.assertEqual([entry.status for entry in restored.details.segments],
                         [SegmentStatus.PENDING, SegmentStatus.PENDING])

    def test_database_failure_cleans_only_new_artifacts(self):
        with patch.object(self.repository, '_write_children', side_effect=sqlite3.OperationalError('disk full')):
            with self.assertRaises(sqlite3.OperationalError):
                self.create()
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertEqual(self.repository.list_tasks(), [])

    def test_reopen_does_not_scan_or_import_unregistered_files(self):
        task = self.create()
        unrelated = self.directory / 'old.seed'
        unrelated.write_text(CONTENT)
        (self.directory / 'video/download.m3u8').unlink()
        reopened = TaskService(TaskRepository(self.repository.path))
        with patch('os.walk', side_effect=AssertionError('must not scan')):
            tree = load_tree(reopened)
            self.assertEqual(len(tree.items), 1)
            self.assertEqual(tree.items[0].task_id, task.id)
            self.assertEqual(len(tree.items[0].childs), 2)
        path = reopened.write_playlist(task.id)
        self.assertTrue(path.is_file())
        self.repository.delete(task.id)
        self.assertEqual(load_tree(reopened).items, [])
        self.assertTrue(path.is_file())

    def test_startup_refresh_and_tree_projection_do_not_read_download_files(self):
        task = self.create()
        segment = task.details.segments[0]
        path = task.save_dir / task.name / segment.segment_path
        path.parent.mkdir(parents=True)
        path.write_bytes(b'data')
        self.service.finish_segment(task.id, segment.sequence, str(path), True)
        self.service.runtime_status(task.id, TaskStatus.DOWNLOADING)
        path.unlink()
        (task.save_dir / task.name / task.details.playlist_path).unlink()
        reopened = TaskService(self.repository)
        original_stat = Path.stat

        def stat(path, *args, **kwargs):
            if str(path).startswith(str(task.save_dir)):
                self.fail('启动或列表展示不应访问下载文件：' + str(path))
            return original_stat(path, *args, **kwargs)

        with patch.object(Path, 'stat', stat), patch.object(Path, 'iterdir', side_effect=AssertionError('must not scan')):
            tree = load_tree(reopened)
            with patch.object(MainFrame, 'Show'):
                frame = MainFrame(None, 'test', reopened, tree)
            try:
                with patch.object(frame.runner, 'queue_duration') as duration:
                    frame.OnRefresh(None)
                    frame.OnExpandAll(None)
                    frame.OnTaskProgress(None)
                    duration.assert_not_called()
                row = frame.model.fileTree.items[0]
                self.assertEqual(row.progress.completed_segments, 1)
                self.assertEqual(row.childs[0].fileSize, '4.00 B')
                self.assertEqual(row.childs[0].modifyAt, '—')
                self.assertEqual(frame.model.TaskInfo(0)['progress'], '50% · 1/2')
            finally:
                frame.Destroy()
                self.app.ProcessPendingEvents()

    def test_download_uses_recorded_paths_after_default_directory_change(self):
        task = self.create()
        frame = self.frame()
        SysSetting._values['download_dir'] = str(self.root / 'new-default')
        (self.directory / 'video/download.m3u8').unlink()
        with patch('os.walk', side_effect=AssertionError('must not scan')):
            frame.OnRefresh(None)
        item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        with patch.object(M3U8Downloader, 'DownloadTSFile', return_value=True) as download:
            frame.OnTaskAction(item, 'start')
        self.assertEqual([call.args[0] for call in download.call_args_list],
                         [segment.source_url for segment in task.details.segments])
        self.assertEqual([Path(call.args[1]) for call in download.call_args_list],
                         [self.directory / task.name / segment.segment_path for segment in task.details.segments])
        self.assertEqual(frame.model.GetValue(item, 1), 'video')
        self.assertTrue(frame._CreateM3U8File(str(self.directory / 'video/download.m3u8')))

    def test_invalid_download_index_does_not_enqueue(self):
        self.create()
        frame = self.frame()
        with patch('wx.MessageBox') as message, patch.object(M3U8Downloader, 'DownloadTSFile') as download:
            self.assertEqual(frame._DownloadFiles(str(self.directory / 'video/download.m3u8'), [(99, None)]), 0)
        download.assert_not_called()
        message.assert_called_once()

    def test_delete_ui_removes_record_and_keeps_files(self):
        task = self.create()
        frame = self.frame()
        with patch('wx.RichMessageDialog') as dialog:
            dialog.return_value.ShowModal.return_value = wx.ID_YES
            dialog.return_value.IsCheckBoxChecked.return_value = False
            frame.OnDeleteTask(frame.model.fileTree.items[0])
        self.assertIsNone(self.repository.get(task.id))
        self.assertEqual(frame.model.fileTree.items, [])
        self.assertTrue((self.directory / 'video/download.m3u8').is_file())

    def test_refresh_failure_keeps_visible_tasks(self):
        self.create()
        frame = self.frame()
        previous = frame.model.fileTree
        with patch.object(self.service, 'load_tasks', side_effect=sqlite3.OperationalError('locked')), patch('wx.MessageBox'):
            frame.OnRefresh(None)
        self.assertIs(frame.model.fileTree, previous)

    def test_refresh_tracks_expansion_by_id_when_rows_move(self):
        first = self.create()
        second = self.service.create_m3u8(self.root / 'second', 'https://example.com/index.m3u8', CONTENT)
        frame = self.frame()
        frame.mcTree.Expand(frame.model.ObjectToItem(frame.model._BuildKey((1,))))
        self.repository.delete(first.id)
        frame.OnRefresh(None)
        self.assertEqual(frame.model.fileTree.items[0].task_id, second.id)
        self.assertTrue(frame.mcTree.IsExpanded(frame.model.ObjectToItem(frame.model._BuildKey((0,)))))

    def test_merge_uses_regenerated_playlist_and_recorded_directory(self):
        task = self.create()
        for segment in task.details.segments:
            path = task.save_dir / task.name / segment.segment_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'test segment')
            self.service.finish_segment(task.id, segment.sequence, str(path), True)
        (self.directory / 'video/download.m3u8').unlink()
        frame = self.frame()
        item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        with patch('src.core.task_runner.FFmpegConverter.ConvertTSFile') as convert:
            frame.OnTaskAction(item, 'merge')
        self.assertEqual(Path(convert.call_args.args[1]), self.directory / 'video.mp4')
        manifest = Path(convert.call_args.args[0]).read_text()
        for segment in task.details.segments:
            self.assertIn(str(self.directory / task.name / segment.segment_path), manifest)
        self.assertTrue((self.directory / 'video/download.m3u8').is_file())

    def test_both_dialogs_write_through_service(self):
        for dialog_class in (DownloadDialogMU, DownloadDialogTS):
            service = Mock()
            dialog = dialog_class(None, str(self.root), task_service=service)
            try:
                dialog.downPath.tcDown.SetValue('new-task')
                dialog.downEdit.tsList.SetValue(CONTENT)
                with patch.object(dialog, 'EndModal') as end:
                    dialog.OnDownBtnClicked(None)
                service.create_m3u8.assert_called_once()
                self.assertEqual(Path(service.create_m3u8.call_args.args[0]), self.root)
                self.assertEqual(service.create_m3u8.call_args.kwargs['filename'], 'new-task.mp4')
                end.assert_called_once_with(wx.OK)
                service.create_m3u8.side_effect = sqlite3.OperationalError('disk full')
                with patch('wx.MessageBox'), patch.object(dialog, 'EndModal') as end:
                    dialog.OnDownBtnClicked(None)
                end.assert_not_called()
            finally:
                dialog.Destroy()
        self.app.ProcessPendingEvents()

    def test_failed_manifest_generation_does_not_launch_converter(self):
        task = self.create()
        for segment in task.details.segments:
            path = task.save_dir / task.name / segment.segment_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'data')
            self.service.finish_segment(task.id, segment.sequence, str(path), True)
        frame = self.frame()
        item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        with patch('src.core.task_runner.FFmpegConverter.ConcatPlaylist', return_value=False), \
             patch('src.core.task_runner.FFmpegConverter.ConvertTSFile') as convert, \
             patch('wx.MessageBox') as message:
            frame.OnTaskAction(item, 'merge')
        convert.assert_not_called()
        message.assert_called_once()
        self.assertEqual(self.repository.get(task.id).status.value, 'waiting_merge')

    def test_ts_duration_checkbox_preserves_input_and_requires_ffprobe(self):
        service = Mock()
        dialog = DownloadDialogTS(None, str(self.root), task_service=service)
        try:
            original = dialog.downEdit.tcPlay.GetValue()
            self.assertFalse(dialog.downEdit.detectDuration.GetValue())
            dialog.downEdit.detectDuration.SetValue(True)
            self.assertEqual(dialog.downEdit.tcPlay.GetValue(), original)
            dialog.downPath.tcDown.SetValue('new-task')
            dialog.downEdit.tsList.SetValue(CONTENT)
            with patch.object(SysSetting, 'GetFFprobe', return_value=None), \
                    patch('wx.MessageBox'), patch.object(dialog, 'EndModal') as end:
                dialog.OnDownBtnClicked(None)
                end.assert_not_called()
                service.create_m3u8.assert_not_called()
            with patch.object(SysSetting, 'GetFFprobe', return_value='ffprobe'), patch.object(dialog, 'EndModal'):
                dialog.OnDownBtnClicked(None)
            self.assertTrue(service.create_m3u8.call_args.kwargs['detect_duration'])
        finally:
            dialog.Destroy()
        self.app.ProcessPendingEvents()

    def test_missing_segment_url_does_not_enqueue_request(self):
        task = self.create()
        self.repository.mutate(task.id, lambda record: setattr(record.details.segments[0], 'source_url', None))
        frame = self.frame()
        with patch.object(M3U8Downloader, 'DownloadTSFile') as download:
            count = frame._DownloadFiles(str(self.directory / 'video/download.m3u8'), [(0, None)])
        self.assertEqual(count, 0)
        download.assert_not_called()
        saved = self.repository.get(task.id)
        self.assertEqual(saved.details.segments[0].last_error, '分片缺少下载地址。')


if __name__ == '__main__':
    unittest.main()
