import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import wx

from src.managers.downloader import Downloader
from src.managers.converter import Converter
from src.managers.path_manager import PathManager
from src.managers.sys_setting import SysSetting
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
                        patch.object(SysSetting, '_values', dict(SysSetting.GetAll(), default_expand_tasks=False)),
                        patch.object(Downloader, '_requesting', set()),
                        patch.object(Downloader, '_failed', set()),
                        patch.object(Downloader, '_errors', {}),
            patch.object(Downloader, '_paused_files', set()),
                        patch.object(Downloader, '_user_paused', threading.Event()),
                        patch.object(Converter, '_outputs', set()),
                        patch('src.models.tree_model.TaskService.load_tree', return_value=self.tree)):
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

    def test_callback_without_stable_identity_does_not_change_rows(self):
        self.tree.items.insert(0, TreeItem(parent=FileItem(fileName='other/download.m3u8'),
                                        childs=[FileItem(fileName='other/a.ts')]))
        receiver = SimpleNamespace(model=self.model)
        updated = FileItem(fileName='task/b.ts', fileSize=10)
        with patch('src.views.main_frame.FileManager.GetFileItem', return_value=(True, updated)):
            MainFrame._DownloadCall(receiver, True, PathManager.GetAbsPath('task/b.ts'), None)
        self.assertEqual(self.tree.items[0].download, 0)
        self.assertEqual(self.tree.items[1].download, 1)

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
            Downloader._pending.clear()
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

    def test_filter_matches_paths_and_keeps_original_task_indices(self):
        self.task.outputs.append(FileItem(fileName='task/movie.mp4'))
        other = TreeItem(parent=FileItem(fileName='other/download.m3u8'),
                         childs=[FileItem(fileName='other/c.ts')])
        self.tree.items.insert(0, other)
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test')
        try:
            for text, expected in [('download', {0, 1}), ('MOVIE.MP4', {1}),
                                   (' B.TS ', {1}), ('other/', {0}), ('不存在', set()), ('更多', set())]:
                frame._SearchItems(text)
                self.assertEqual(frame.model.visible_tasks, expected)
            frame._SearchItems('b.ts')
            roots = []
            self.assertEqual(frame.model.GetChildren(wx.dataview.NullDataViewItem, roots), 1)
            self.assertEqual(frame.model.ItemToObject(roots[0]), '1')
            with patch.object(frame, 'OnDeleteTask') as delete:
                frame.OnTaskAction(roots[0], 'delete')
                delete.assert_called_once_with(self.task)
            # 筛选隐藏的任务下载完成时，仍更新原始数据，不丢失任务。
            updated = FileItem(fileName='other/c.ts', fileSize=10)
            with patch.object(frame.model, '_SendEvent'):
                frame.model.SetValue(updated, frame.model.ObjectToItem(frame.model._BuildKey((0, 0))), 0)
            self.assertEqual(other.download, 1)
            frame._SearchItems('')
            self.assertIsNone(frame.model.visible_tasks)
            self.assertEqual(len(frame.model.fileTree.items), 2)
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_status_filter_tracks_progress_and_refresh_keeps_conditions(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test')
        try:
            frame.statusFilter.SetStringSelection('待继续')
            frame.OnStatusFilter(None)
            self.assertEqual(frame.model.visible_tasks, {0})
            Downloader._pending.add(self.key)
            Downloader._requesting.add(self.key)
            frame.OnTaskProgress(None)
            self.assertEqual(frame.model.visible_tasks, set())
            frame.statusFilter.SetStringSelection('下载中')
            frame.OnStatusFilter(None)
            self.assertEqual(frame.model.visible_tasks, {0})
            frame.searchCtrl.ChangeValue('missing')
            frame.OnStatusFilter(None)
            frame.OnRefresh(None)
            self.assertEqual(frame.model.visible_tasks, set())
            frame.OnClearSearch(None)
            self.assertEqual(frame.model.visible_tasks, {0})
            frame.statusFilter.SetSelection(0)
            frame.OnStatusFilter(None)
            self.assertIsNone(frame.model.visible_tasks)
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_global_actions_are_in_menu_and_toolbar(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test')
        try:
            self.assertEqual([frame.menuBar.GetMenuLabelText(i) for i in range(4)],
                             ['文件', '任务', '查看', '帮助'])
            for index, expected in enumerate((
                ['打开下载文件夹', '下载M3U8', '下载TS', '设置', '退出'],
                ['全部暂停', '全部继续'],
                ['全部展开', '全部折叠', '刷新', '查找', '默认展开任务', '显示工具栏', '显示状态栏'],
                ['使用说明', '关于'],
            )):
                labels = [item.GetItemLabelText() for item in frame.menuBar.GetMenu(index).GetMenuItems()
                          if not item.IsSeparator()]
                self.assertEqual(labels, expected)
            tools = [frame.toolBar.GetToolByPos(i).GetLabel() for i in range(frame.toolBar.GetToolsCount())]
            for label in ('全部展开', '全部折叠', '全部暂停', '刷新', '设置'):
                self.assertIn(label, tools)
            self.assertFalse(frame.toolBar.GetToolEnabled(frame._pauseTool.GetId()))
            Downloader._pending.add(self.key)
            frame._UpdatePauseTool()
            self.assertTrue(frame.toolBar.GetToolEnabled(frame._pauseTool.GetId()))
            Downloader.Pause()
            frame._UpdatePauseTool()
            self.assertEqual(frame._pauseTool.GetLabel(), '全部继续')
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_fixed_pause_resume_actions_handle_individually_paused_tasks(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test')
        try:
            self.assertEqual(frame._GlobalDownloadActions(), (False, False))
            other = Downloader.FileKey(PathManager.GetAbsPath('other/a.ts'))
            Downloader._pending.update({self.key, other})
            Downloader.PauseFiles({self.key})
            self.assertEqual(frame._GlobalDownloadActions(), (True, True))
            frame.OnResumeAllDownloads(None)
            self.assertFalse(Downloader.Snapshot()['paused_files'])
            self.assertEqual(frame._GlobalDownloadActions(), (True, False))
            frame.OnPauseAllDownloads(None)
            self.assertEqual(frame._GlobalDownloadActions(), (False, True))
            # 再次调用暂停不能意外切换成继续。
            frame.OnPauseAllDownloads(None)
            self.assertTrue(Downloader.IsPaused())
            frame.OnResumeAllDownloads(None)
            Downloader.PauseFiles({self.key, other})
            frame._UpdatePauseTool()
            self.assertEqual(frame._pauseTool.GetLabel(), '全部继续')
            frame.OnPauseDownloads(None)
            self.assertEqual(frame._GlobalDownloadActions(), (True, False))
            self.assertEqual([item.GetItemLabelText() for item in frame.menuBar.GetMenu(1).GetMenuItems()],
                             ['全部暂停', '全部继续'])
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_empty_filter_message_and_clear_all(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test')
        try:
            frame.statusFilter.SetStringSelection('已完成')
            frame.searchCtrl.ChangeValue('missing')
            frame.OnStatusFilter(None)
            self.assertTrue(frame.emptyPanel.IsShown())
            self.assertEqual(frame.emptyText.GetLabel(), '没有符合条件的任务')
            self.assertTrue(frame.clearFiltersButton.IsShown())
            frame.OnClearAllFilters(None)
            self.assertEqual(frame.searchCtrl.GetValue(), '')
            self.assertEqual(frame.statusFilter.GetSelection(), 0)
            self.assertIsNone(frame.model.visible_tasks)
            self.assertFalse(frame.emptyPanel.IsShown())
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_double_click_only_toggles_and_segment_download_is_explicit(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test')
        try:
            item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
            child = frame.model.ObjectToItem(frame.model._BuildKey((0, 1)))
            event = SimpleNamespace(GetDataViewColumn=lambda: frame.mcTree.GetColumn(1), GetItem=lambda: item)
            with patch.object(frame, '_DownloadFiles') as download:
                frame.OnActivatedChanged(event)
                self.assertTrue(frame.mcTree.IsExpanded(item))
                frame.OnActivatedChanged(event)
                self.assertFalse(frame.mcTree.IsExpanded(item))
                event.GetItem = lambda: child
                frame.OnActivatedChanged(event)
                download.assert_not_called()
                renderer = frame.mcTree.GetColumn(6).GetRenderer()
                renderer.ActivateCell(wx.Rect(0, 0, 200, 24), frame.model, child, 4, None)
                download.assert_called_once_with(self.task.parent.fileName, [(1, child)])
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_progress_only_notifies_changed_cells(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test')
        try:
            item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
            info = frame.model.TaskInfo(0)
            info['progress'] = '75% · 3/4'
            with patch.object(frame.model, 'TaskInfo', return_value=info), \
                 patch.object(frame.model, 'ItemChanged') as row_changed, \
                 patch.object(frame.model, 'ValueChanged') as cell_changed:
                frame.OnTaskProgress(None)
                row_changed.assert_not_called()
                cell_changed.assert_called_once_with(item, 5)
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_settings_picker_labels_are_chinese(self):
        from src.views.tab_setting import TabSetting
        frame = wx.Frame(None)
        try:
            panel = TabSetting(frame)
            self.assertEqual(panel.controls['download_dir'].GetPickerCtrl().GetLabel(), '选择文件夹')
            self.assertEqual(panel.controls['ffmpeg_path'].GetPickerCtrl().GetLabel(), '选择文件')
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_menu_shortcuts_and_search_escape(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test')
        try:
            labels = {item.GetItemLabelText(): item.GetItemLabel()
                      for index in range(frame.menuBar.GetMenuCount())
                      for item in frame.menuBar.GetMenu(index).GetMenuItems() if not item.IsSeparator()}
            for label, shortcut in {
                '全部暂停': 'Ctrl-Shift-P', '全部继续': 'Ctrl-Shift-R',
                '全部展开': 'Ctrl-Shift-E', '全部折叠': 'Ctrl-Shift-C',
                '使用说明': 'F1', '刷新': 'F5', '查找': 'Ctrl-F',
            }.items():
                self.assertEqual(labels[label].split('\t')[1], shortcut)
            frame.statusFilter.SetStringSelection('待继续')
            frame.searchCtrl.ChangeValue('missing')
            frame.OnStatusFilter(None)
            self.assertEqual(frame.model.visible_tasks, set())
            event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
            event.SetKeyCode(wx.WXK_ESCAPE)
            frame.OnSearchKey(event)
            self.assertEqual(frame.searchCtrl.GetValue(), '')
            self.assertEqual(frame.statusFilter.GetStringSelection(), '待继续')
            self.assertEqual(frame.model.visible_tasks, {0})
            self.assertFalse(frame._searchTimer.IsRunning())
            event.SetKeyCode(ord('A'))
            frame.OnSearchKey(event)
            self.assertTrue(event.GetSkipped())
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_default_expansion_applies_to_new_tasks_not_manual_actions(self):
        values = dict(SysSetting.GetAll(), default_expand_tasks=True)
        with patch.object(SysSetting, 'GetAll', side_effect=lambda: dict(values)), patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test')
            try:
                item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
                self.assertTrue(frame.defaultExpandItem.IsChecked())
                self.assertTrue(frame.mcTree.IsExpanded(item))
                with patch.object(SysSetting, 'Save') as save:
                    frame.OnCollapseAll(None)
                    save.assert_not_called()
                self.tree.items.append(TreeItem(parent=FileItem(fileName='new/download.m3u8'),
                                                childs=[FileItem(fileName='new/a.ts')]))
                frame._RefreshWithState()
                new_item = frame.model.ObjectToItem(frame.model._BuildKey((1,)))
                self.assertFalse(frame.mcTree.IsExpanded(item))
                self.assertTrue(frame.mcTree.IsExpanded(new_item))
                with patch.object(SysSetting, 'Save') as save:
                    frame.OnDefaultExpandTasks(SimpleNamespace(IsChecked=lambda: False))
                    self.assertFalse(save.call_args.args[0]['default_expand_tasks'])
                    self.assertTrue(frame.mcTree.IsExpanded(new_item))
                with patch.object(SysSetting, 'Save', side_effect=OSError('denied')), \
                     patch('src.views.main_frame.wx.MessageBox'):
                    frame.defaultExpandItem.Check(False)
                    frame.OnDefaultExpandTasks(SimpleNamespace(IsChecked=lambda: False))
                    self.assertTrue(frame.defaultExpandItem.IsChecked())
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
