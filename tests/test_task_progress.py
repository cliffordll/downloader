import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import wx

from src.media.m3u8.m3u8_downloader import M3U8Downloader
from src.media.m3u8.ffmpeg_converter import FFmpegConverter
from src.core.path_manager import PathManager
from src.core.sys_setting import SysSetting
from src.models.tree_model import MultiColumnTreeModel
from src.models.file_base import FileItem, TreeItem, TreeData
from src.views.main_frame import MainFrame


class TaskProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.GetApp() or wx.App(False)

    def setUp(self):
        self.task = TreeItem(parent=FileItem(fileName='task/download.m3u8'), childs=[
            FileItem(fileName='task/a.ts', fileSize=10), FileItem(fileName='task/b.ts')], download=1)
        self.tree = TreeData(items=[self.task])
        for patcher in (patch.object(M3U8Downloader, '_pending', set()),
                        patch.object(SysSetting, '_values', dict(SysSetting.GetAll(), default_expand_tasks=False)),
                        patch.object(M3U8Downloader, '_requesting', set()),
                        patch.object(M3U8Downloader, '_failed', set()),
                        patch.object(M3U8Downloader, '_errors', {}),
            patch.object(M3U8Downloader, '_paused_files', set()),
                        patch.object(M3U8Downloader, '_user_paused', threading.Event()),
                        patch.object(FFmpegConverter, '_outputs', set()),
                        patch('src.views.main_frame.load_tree', return_value=self.tree)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.service = Mock()  # 展示测试显式注入服务替身，避免访问用户任务库。
        self.service.repository.list_tasks.return_value = []
        self.model = MultiColumnTreeModel(self.tree)
        self.addCleanup(self.model.DecRef)
        self.key = M3U8Downloader.FileKey(PathManager.GetAbsPath('task/b.ts'))

    def test_progress_and_pause_transitions(self):
        self.assertEqual(self.model.TaskInfo(0)['progress'], '50% · 1/2')
        self.assertEqual(self.model.TaskInfo(0)['status'], '待继续')
        M3U8Downloader._pending.add(self.key)
        self.assertEqual(self.model.TaskInfo(0)['status'], '等待下载')
        M3U8Downloader._requesting.add(self.key)
        self.assertEqual(self.model.TaskInfo(0)['status'], '下载中')
        M3U8Downloader.Pause()
        self.assertEqual(self.model.TaskInfo(0)['status'], '暂停中')
        M3U8Downloader._requesting.clear()
        self.assertEqual(self.model.TaskInfo(0)['status'], '已暂停')
        M3U8Downloader.Resume()
        M3U8Downloader._pending.clear()
        M3U8Downloader._failed.add(self.key)
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
        FFmpegConverter._outputs.add(os.path.abspath(output))
        self.assertEqual(self.model.TaskInfo(0)['status'], '合并中')
        FFmpegConverter._outputs.clear()
        self.model.merge_failed.add(self.task.parent.fileName)
        self.assertEqual(self.model.TaskInfo(0)['status'], '合并失败')
        self.model.merge_failed.clear()
        self.task.outputs.append(FileItem(fileName='task/output.mp4', fileSize=20))
        self.assertEqual(self.model.TaskInfo(0)['status'], '已完成')

    def test_row_pause_button_dispatches_task_id_to_runner(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            M3U8Downloader._pending.add(self.key)
            item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
            with patch.object(frame.runner, 'activate', return_value=False) as activate:
                frame.OnTaskAction(item, 'start')
                activate.assert_called_once_with(self.task.task_id, retry=False)
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_unknown_task_notification_does_not_change_rows(self):
        self.tree.items.insert(0, TreeItem(parent=FileItem(fileName='other/download.m3u8'),
                                        childs=[FileItem(fileName='other/a.ts')]))
        receiver = SimpleNamespace(model=self.model)
        MainFrame._M3U8Changed(receiver, 'unknown-task-id')
        self.assertEqual(self.tree.items[0].download, 0)
        self.assertEqual(self.tree.items[1].download, 1)

    def test_fixed_row_actions_and_retry_scope(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
            self.assertEqual(frame.model.GetValue(item, 5), '50% · 1/2')
            renderer = frame.mcTree.GetColumn(6).GetRenderer()
            size = frame.FromDIP(wx.Size(200, 24))
            cell = wx.Rect(0, 0, size.width, size.height)

            def click(index):
                rect = renderer._ActionRects(cell)[index]
                point = wx.Point(rect.x + rect.width // 2, rect.y + rect.height // 2)
                return renderer.ActivateAt(cell, frame.model, item, 4, point)

            actions = frame.model.TaskActions(0)
            self.assertFalse(frame.mcTree.IsExpanded(item))
            self.assertEqual([a['label'] for a in actions], ['继续', '重试', '删除', '展开', '更多'])
            self.assertEqual([a['enabled'] for a in actions], [True, False, True, True, True])
            self.assertFalse(click(1))
            M3U8Downloader._failed.add(self.key)
            with patch.object(frame.runner, 'activate', return_value=1) as download:
                self.assertTrue(click(1))
                download.assert_called_once_with(self.task.task_id, retry=True)
            with patch.object(frame, 'OnDeleteTask') as delete:
                self.assertTrue(click(2))
                self.app.ProcessPendingEvents()
                delete.assert_called_once_with(self.task)
            with patch.object(frame, 'OnTaskMenu') as menu:
                self.assertTrue(click(4))
                self.app.ProcessPendingEvents()
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
            M3U8Downloader._pending.add(self.key)
            M3U8Downloader.Pause()
            self.assertEqual([a['enabled'] for a in frame.model.TaskActions(0)], [True, False, False, True, True])
            for index in (1, 2):
                self.assertFalse(click(index))
            M3U8Downloader._pending.clear()
            self.task.childs[1].fileSize = '10 B'
            with patch.object(frame, '_CreateMP4File') as merge:
                frame.OnTaskAction(item, 'merge')
                merge.assert_called_once_with(self.task.parent.fileName, item)
            with patch.object(frame, 'OnTaskMenu') as menu, patch.object(frame, 'OnDeleteTask') as delete:
                renderer.ActivateCell(cell, frame.model, item, 4, None)
                self.app.ProcessPendingEvents()
                menu.assert_called_once_with(item)
                delete.assert_not_called()
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_more_menu_and_delete_cancel(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
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


            M3U8Downloader._pending.add(self.key)
            with patch.object(frame.mcTree, 'PopupMenu', side_effect=inspect_menu):
                frame.OnTaskMenu(item)
                child = frame.model.ObjectToItem(frame.model._BuildKey((0, 0)))
                frame.OnTaskMenu(child)
            M3U8Downloader._pending.clear()
            with patch.object(frame.runner, 'busy', return_value=False), \
                 patch.object(frame.tasks.repository, 'delete') as delete, \
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
            frame = MainFrame(None, 'test', self.service, self.tree)
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
                self.app.ProcessPendingEvents()
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
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            frame.statusFilter.SetStringSelection('待继续')
            frame.OnStatusFilter(None)
            self.assertEqual(frame.model.visible_tasks, {0})
            M3U8Downloader._pending.add(self.key)
            M3U8Downloader._requesting.add(self.key)
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
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            self.assertEqual([frame.menuBar.GetMenuLabelText(i) for i in range(4)],
                             ['文件', '任务', '查看', '帮助'])
            for index, expected in enumerate((
                ['打开下载文件夹', '下载 M3U8', '下载 TS', '下载 MP4', '设置', '退出'],
                ['全部暂停', '全部继续'],
                ['全部展开', '全部折叠', '刷新', '查找', '默认展开任务', '显示工具栏', '显示状态栏'],
                ['使用说明', '关于'],
            )):
                labels = [item.GetItemLabelText() for item in frame.menuBar.GetMenu(index).GetMenuItems()
                          if not item.IsSeparator()]
                self.assertEqual(labels, expected)
            if wx.Platform == '__WXMAC__':
                tools = [frame.toolBar.FindToolByIndex(i).GetLabel()
                         for i in range(frame.toolBar.GetToolCount())]
            else:
                tools = [frame.toolBar.GetToolByPos(i).GetLabel()
                         for i in range(frame.toolBar.GetToolsCount())]
            for label in ('全部展开', '全部折叠', '全部暂停', '刷新', '设置'):
                self.assertIn(label, tools)
            self.assertFalse(frame.toolBar.GetToolEnabled(frame._pauseTool.GetId()))
            M3U8Downloader._pending.add(self.key)
            frame._UpdatePauseTool()
            self.assertTrue(frame.toolBar.GetToolEnabled(frame._pauseTool.GetId()))
            M3U8Downloader.Pause()
            frame._UpdatePauseTool()
            self.assertEqual(frame._pauseTool.GetLabel(), '全部继续')
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_fixed_pause_resume_actions_handle_individually_paused_tasks(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            self.assertEqual(frame._GlobalDownloadActions(), (False, False))
            other = M3U8Downloader.FileKey(PathManager.GetAbsPath('other/a.ts'))
            M3U8Downloader._pending.update({self.key, other})
            M3U8Downloader.PauseFiles({self.key})
            self.assertEqual(frame._GlobalDownloadActions(), (True, True))
            frame.OnResumeAllDownloads(None)
            self.assertFalse(M3U8Downloader.Snapshot()['paused_files'])
            self.assertEqual(frame._GlobalDownloadActions(), (True, False))
            frame.OnPauseAllDownloads(None)
            self.assertEqual(frame._GlobalDownloadActions(), (False, True))
            # 再次调用暂停不能意外切换成继续。
            frame.OnPauseAllDownloads(None)
            self.assertTrue(M3U8Downloader.IsPaused())
            frame.OnResumeAllDownloads(None)
            M3U8Downloader.PauseFiles({self.key, other})
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
            frame = MainFrame(None, 'test', self.service, self.tree)
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
            frame = MainFrame(None, 'test', self.service, self.tree)
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
            frame = MainFrame(None, 'test', self.service, self.tree)
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
        from src.views.dialogs.settings_dialog import TabSetting
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
            frame = MainFrame(None, 'test', self.service, self.tree)
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
            frame = MainFrame(None, 'test', self.service, self.tree)
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
            frame = MainFrame(None, 'test', self.service, self.tree)
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

    def test_sequence_column_grows_for_long_child_indices_without_resize(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            initial = frame.mcTree.GetColumn(0).GetWidth()
            self.task.childs = [FileItem(fileName=f'task/{i}.ts') for i in range(1000)]
            frame._RefreshWithState()
            width = frame.mcTree.GetColumn(0).GetWidth()
            self.assertGreater(width, initial)
            dc = wx.ClientDC(frame.mcTree)
            font = wx.Font(frame.mcTree.GetFont())
            font.SetWeight(wx.FONTWEIGHT_BOLD)
            dc.SetFont(font)
            required = dc.GetTextExtent('1.1000')[0] + 2 * frame.mcTree.GetIndent()
            self.assertGreaterEqual(width, required)
            self.assertLessEqual(sum(frame.mcTree.GetColumn(i).GetWidth() for i in range(7)),
                                 frame.mcTree.GetClientSize().width)
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_columns_fit_available_width_when_resizing(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            layouts = {}
            for width in (780, 1024, 1400, 850, 1024):
                frame.mcTree.SetSize(frame.FromDIP(wx.Size(width, 400)))
                frame._FitTaskColumns()
                columns = [frame.mcTree.GetColumn(i).GetWidth() for i in range(7)]
                self.assertLessEqual(sum(columns), frame.mcTree.GetClientSize().width)
                dc = wx.ClientDC(frame.mcTree)
                dc.SetFont(frame.mcTree.GetFont())
                self.assertGreaterEqual(columns[6], frame._actionRenderer.MinimumWidth(dc))
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
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            self.assertEqual(shown, [frame])
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()


if __name__ == '__main__':
    unittest.main()
