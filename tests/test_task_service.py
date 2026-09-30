from src.models.tree_model import load_tree
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import wx

from src.media.m3u8.m3u8_downloader import M3U8Downloader
from src.core.sys_setting import SysSetting
from src.storage.task_repository import TaskRepository
from src.core.task_service import TaskService
from src.schemas.task import SourceType
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
        for patcher in (patch.object(SysSetting, '_values', config),
                        patch('src.models.tree_model.TaskService', return_value=self.service)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def create(self):
        return self.service.create_m3u8(self.directory, 'https://example.com/media/index.m3u8', CONTENT)

    def test_mp4_dialog_creates_record_and_returns_task_id(self):
        dialog = DownloadDialogMP4(None, str(self.root), self.service)
        try:
            dialog.url.SetValue('https://example.com/movie.mp4')
            dialog.downPath.tcDown.SetValue('direct-video')
            with patch.object(dialog, 'EndModal') as end:
                dialog.OnDownload(None)
                end.assert_called_once_with(wx.OK)
            task = self.repository.get(dialog.task_id)
            self.assertEqual(task.source_url, 'https://example.com/movie.mp4')
            self.assertEqual(task.save_dir, self.root / 'direct-video')
            self.assertFalse((task.save_dir / task.details.target_path).exists())
        finally:
            dialog.Destroy()
        self.app.ProcessPendingEvents()

    def frame(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test')
        self.addCleanup(self.app.ProcessPendingEvents)
        self.addCleanup(frame.Destroy)
        return frame

    def test_creation_persists_resolved_urls_and_local_playlist(self):
        task = self.create()
        self.assertEqual(task.source_type, SourceType.M3U8)
        self.assertEqual([segment.source_url for segment in task.details.segments], [
            'https://example.com/media/a.ts?token=1', 'https://example.com/media/a.ts?token=2'])
        self.assertEqual(len({segment.relative_path for segment in task.details.segments}), 2)
        self.assertFalse((self.directory / 'download.seed').exists())
        content = (self.directory / 'download.m3u8').read_text(encoding='utf-8')
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
        invalid = ['', '#EXTM3U\n', 'garbage',
                   '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXTINF:4,\na.ts\n']
        for content in invalid:
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.service.create_m3u8(self.directory, 'https://example.com/index.m3u8', content)
        with self.assertRaises(ValueError):
            self.service.create_m3u8(self.directory, '', CONTENT, source_type=SourceType.TS_PATTERN)
        self.assertFalse(self.directory.exists())
        self.directory.mkdir(parents=True)
        existing = self.directory / 'keep.txt'
        existing.write_text('keep')
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(existing.read_text(), 'keep')
        self.assertEqual(self.repository.list_tasks(), [])

    def test_database_failure_cleans_only_new_artifacts(self):
        with patch.object(self.repository, 'create', side_effect=sqlite3.OperationalError('disk full')):
            with self.assertRaises(sqlite3.OperationalError):
                self.create()
        self.assertFalse(self.directory.exists())
        self.assertEqual(self.repository.list_tasks(), [])

    def test_reopen_does_not_scan_or_import_unregistered_files(self):
        task = self.create()
        unrelated = self.directory / 'old.seed'
        unrelated.write_text(CONTENT)
        (self.directory / 'download.m3u8').unlink()
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

    def test_download_uses_recorded_paths_after_default_directory_change(self):
        task = self.create()
        frame = self.frame()
        SysSetting._values['download_dir'] = str(self.root / 'new-default')
        (self.directory / 'download.m3u8').unlink()
        with patch('os.walk', side_effect=AssertionError('must not scan')):
            frame.OnRefresh(None)
        item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        with patch.object(M3U8Downloader, 'DownloadTSFile', return_value=True) as download:
            frame.OnTaskAction(item, 'start')
        self.assertEqual([call.args[0] for call in download.call_args_list],
                         [segment.source_url for segment in task.details.segments])
        self.assertEqual([Path(call.args[1]) for call in download.call_args_list],
                         [self.directory / segment.relative_path for segment in task.details.segments])
        self.assertEqual(frame.model.GetValue(item, 1), 'video')
        self.assertTrue(frame._CreateM3U8File(str(self.directory / 'download.m3u8')))

    def test_invalid_download_index_does_not_enqueue(self):
        self.create()
        frame = self.frame()
        with patch('wx.MessageBox') as message, patch.object(M3U8Downloader, 'DownloadTSFile') as download:
            self.assertEqual(frame._DownloadFiles(str(self.directory / 'download.m3u8'), [(99, None)]), 0)
        download.assert_not_called()
        message.assert_called_once()

    def test_delete_ui_removes_record_and_keeps_files(self):
        task = self.create()
        frame = self.frame()
        with patch('wx.MessageDialog') as dialog:
            dialog.return_value.ShowModal.return_value = wx.ID_YES
            frame.OnDeleteTask(frame.model.fileTree.items[0])
        self.assertIsNone(self.repository.get(task.id))
        self.assertEqual(frame.model.fileTree.items, [])
        self.assertTrue((self.directory / 'download.m3u8').is_file())

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
            path = task.save_dir / segment.relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'test segment')
        (self.directory / 'download.m3u8').unlink()
        frame = self.frame()
        item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        with patch('src.views.main_frame.FFmpegConverter.ConvertTSFile') as convert:
            frame.OnTaskAction(item, 'merge')
        self.assertEqual(Path(convert.call_args.args[1]), self.directory / 'output.mp4')
        manifest = Path(convert.call_args.args[0]).read_text()
        for segment in task.details.segments:
            self.assertIn(str(self.directory / segment.relative_path), manifest)
        self.assertTrue((self.directory / 'download.m3u8').is_file())

    def test_both_dialogs_write_through_service(self):
        for dialog_class in (DownloadDialogMU, DownloadDialogTS):
            service = Mock()
            dialog = dialog_class(None, 'test', str(self.root), task_service=service)
            try:
                dialog.downPath.tcDown.SetValue('new-task')
                dialog.downEdit.tsList.SetValue(CONTENT)
                with patch.object(dialog, 'EndModal') as end:
                    dialog.OnDownBtnClicked(None)
                service.create_m3u8.assert_called_once()
                self.assertEqual(Path(service.create_m3u8.call_args.args[0]), self.root / 'new-task')
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
            path = task.save_dir / segment.relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'data')
        frame = self.frame()
        item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
        with patch('src.views.main_frame.FFmpegConverter.ConcatPlaylist', return_value=False), \
             patch('src.views.main_frame.FFmpegConverter.ConvertTSFile') as convert, \
             patch('wx.MessageBox') as message:
            frame.OnTaskAction(item, 'merge')
        convert.assert_not_called()
        message.assert_called_once()
        self.assertEqual(self.repository.get(task.id).status.value, 'waiting_merge')

    def test_ts_duration_checkbox_preserves_input_and_requires_ffprobe(self):
        service = Mock()
        dialog = DownloadDialogTS(None, 'test', str(self.root), task_service=service)
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
            count = frame._DownloadFiles(str(self.directory / 'download.m3u8'), [(0, None)])
        self.assertEqual(count, 0)
        download.assert_not_called()
        saved = self.repository.get(task.id)
        self.assertEqual(saved.details.segments[0].last_error, '分片缺少下载地址。')


if __name__ == '__main__':
    unittest.main()
