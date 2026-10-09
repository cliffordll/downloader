"""启动入口负责建库与恢复；构造窗口/模型不再隐式打开另一份任务库。"""
from contextlib import ExitStack
from pathlib import Path
from queue import SimpleQueue
import runpy
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import wx

from src.core.task_service import TaskService
from src.media.m3u8.m3u8_downloader import M3U8Downloader
from src.models.tree_model import load_tree
from src.schemas.task import TaskStatus
from src.storage.task_repository import TaskRepository
from src.views.main_frame import MainFrame


class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.GetApp() or wx.App(False)

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / 'downloads.db'

    def run_entry(self, stack):
        stack.enter_context(patch('src.storage.task_repository.database_path', return_value=self.path))
        if sys.platform == 'win32':
            stack.enter_context(patch('ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID', return_value=0))
        app = stack.enter_context(patch('wx.App'))
        frame = stack.enter_context(patch('src.views.main_frame.MainFrame'))
        message = stack.enter_context(patch('wx.MessageBox'))
        dock = stack.enter_context(patch('src.views.components.icons.create_dock_icon'))
        stack.enter_context(patch.object(M3U8Downloader, 'Shutdown'))
        return app, frame, message, dock

    def test_entry_restores_tasks_once_before_creating_window(self):
        service = TaskService(TaskRepository(self.path))
        task = service.create_mp4(self.root / 'video', 'https://example.com/video.mp4')
        service.runtime_status(task.id, TaskStatus.DOWNLOADING)
        original_load = TaskService.load_tasks
        reads = []

        def load(service):
            reads.append(service)
            return original_load(service)

        with ExitStack() as stack:
            app, frame, message, dock = self.run_entry(stack)
            stack.enter_context(patch.object(TaskService, 'load_tasks', load))
            repository = stack.enter_context(patch('src.storage.task_repository.TaskRepository', wraps=TaskRepository))
            runpy.run_path(str(Path(__file__).resolve().parents[1] / 'main.py'), run_name='__main__')
            repository.assert_called_once_with()
            frame.assert_called_once()
            injected_service, tree = frame.call_args.args[2:]
            self.assertEqual(reads, [injected_service])
            self.assertEqual(injected_service.repository.path, self.path)
            self.assertEqual(tree.items[0].task_id, task.id)
            self.assertEqual(tree.items[0].task_status, TaskStatus.INTERRUPTED)
            app.return_value.MainLoop.assert_called_once()
            dock.assert_called_once_with()
            dock.return_value.Destroy.assert_called_once_with()
            message.assert_not_called()

    def test_database_failure_stops_startup_before_window_creation(self):
        for target in ('src.storage.task_repository.TaskRepository', 'src.core.task_service.TaskService.load_tasks'):
            with self.subTest(target=target), ExitStack() as stack:
                app, frame, message, dock = self.run_entry(stack)
                stack.enter_context(patch(target, side_effect=sqlite3.OperationalError('database unavailable')))
                with self.assertRaises(SystemExit) as stopped:
                    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'main.py'), run_name='__main__')
                self.assertEqual(stopped.exception.code, 1)
                frame.assert_not_called()
                app.return_value.MainLoop.assert_not_called()
                message.assert_called_once()
                self.assertIn('database unavailable', message.call_args.args[0])
                dock.return_value.Destroy.assert_called_once_with()

    def test_window_uses_preloaded_tree_and_shared_service_without_database_access(self):
        service = TaskService(TaskRepository(self.path))
        tree = load_tree(service)
        with patch.object(MainFrame, 'Show'), patch.object(M3U8Downloader, '_changes', SimpleQueue()), \
             patch.object(service, 'load_tasks', side_effect=AssertionError('unexpected second load')), \
             patch('sqlite3.connect', side_effect=AssertionError('unexpected database access')):
            frame = MainFrame(None, 'test', service, tree)
            try:
                self.assertIs(frame.tasks, service)
                self.assertIs(frame.runner.mp4.repository, service.repository)
                self.assertIs(frame.model.fileTree, tree)
                self.assertFalse(hasattr(frame.model, 'tasks'))
            finally:
                frame.Destroy()
                self.app.ProcessPendingEvents()


if __name__ == '__main__':
    unittest.main()
