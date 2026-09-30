from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import wx
from src.media.m3u8.m3u8_downloader import M3U8Downloader
from src.core.task_service import TaskService
from src.schemas.task import M3U8Details
from src.storage.task_repository import TaskRepository
from src.views.dialogs.panels.ts_form import DownloadEditTS
from src.views.dialogs.ts_dialog import DownloadDialogTS


class TSOptionsTests(unittest.TestCase):
    def test_padded_and_plain_segment_names(self):
        format_name = DownloadEditTS.FormatSegmentName
        self.assertEqual(format_name('seg-{idx}.ts', 2), 'seg-2.ts')
        self.assertEqual(format_name('seg-{idx:03d}.ts', 2), 'seg-002.ts')
        self.assertEqual(format_name('seg-{idx:03d}.ts', 1234), 'seg-1234.ts')
        self.assertEqual(format_name('{idx}/{idx:04d}.ts', 9), '9/0009.ts')
        for rule in ('a.ts', '{other}.ts', '{idx.foo}.ts', '{idx:99999999d}.ts', '{idx:03d}{bad}'):
            with self.assertRaises(ValueError):
                format_name(rule, 1)

    def test_headers_persist_without_table_migration(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            repository = TaskRepository(root / 'downloads.db')
            headers = {'Referer': 'https://example.com/watch', 'Cookie': 'session=test; a=b'}
            task = TaskService(repository).create_m3u8(root / 'video', 'https://example.com/index.m3u8',
                '#EXTM3U\n#EXTINF:5,\na.ts\n', request_headers=headers)
            restored = TaskRepository(repository.path).get(task.id)
            self.assertEqual(restored.details.request_headers, headers)
        self.assertEqual(M3U8Details().request_headers, {})

    def test_invalid_headers_rejected(self):
        for headers in ({'Cookie': 'a=b\r\nInjected: x'}, {'Host': 'bad'}, {'Referer': '中文'}):
            with self.assertRaises(ValueError):
                M3U8Details(request_headers=headers)

    def test_retry_keeps_headers_and_other_task_has_none(self):
        response = Mock(status_code=503)
        success = Mock(status_code=200, content=b'media')
        headers = {'Referer': 'https://example.com/watch', 'Cookie': 'session=test'}
        with patch.object(M3U8Downloader, '_WaitForRequest', return_value=True), \
                patch.object(M3U8Downloader, '_shutdown') as shutdown, \
                patch('src.media.m3u8.m3u8_downloader.SysSetting.GetAll', return_value={'max_retries': 1, 'connect_timeout': 10, 'read_timeout': 30}), \
                patch('src.media.m3u8.m3u8_downloader.requests.get', side_effect=[response, success, success]) as get:
            shutdown.wait.return_value = False
            self.assertEqual(M3U8Downloader.DownloadContent('https://example.com/a.ts', headers=headers), (True, b'media'))
            M3U8Downloader.DownloadContent('https://example.com/b.ts')
            self.assertEqual(get.call_args_list[0].kwargs['headers'], headers)
            self.assertEqual(get.call_args_list[1].kwargs['headers'], headers)
            self.assertNotIn('headers', get.call_args_list[2].kwargs)

    def test_queue_copies_task_headers(self):
        queue = Queue()
        with patch.object(M3U8Downloader, 'threadQueue', queue), patch.object(M3U8Downloader, 'isStop', False), \
                patch.object(M3U8Downloader, '_pending', set()), patch.object(M3U8Downloader, '_shutdown') as shutdown:
            shutdown.is_set.return_value = False
            headers = {'Cookie': 'a=b'}
            self.assertTrue(M3U8Downloader.DownloadTSFile('https://example.com/a.ts', 'a.ts', Mock(), None, headers))
            headers['Cookie'] = 'changed'
            self.assertEqual(queue.get_nowait()[5], {'Cookie': 'a=b'})

    def test_form_generation_and_collapsed_options(self):
        app = wx.GetApp() or wx.App(False)
        service = Mock()
        dialog = DownloadDialogTS(None, 'test', str(Path.cwd()), task_service=service)
        try:
            form = dialog.downEdit
            self.assertTrue(form.advanced.IsCollapsed())
            form.tcReg.SetValue('seg-{idx:03d}.ts')
            form.tcStart.SetValue('1')
            form.tcEnd.SetValue('2')
            form.OnBtnAppendClicked(SimpleNamespace(Skip=lambda: None))
            self.assertIn('seg-001.ts', form.GetContent())
            self.assertIn('seg-002.ts', form.GetContent())
            form.tcCookie.SetValue('session=test')
            form.tcReferer.SetValue('https://example.com/watch')
            form.advanced.Expand()
            form.OnAdvancedChanged(None)
            dialog.Layout()
            self.assertGreater(form.tsList.GetSize().height, 100)
            self.assertGreater(form.tcCookie.GetSize().width, 100)
            dialog.downPath.tcDown.SetValue('option-test')
            with patch.object(dialog, 'EndModal'), patch('wx.MessageBox') as message:
                dialog.OnDownBtnClicked(None)
                message.assert_not_called()
            self.assertEqual(service.create_m3u8.call_args.kwargs['request_headers'],
                             {'Referer': 'https://example.com/watch', 'Cookie': 'session=test'})
        finally:
            dialog.Destroy()
            app.ProcessPendingEvents()


if __name__ == '__main__':
    unittest.main()
