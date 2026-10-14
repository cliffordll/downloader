from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from queue import Queue

import wx

from src.views.dialogs.m3u8_dialog import DownloadDialogMU
from src.views.dialogs.mp4_dialog import DownloadDialogMP4
from src.core.task_service import TaskService
from src.storage.task_repository import TaskRepository
from src.views.main_frame import MainFrame


class BrowserCaptureUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.GetApp() or wx.App(False)

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.service = TaskService(TaskRepository(self.root / 'tasks.db'))

    def test_prefilled_forms_create_tasks_only_on_confirmation_and_persist_referer(self):
        for kind in ('m3u8', 'mp4'):
            with self.subTest(kind=kind):
                capture = {'type': kind, 'url': f'https://cdn.example/video.{kind}?sign=a%2Fb',
                           'referer': 'https://example.com/watch'}
                if kind == 'm3u8':
                    dialog = DownloadDialogMU(None, 'test', str(self.root), self.service, capture=capture)
                    dialog.downEdit.tsList.SetValue('#EXTM3U\n#EXTINF:4,\na.ts\n')
                else:
                    dialog = DownloadDialogMP4(None, str(self.root), self.service, capture=capture)
                try:
                    before = len(self.service.repository.list_tasks())
                    self.assertEqual(dialog.downEdit.GetBaseURI(), capture['url'])
                    self.assertTrue(dialog.downPath.GetFileName())
                    with patch.object(dialog, 'EndModal') as confirm:
                        dialog.OnDownBtnClicked(None)
                        confirm.assert_called_once_with(wx.OK)
                    records = self.service.repository.list_tasks()
                    self.assertEqual(len(records), before + 1)
                    record = next(t for t in records if t.source_url == capture['url'])
                    self.assertEqual(record.details.request_headers, {'Referer': capture['referer']})
                finally:
                    dialog.Destroy()
                    self.app.ProcessPendingEvents()

    def test_modal_dialog_defers_inbox_and_only_schedules_one_confirmation(self):
        inbox = Queue()
        capture = {'type': 'mp4', 'url': 'https://cdn.example/video.mp4', 'referer': ''}
        inbox.put(capture)
        inbox.put(capture)
        frame = SimpleNamespace(browser_bridge=SimpleNamespace(inbox=inbox),
                                _capture_dialog_open=False, _OpenBrowserCapture=Mock())
        modal = Mock(spec=wx.Dialog)
        modal.IsModal.return_value = True
        with patch('wx.GetTopLevelWindows', return_value=[modal]), patch('wx.CallAfter') as dispatch:
            MainFrame._CheckBrowserInbox(frame)
            dispatch.assert_not_called()
            self.assertEqual(inbox.qsize(), 2)
        with patch('wx.GetTopLevelWindows', return_value=[]), patch('wx.CallAfter') as dispatch:
            MainFrame._CheckBrowserInbox(frame)
            MainFrame._CheckBrowserInbox(frame)
            dispatch.assert_called_once_with(frame._OpenBrowserCapture, capture)
            self.assertEqual(inbox.qsize(), 1)

    def test_startup_restores_connection_only_after_first_enable(self):
        config = self.root / 'browser-pairing.json'
        frame = SimpleNamespace(browser_bridge=None, statusBar=Mock())
        with patch('src.views.main_frame.pairing_path', return_value=config), \
             patch('src.views.main_frame.BrowserBridge') as bridge:
            MainFrame.RestoreBrowserConnection(frame)
            bridge.assert_not_called()
            config.write_text('{}')
            MainFrame.RestoreBrowserConnection(frame)
            bridge.assert_called_once_with()
            self.assertIs(frame.browser_bridge, bridge.return_value)
            MainFrame.RestoreBrowserConnection(frame)
            bridge.assert_called_once_with()

    def test_connection_restore_failure_keeps_app_usable(self):
        config = self.root / 'browser-pairing.json'
        config.write_text('{}')
        frame = SimpleNamespace(browser_bridge=None, statusBar=Mock())
        with patch('src.views.main_frame.pairing_path', return_value=config), \
             patch('src.views.main_frame.BrowserBridge', side_effect=OSError('port occupied')):
            MainFrame.RestoreBrowserConnection(frame)
        self.assertIsNone(frame.browser_bridge)
        frame.statusBar.SetStatusText.assert_called_once()
