from queue import Queue
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

import wx

from src.core.downloader import Downloader
from src.views.dialogs.m3u8_dialog import DownloadDialogMU


class PlaylistFetchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.GetApp() or wx.App(False)

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dialog = DownloadDialogMU(None, 'test', temporary.name, task_service=Mock())
        self.addCleanup(self.app.ProcessPendingEvents)
        self.addCleanup(lambda: self.dialog.Destroy() if self.dialog else None)
        self.panel = self.dialog.downEdit
        self.panel.tcURI.SetValue('https://example.com/media/index.m3u8')

    def test_request_runs_off_ui_thread_and_notifies_path_only_after_success(self):
        queued, started, release = Queue(), Event(), Event()
        on_main_thread = []
        def download(url):
            on_main_thread.append(wx.IsMainThread())
            started.set()
            release.wait(3)
            return True, b'#EXTM3U\n#EXTINF:4,\na.ts\n'
        with patch.object(Downloader, 'DownloadContent', side_effect=download) as request, \
             patch('wx.CallAfter', side_effect=lambda *args: queued.put(args)):
            try:
                self.panel.OnBtnM3U8Clicked(Mock())
                self.assertTrue(started.wait(2))
                self.assertFalse(self.panel.fetchButton.IsEnabled())
                self.assertEqual(self.dialog.downPath.tcDown.GetValue(), '')
                self.panel.OnBtnM3U8Clicked(Mock())
                request.assert_called_once()
            finally:
                release.set()
            callback, *args = queued.get(timeout=3)
        callback(*args)
        self.app.ProcessPendingEvents()
        self.assertEqual(on_main_thread, [False])
        self.assertTrue(self.panel.fetchButton.IsEnabled())
        self.assertTrue(self.panel.tsList.GetValue().startswith('#EXTM3U'))
        self.assertEqual(self.dialog.downPath.tcDown.GetValue(), 'media')

    def test_error_and_changed_url_leave_existing_content_intact(self):
        self.panel.tsList.SetValue('existing')
        address = self.panel.GetBaseURI()
        with patch('wx.MessageBox') as message:
            self.panel._OnFetched(address, False, 'timeout')
            message.assert_called_once()
        self.panel.tcURI.SetValue('https://example.com/other.m3u8')
        with patch('wx.MessageBox') as message, patch('wx.PostEvent') as post:
            self.panel._OnFetched(address, True, 'outdated')
            self.panel._OnFetched(address, False, 'outdated error')
            message.assert_not_called()
            post.assert_not_called()
        self.assertEqual(self.panel.tsList.GetValue(), 'existing')
        self.assertTrue(self.panel.fetchButton.IsEnabled())

    def test_late_result_after_close_does_not_touch_controls(self):
        address = self.panel.GetBaseURI()
        self.dialog.Destroy()
        self.app.Yield()  # wx 在空闲处理阶段真正销毁顶层窗口及其子控件。
        with patch('wx.MessageBox') as message, patch('wx.PostEvent') as post:
            self.panel._OnFetched(address, True, 'late result')
            message.assert_not_called()
            post.assert_not_called()

    def test_line_numbers_resize_without_entering_selected_text(self):
        editor = self.panel.tsList
        content = '#EXTM3U\n#EXTINF:4,\n片段.ts'
        editor.SetValue(content)
        self.app.ProcessPendingEvents()
        self.assertEqual(editor.GetLineCount(), 3)
        editor.SelectAll()
        self.assertEqual(editor.GetSelectedText(), content)
        self.assertFalse(editor.GetReadOnly())
        editor.ReplaceSelection('edited')
        self.assertEqual(editor.GetValue(), 'edited')
        self.app.ProcessPendingEvents()
        initial_width = editor.GetMarginWidth(0)
        editor.SetValue('\n'.join('segment.ts' for _ in range(1000)))
        self.app.ProcessPendingEvents()
        self.assertEqual(editor.GetLineCount(), 1000)
        self.assertGreater(editor.GetMarginWidth(0), initial_width)
