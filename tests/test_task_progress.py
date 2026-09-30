import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import wx

from src.managers.downloader import Downloader
from src.managers.converter import Converter
from src.managers.path_manager import PathManager
from src.models.tree_model import MultiColumnTreeModel
from src.schemas.file_base import FileItem, TreeItem, TreeData
from src.views.main_frame import MainFrame


class TaskProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.GetApp() or wx.App(False)

    def setUp(self):
        self.task = TreeItem(parent=FileItem(fileName='task/download.m3u8'), childs=[
            FileItem(fileName='task/a.ts', fileSize=10), FileItem(fileName='task/b.ts')], download=1)
        self.tree = TreeData(items=[self.task])
        for patcher in (patch.object(Downloader, '_pending', set()),
                        patch.object(Downloader, '_requesting', set()),
                        patch.object(Downloader, '_failed', set()),
            patch.object(Downloader, '_paused_files', set()),
                        patch.object(Downloader, '_user_paused', threading.Event()),
                        patch.object(Converter, '_outputs', set()),
                        patch('src.models.tree_model.FileManager.GetFileInfos', return_value=self.tree)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.model = MultiColumnTreeModel()
        self.addCleanup(self.model.DecRef)
        self.key = Downloader.FileKey(PathManager.GetAbsPath('task/b.ts'))

    def test_progress_and_pause_transitions(self):
        self.assertEqual(self.model.TaskInfo(0)['progress'], '50% · 1/2')
        self.assertEqual(self.model.TaskInfo(0)['status'], '待继续')
        Downloader._pending.add(self.key)
        self.assertEqual(self.model.TaskInfo(0)['status'], '等待下载')
        Downloader._requesting.add(self.key)
        self.assertEqual(self.model.TaskInfo(0)['status'], '下载中')
        Downloader.Pause()
        self.assertEqual(self.model.TaskInfo(0)['status'], '暂停中')
        Downloader._requesting.clear()
        self.assertEqual(self.model.TaskInfo(0)['status'], '已暂停')
        Downloader.Resume()
        Downloader._pending.clear()
        Downloader._failed.add(self.key)
        self.assertEqual(self.model.TaskInfo(0)['status'], '下载失败 · 1 个失败')

    def test_completion_is_idempotent_and_accounts_for_mp4_rows(self):
        self.task.outputs.append(FileItem(fileName='task/output.mp4', fileSize=20))
        item = self.model.ObjectToItem(self.model._BuildKey((0, 2)))
        with patch.object(self.model, '_SendEvent') as event:
            for _ in range(2):
                self.model.SetValue(FileItem(fileName='task/b.ts', fileSize=10), item, 0)
            self.assertEqual(self.task.download, 2)
            event.assert_called_once()
        self.assertEqual(self.model.TaskInfo(0)['progress'], '100% · 2/2')
        output = self.model.ObjectToItem(self.model._BuildKey((0, 0)))
        self.assertEqual(self.model.GetValue(output, 4), '')

    def test_merge_states(self):
        self.task.childs[1].fileSize = '10 B'
        self.assertEqual(self.model.TaskInfo(0)['status'], '待合并')
        output = os.path.join(os.path.dirname(PathManager.GetAbsPath(self.task.parent.fileName)), 'output.mp4')
        Converter._outputs.add(os.path.abspath(output))
        self.assertEqual(self.model.TaskInfo(0)['status'], '合并中')
        Converter._outputs.clear()
        self.model.merge_failed.add(self.task.parent.fileName)
        self.assertEqual(self.model.TaskInfo(0)['status'], '合并失败')
        self.model.merge_failed.clear()
        self.task.outputs.append(FileItem(fileName='task/output.mp4', fileSize=20))
        self.assertEqual(self.model.TaskInfo(0)['status'], '已完成')

    def test_row_pause_and_resume_only_affect_its_pending_files(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test')
        try:
            other = Downloader.FileKey(PathManager.GetAbsPath('other/a.ts'))
            Downloader._pending.update({self.key, other})
            Downloader._requesting.add(self.key)
            item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
            self.assertEqual(frame.model.TaskActions(0)[0]['label'], '暂停')
            frame.OnTaskAction(item, 'start')
            self.assertEqual(Downloader.Snapshot()['paused_files'], {self.key})
            self.assertEqual(frame.model.TaskInfo(0)['status'], '暂停中')
            Downloader._requesting.clear()
            self.assertEqual(frame.model.TaskInfo(0)['status'], '已暂停')
            self.assertEqual(frame.model.TaskActions(0)[0]['label'], '继续')
            with patch.object(frame, '_DownloadFiles') as enqueue:
                frame.OnTaskAction(item, 'start')
                enqueue.assert_not_called()
            self.assertFalse(Downloader.Snapshot()['paused_files'])
            self.assertEqual(frame.model.TaskActions(0)[0]['label'], '暂停')
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_callback_uses_file_path_after_task_reorder(self):
        self.tree.items.insert(0, TreeItem(parent=FileItem(fileName='other/download.m3u8'),
                                        childs=[FileItem(fileName='other/a.ts')]))
        receiver = SimpleNamespace(model=self.model)
        updated = FileItem(fileName='task/b.ts', fileSize=10)
        with patch('src.views.main_frame.FileManager.GetFileItem', return_value=(True, updated)):
            MainFrame._DownloadCall(receiver, True, PathManager.GetAbsPath('task/b.ts'), None)
        self.assertEqual(self.tree.items[0].download, 0)
        self.assertEqual(self.tree.items[1].download, 2)

    def test_fixed_row_actions_and_retry_scope(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test')
        try:
            item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
            self.assertEqual(frame.model.GetValue(item, 5), '50% · 1/2')
            renderer = frame.mcTree.GetColumn(6).GetRenderer()
            size = frame.FromDIP(wx.Size(200, 24))
            cell = wx.Rect(0, 0, size.width, size.height)

            def click(index):
                rect = renderer._ActionRects(cell)[index]
                mouse = SimpleNamespace(GetPosition=lambda: wx.Point(rect.x + rect.width // 2,
                                                                     rect.y + rect.height // 2))
                return renderer.ActivateCell(cell, frame.model, item, 4, mouse)

            actions = frame.model.TaskActions(0)
            self.assertFalse(frame.mcTree.IsExpanded(item))
            self.assertEqual([a['label'] for a in actions], ['继续', '重试', '删除', '展开', '更多'])
            self.assertEqual([a['enabled'] for a in actions], [True, False, True, True, True])
            self.assertFalse(click(1))
            Downloader._failed.add(self.key)
            with patch.object(frame, '_DownloadFiles', return_value=1) as download:
                self.assertTrue(click(1))
                self.assertEqual([i for i, _ in download.call_args.args[1]], [1])
            with patch.object(frame, 'OnDeleteTask') as delete:
                self.assertTrue(click(2))
                delete.assert_called_once_with(self.task)
            with patch.object(frame, 'OnTaskMenu') as menu:
                self.assertTrue(click(4))
                menu.assert_called_once_with(item)
            # 同一点击区域切换展开状态，箭头操作后也重新读取正确文字。
            self.assertTrue(click(3))
            self.assertTrue(frame.mcTree.IsExpanded(item))
            self.assertEqual(frame.model.TaskActions(0)[3]['label'], '折叠')
            self.assertTrue(click(3))
            self.assertFalse(frame.mcTree.IsExpanded(item))
            self.assertEqual(frame.model.TaskActions(0)[3]['label'], '展开')
            frame.mcTree.Expand(item)
            self.assertEqual(frame.model.TaskActions(0)[3]['label'], '折叠')
            bitmap = wx.Bitmap(size.width, size.height)
            dc = wx.MemoryDC(bitmap)
            renderer.SetValue(frame.model.GetValue(item, 4))
            self.assertTrue(renderer.Render(cell, dc, 0))
            dc.SelectObject(wx.NullBitmap)
            Downloader._pending.add(self.key)
            Downloader.Pause()
            self.assertEqual([a['enabled'] for a in frame.model.TaskActions(0)], [True, False, False, True, True])
            for index in (1, 2):
                self.assertFalse(click(index))
            Downloader._pending.clear()
            self.task.childs[1].fileSize = '10 B'
            with patch.object(frame, '_CreateMP4File') as merge:
                frame.OnTaskAction(item, 'merge')
                merge.assert_called_once_with(self.task.parent.fileName, item)
            with patch.object(frame, 'OnTaskMenu') as menu, patch.object(frame, 'OnDeleteTask') as delete:
                renderer.ActivateCell(cell, frame.model, item, 4, None)
                menu.assert_called_once_with(item)
                delete.assert_not_called()
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_more_menu_and_delete_cancel(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test')
        try:
            item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
            self.assertEqual(frame.mcTree.GetColumnCount(), 7)

            def inspect_menu(menu):
                entries = {entry.GetItemLabelText(): entry for entry in menu.GetMenuItems()
                           if not entry.IsSeparator()}
                self.assertEqual(set(entries), {'打开文件夹', '转 MP4', '播放视频'})
                self.assertTrue(entries['打开文件夹'].IsEnabled())
                self.assertFalse(entries['播放视频'].IsEnabled())
                self.assertFalse(entries['转 MP4'].IsEnabled())


            Downloader._pending.add(self.key)
            with patch.object(frame.mcTree, 'PopupMenu', side_effect=inspect_menu):
                frame.OnTaskMenu(item)
                child = frame.model.ObjectToItem(frame.model._BuildKey((0, 0)))
                frame.OnTaskMenu(child)
            with patch('src.views.main_frame.FileManager.TaskDeletionDirectory', return_value='task'), \
                 patch('src.views.main_frame.FileManager.DeleteTaskDirectory') as delete, \
                 patch('src.views.main_frame.wx.MessageDialog') as dialog:
                dialog.return_value.ShowModal.return_value = wx.ID_NO
                frame.OnDeleteTask(self.task)
                delete.assert_not_called()
                dialog.return_value.Destroy.assert_called_once()
                self.assertTrue(dialog.call_args.args[3] & wx.NO_DEFAULT)
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_search_scans_paths_and_reveals_collapsed_segment(self):
        self.task.outputs.append(FileItem(fileName='task/movie.mp4'))
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test')
        try:
            parent = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
            frame.mcTree.Collapse(parent)
            # 搜索不能再遍历视图或计算任务状态；大量分片时这些调用很昂贵。
            with patch.object(frame.model, 'GetFirstChild', side_effect=AssertionError), \
                 patch.object(frame.model, 'GetValue', side_effect=AssertionError), \
                 patch.object(frame.model, 'TaskInfo', side_effect=AssertionError):
                self.assertFalse(frame._FindItem('不存在').IsOk())
                self.assertFalse(frame._FindItem('更多').IsOk())
                for text, key in [('download', '0'), ('movie.mp4', '0.0'), ('b.ts', '0.2')]:
                    self.assertEqual(frame.model.ItemToObject(frame._FindItem(text)), key)
            frame._SearchItems(' B.TS ')
            self.assertTrue(frame.mcTree.IsExpanded(parent))
            self.assertEqual(frame.model.ItemToObject(frame.mcTree.GetSelection()), '0.2')
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_search_debounces_input_and_button_searches_immediately(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test')
        try:
            with patch.object(frame, '_SearchItems') as search, \
                 patch.object(frame._searchTimer, 'StartOnce') as start, \
                 patch.object(frame._searchTimer, 'Stop') as stop:
                for text in ('b', 'b.ts'):
                    frame.OnSearchText(SimpleNamespace(GetString=lambda text=text: text))
                search.assert_not_called()
                self.assertEqual(start.call_count, 2)
                start.assert_called_with(200)
                frame.OnSearchTimer(None)
                search.assert_called_once_with('b.ts')
                frame.OnSearchText(SimpleNamespace(GetString=lambda: ''))
                self.assertEqual(start.call_count, 2)
                control = SimpleNamespace(GetValue=lambda: 'movie.mp4')
                frame.OnSearch(SimpleNamespace(GetEventObject=lambda: control))
                search.assert_called_with('movie.mp4')
                self.assertEqual(stop.call_count, 4)
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_columns_fit_available_width_when_resizing(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test')
        try:
            layouts = {}
            for width in (780, 1024, 1400, 850, 1024):
                frame.mcTree.SetSize(frame.FromDIP(wx.Size(width, 400)))
                frame._FitTaskColumns()
                columns = [frame.mcTree.GetColumn(i).GetWidth() for i in range(7)]
                self.assertLessEqual(sum(columns), frame.mcTree.GetClientSize().width)
                self.assertGreaterEqual(columns[6], frame.FromDIP(200))
                self.assertGreater(columns[1], 0)
                if width in layouts:
                    self.assertEqual(columns, layouts[width])
                layouts[width] = columns
            for normal, expanded in zip(layouts[1024], layouts[1400]):
                self.assertGreater(expanded, normal)
            with patch.object(frame.mcTree, 'GetColumn') as get_column:
                frame._FitTaskColumns()
                frame._FitTaskColumns()
                get_column.assert_not_called()
            with patch.object(frame, '_FitTaskColumns') as fit:
                frame.OnTaskProgress(None)
                frame.OnTaskProgress(None)
                fit.assert_not_called()
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_initial_columns_and_progress_are_ready_before_show(self):
        shown = []
        def inspect_before_show(frame):
            shown.append(frame)
            columns = [frame.mcTree.GetColumn(i).GetWidth() for i in range(7)]
            self.assertLessEqual(sum(columns), frame.mcTree.GetClientSize().width)
            self.assertGreater(columns[6], 0)
            self.assertIn(self.task.parent.fileName, frame._task_display)
            # 初次定时刷新不应把已经显示的相同状态再次通知整行重绘。
            with patch.object(frame.model, 'ItemChanged') as changed:
                frame.OnTaskProgress(None)
                changed.assert_not_called()

        with patch.object(MainFrame, 'Show', new=inspect_before_show):
            frame = MainFrame(None, 'test')
        try:
            self.assertEqual(shown, [frame])
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()


if __name__ == '__main__':
    unittest.main()
