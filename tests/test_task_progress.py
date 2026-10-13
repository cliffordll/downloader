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

    def test_partial_task_keeps_unselected_segments_not_started_and_can_continue(self):
        from src.schemas.task import TaskStatus
        self.task.task_status = TaskStatus.PARTIAL
        self.assertEqual(self.model.TaskInfo(0)['status'], '部分完成')
        self.assertEqual(self.model.ChildStatus(0, 0), '已完成')
        self.assertEqual(self.model.ChildStatus(0, 1), '未开始')
        action = next(a for a in self.model.TaskActions(0) if a['id'] == 'start')
        self.assertEqual(action['label'], '继续')
        self.assertTrue(action['enabled'])
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            self.assertTrue(frame.statusFilter.SetStringSelection('部分完成'))
            self.assertEqual(frame._GlobalDownloadActions(), (False, True))
        finally:
            frame.Destroy()

    def test_hover_details_and_clear_without_disk_access(self):
        from pathlib import Path
        self.task.parent.absUri = 'https://example.com/index.m3u8?token=1'
        self.task.childs[1].absUri = 'https://cdn.example.com/b.ts?token=2'
        self.task.work_dir = Path('task')
        self.task.manifests[-2] = FileItem(fileName='task/download.m3u8')
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            model = frame.model
            root = model.ObjectToItem(model._BuildKey((0,)))
            child = model.ObjectToItem(model._BuildKey((0, 1)))
            folder = model.ObjectToItem(model._BuildKey((0, -1)))
            playlist = model.ObjectToItem(model._BuildKey((0, -2)))
            with patch.object(Path, 'stat', side_effect=AssertionError('tooltip must not check files')):
                self.assertIn(self.task.parent.absUri, model.GetItemToolTip(root))
                self.assertIn(self.task.childs[1].absUri, model.GetItemToolTip(child))
                self.assertEqual(model.GetItemToolTip(folder), '目录：' + str(Path('task') / 'segments'))
                self.assertNotIn('下载地址', model.GetItemToolTip(playlist))
            source = frame.mcTree.GetMainWindow()
            event = SimpleNamespace(Skip=Mock(), GetEventObject=lambda: source,
                                    GetPosition=lambda: wx.Point(10, 10), Dragging=lambda: False)
            with patch.object(frame.mcTree, 'HitTest', return_value=(child, frame.mcTree.GetColumn(1))), \
                 patch('src.views.main_frame.wx.CallLater') as delay:
                frame.OnTaskHover(event)
                delay.assert_called_once_with(500, frame._ShowTaskToolTip)
                frame._ShowTaskToolTip()
                self.assertIsNotNone(frame._taskTipWindow)
                self.assertIn(self.task.childs[1].absUri, frame._taskTipWindow.GetChildren()[0].GetLabel())
                # 显示后不安排关闭定时器，重复悬停也不重建提示。
                frame.OnTaskHover(event)
                delay.assert_called_once()
            frame.ClearTaskToolTip(event)
            self.assertIsNone(frame._taskTipWindow)
            self.assertEqual(frame._taskTip, '')
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_manual_duration_completion_updates_playlist_without_auto_merge(self):
        from src.schemas.task import TaskStatus
        self.task.task_status = TaskStatus.WAITING_MERGE
        self.task.download = len(self.task.childs)
        self.service.repository.get.return_value = SimpleNamespace(details=SimpleNamespace(segments=[]))
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            frame._manualDurationChecks = {self.task.task_id}
            with patch.object(frame.model, 'ApplyTaskRecord', return_value=0), \
                 patch.object(frame, '_CreateM3U8File', return_value=True) as playlist, \
                 patch.object(frame.model, '_SendEvent') as complete:
                frame._DurationChecked(self.task.task_id, None, None)
                playlist.assert_called_once_with(self.task.parent.fileName)
                complete.assert_not_called()
                self.assertIn(self.task.task_id, frame._completion_notified)
                self.assertFalse(frame._manualDurationChecks)
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_segment_rows_show_only_their_own_status(self):
        complete = self.model.ObjectToItem(self.model._BuildKey((0, 0)))
        pending = self.model.ObjectToItem(self.model._BuildKey((0, 1)))
        self.assertEqual(self.model.GetValue(complete, 5), '')
        self.assertEqual(self.model.GetValue(complete, 6), '已完成')
        self.assertEqual(self.model.GetValue(pending, 6), '未开始')
        M3U8Downloader._pending.add(self.key)
        self.assertEqual(self.model.GetValue(pending, 6), '等待下载')
        M3U8Downloader._requesting.add(self.key)
        self.assertEqual(self.model.GetValue(pending, 6), '下载中')
        M3U8Downloader.Pause()
        self.assertEqual(self.model.GetValue(pending, 6), '暂停中')
        M3U8Downloader._requesting.clear()
        self.assertEqual(self.model.GetValue(pending, 6), '已暂停')
        M3U8Downloader.Resume()
        M3U8Downloader._pending.clear()
        M3U8Downloader._failed.add(self.key)
        self.assertEqual(self.model.GetValue(pending, 6), '下载失败')
        self.assertEqual(self.model.GetValue(pending, 5), '')

    def test_expanded_segment_status_refreshes_when_parent_status_is_unchanged(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            root = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
            frame.mcTree.Expand(root)
            folder = frame.model.ObjectToItem(frame.model._BuildKey((0, -1)))
            frame.mcTree.Expand(folder)
            self.task.childs[0].fileSize = '-'
            first_key = M3U8Downloader.FileKey(PathManager.GetAbsPath('task/a.ts'))
            M3U8Downloader._pending.update({self.key, first_key})
            M3U8Downloader._requesting.add(first_key)
            frame.OnTaskProgress(None)
            parent_status = frame.model.TaskInfo(0)['status']
            child = frame.model.ObjectToItem(frame.model._BuildKey((0, 1)))
            with patch.object(frame.model, 'ValueChanged') as changed:
                M3U8Downloader._requesting.add(self.key)
                frame.OnTaskProgress(None)
                self.assertEqual(frame.model.TaskInfo(0)['status'], parent_status)
                self.assertTrue(any(call.args == (child, 6) for call in changed.call_args_list))
            with patch.object(frame.model, 'ValueChanged') as changed:
                frame.OnTaskProgress(None)
                changed.assert_not_called()
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_task_expand_keeps_segments_collapsed_and_refresh_preserves_choice(self):
        self.task.manifests[-2] = FileItem(fileName='task/download.m3u8', displayName='download.m3u8')
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            model = frame.model
            root = model.ObjectToItem(model._BuildKey((0,)))
            folder = model.ObjectToItem(model._BuildKey((0, -1)))
            frame._SetTaskExpanded(root)
            self.assertTrue(frame.mcTree.IsExpanded(root))
            self.assertFalse(frame.mcTree.IsExpanded(folder))
            children = []
            model.GetChildren(root, children)
            self.assertEqual([model.GetValue(item, 1) for item in children], ['task/'])
            work = children[0]
            self.assertFalse(frame.mcTree.IsExpanded(work))
            frame._SetTaskExpanded(work, True)
            children = []
            model.GetChildren(work, children)
            self.assertEqual([model.GetValue(item, 1) for item in children], ['download.m3u8', 'segments/'])
            segments = []
            model.GetChildren(folder, segments)
            self.assertEqual(len(segments), 2)
            self.assertTrue(all(model.GetParent(item) == folder for item in segments))
            self.assertEqual(model.GetParent(folder), work)
            self.assertEqual(model.GetParent(work), root)
            self.assertEqual(model.GetValue(children[0], 4), '')
            with patch.object(frame, '_DownloadFiles') as download:
                frame.OnSegmentDownload(children[0])
                frame.OnSegmentDownload(folder)
                download.assert_not_called()
                frame.OnSegmentDownload(segments[1])
                download.assert_called_once()
            self.assertEqual(model.TaskInfo(0)['progress'], '50% · 1/2')
            frame._RefreshWithState()
            self.assertFalse(frame.mcTree.IsExpanded(folder))
            frame._SetTaskExpanded(folder, True)
            frame._RefreshWithState()
            self.assertTrue(frame.mcTree.IsExpanded(folder))
            frame.OnRefresh(None)
            self.assertTrue(frame.mcTree.IsExpanded(folder))
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

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
        self.task.output_path = output
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
            self.assertEqual([a['label'] for a in actions], ['继续', '重试', '删除', '更多'])
            self.assertEqual([a['enabled'] for a in actions], [True, False, True, True])
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
                self.assertTrue(click(3))
                self.app.ProcessPendingEvents()
                menu.assert_called_once_with(item)
            # 展开状态不再改变操作列内容。
            before = frame.model.TaskActions(0)
            frame.mcTree.Expand(item)
            self.assertEqual(frame.model.TaskActions(0), before)
            bitmap = wx.Bitmap(size.width, size.height)
            dc = wx.MemoryDC(bitmap)
            renderer.SetValue(frame.model.GetValue(item, 4))
            self.assertTrue(renderer.Render(cell, dc, 0))
            dc.SelectObject(wx.NullBitmap)
            M3U8Downloader._pending.add(self.key)
            M3U8Downloader.Pause()
            self.assertEqual([a['enabled'] for a in frame.model.TaskActions(0)], [True, False, False, True])
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
                self.assertEqual(set(entries), {'打开文件夹', '检测分片时长', '转 MP4', '播放视频', '另存为…'})
                self.assertTrue(entries['打开文件夹'].IsEnabled())
                self.assertFalse(entries['播放视频'].IsEnabled())
                self.assertFalse(entries['转 MP4'].IsEnabled())


            M3U8Downloader._pending.add(self.key)
            with patch.object(frame.mcTree, 'PopupMenu', side_effect=inspect_menu) as popup:
                frame.OnTaskMenu(item)
                popup.assert_called_once()
            for key in (-4, -1):
                folder = frame.model.ObjectToItem(frame.model._BuildKey((0, key)))
                def inspect_folder_menu(menu):
                    self.assertEqual([entry.GetItemLabelText() for entry in menu.GetMenuItems()],
                                     ['打开文件夹'])
                with patch.object(frame.mcTree, 'PopupMenu', side_effect=inspect_folder_menu) as popup:
                    frame.OnTaskMenu(folder)
                    popup.assert_called_once()
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

    def test_edit_segment_url_saves_only_on_confirmation(self):
        segment = SimpleNamespace(sequence=7, source_url='https://example.com/old.ts', duration=4, status='pending')
        self.service.repository.get.return_value = SimpleNamespace(details=SimpleNamespace(segments=[segment]))
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            with patch('src.views.main_frame.SegmentEditDialog') as dialog, \
                 patch.object(frame.runner, 'update_segment_info') as update, \
                 patch.object(frame, '_SyncDownloads') as sync:
                dialog.return_value.ShowModal.return_value = wx.ID_CANCEL
                frame._EditSegmentInfo(self.task.task_id, 7)
                update.assert_not_called()
                sync.assert_not_called()
                dialog.return_value.ShowModal.return_value = wx.ID_OK
                dialog.return_value.GetURL.return_value = 'https://example.com/new.ts?token=a%2Bb'
                dialog.return_value.GetDuration.return_value = '5.25'
                frame._EditSegmentInfo(self.task.task_id, 7)
                update.assert_called_once_with(self.task.task_id, 7, 'https://example.com/new.ts?token=a%2Bb', '5.25')
                sync.assert_called_once()
                self.assertEqual(dialog.return_value.Destroy.call_count, 2)
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_download_address_menu_preserves_full_url(self):
        from src.schemas.task import TaskType, TaskStatus
        task_url = 'https://example.com/index.m3u8?token=a%2Bb&part=1'
        segment_url = 'https://cdn.example.com/000001.ts?token=x%2Fy&part=2'
        self.task.parent.absUri = task_url
        self.task.childs[1].absUri = segment_url
        self.task.outputs = [FileItem(fileName='task/output.mp4', fileSize=10)]
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            for key, expected in [((0,), task_url), ((0, 2), segment_url), ((0, 0), None), ((0, -1), None)]:
                item = frame.model.ObjectToItem(frame.model._BuildKey(key))
                def inspect(menu):
                    entries = [entry for entry in menu.GetMenuItems() if entry.GetItemLabelText() == '查看下载地址']
                    self.assertEqual(len(entries), int(expected is not None))
                    if entries:
                        menu.ProcessEvent(wx.CommandEvent(wx.EVT_MENU.typeId, entries[0].GetId()))
                with patch.object(frame.mcTree, 'PopupMenu', side_effect=inspect), \
                     patch.object(frame, '_ShowInformation') as show:
                    frame.OnTaskMenu(item)
                    if expected:
                        show.assert_called_once_with('下载地址', expected)
                    else:
                        show.assert_not_called()
            self.task.task_type = TaskType.MP4
            self.task.task_status = TaskStatus.DOWNLOADING
            self.task.outputs[0].fileName = 'task/video.mp4.part'
            expected = task_url
            item = frame.model.ObjectToItem(frame.model._BuildKey((0, 0)))
            with patch.object(frame.mcTree, 'PopupMenu', side_effect=inspect), \
                 patch.object(frame, '_ShowInformation') as show:
                frame.OnTaskMenu(item)
                show.assert_called_once_with('下载地址', task_url)
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_file_context_menus_and_selected_segment_download(self):
        from src.schemas.task import TaskType, TaskStatus
        self.task.manifests[-2] = FileItem(fileName='task/download.m3u8')
        self.task.outputs = [FileItem(fileName='task/output.mp4', fileSize=10)]
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            cases = [(-2, ['打开', '打开所在文件夹', '|', '查看内容']),
                     (0, ['打开', '打开所在文件夹', '|', '另存为…']),
                     (1, ['打开', '打开所在文件夹', '|', '修改分片信息', '|', '另存为…']),
                     (2, ['下载', '|', '修改分片信息'])]
            for key, labels in cases:
                item = frame.model.ObjectToItem(frame.model._BuildKey((0, key)))
                def inspect(menu):
                    self.assertEqual(['|' if entry.IsSeparator() else entry.GetItemLabelText()
                                      for entry in menu.GetMenuItems()], labels)
                    if key == 2:
                        event = wx.CommandEvent(wx.EVT_MENU.typeId, menu.GetMenuItems()[0].GetId())
                        menu.ProcessEvent(event)
                with patch.object(frame.mcTree, 'PopupMenu', side_effect=inspect), \
                     patch.object(frame, '_DownloadFiles') as download:
                    frame.OnTaskMenu(item)
                    if key == 2:
                        download.assert_called_once_with(self.task.parent.fileName, [(1, item)])
            self.task.task_type = TaskType.MP4
            self.task.task_status = TaskStatus.DOWNLOADING
            self.task.outputs[0].fileName = 'task/video.mp4.part'
            item = frame.model.ObjectToItem(frame.model._BuildKey((0, 0)))
            def inspect_partial(menu):
                self.assertEqual([entry.GetItemLabelText() for entry in menu.GetMenuItems()], ['打开所在文件夹'])
            with patch.object(frame.mcTree, 'PopupMenu', side_effect=inspect_partial):
                frame.OnTaskMenu(item)
            with patch('src.views.main_frame.Path.read_text', return_value='#EXTM3U\nsegments/000000.ts') as read, \
                 patch.object(frame, '_ShowInformation') as show:
                frame._ViewPlaylist('task/download.m3u8')
                read.assert_called_once_with(encoding='utf-8-sig')
                show.assert_called_once_with('查看内容：download.m3u8', '#EXTM3U\nsegments/000000.ts', line_numbers=True)
            with patch('src.views.main_frame.Path.read_text', side_effect=FileNotFoundError), \
                 patch('src.views.main_frame.wx.MessageBox') as message:
                frame._ViewPlaylist('task/download.m3u8')
                message.assert_called_once()
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
            view_labels = ['全部展开', '全部折叠', '刷新', '查找', '默认展开任务']
            if wx.Platform != '__WXMAC__':
                view_labels.append('显示工具栏')
            view_labels.append('显示状态栏')
            for index, expected in enumerate((
                ['打开下载文件夹', '下载 M3U8', '下载 TS', '下载 MP4', '设置', '退出'],
                ['全部暂停', '全部继续'],
                view_labels,
                ['使用说明', '关于'],
            )):
                labels = [item.GetItemLabelText() for item in frame.menuBar.GetMenu(index).GetMenuItems()
                          if not item.IsSeparator()]
                self.assertEqual(labels, expected)
            if wx.Platform == '__WXMAC__':
                self.assertIsNone(frame.toolBar)
                self.assertIsNone(frame.GetToolBar())
                self.assertIsNone(frame.showToolItem)
                M3U8Downloader._pending.add(self.key)
                frame._UpdatePauseTool()
                self.assertEqual(frame._GlobalDownloadActions(), (True, False))
                frame.OnPauseAllDownloads(None)
                self.assertEqual(frame._GlobalDownloadActions(), (False, True))
                frame.OnResumeAllDownloads(None)
                self.assertEqual(frame._GlobalDownloadActions(), (True, False))
                return
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
            if frame.toolBar is not None:
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

    @unittest.skipUnless(wx.Platform == '__WXMAC__', 'Mac 行首箭头路径')
    def test_mac_disclosure_toggles_and_consumes_mouse_release(self):
        frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            self.app.Yield()
            tree = frame.mcTree
            window = tree.GetMainWindow()
            item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
            for expanded in (True, False, True, False):
                cell = tree.GetItemRect(item, tree.GetExpanderColumn())
                position = wx.Point(cell.x - frame.FromDIP(8), cell.y + cell.height // 2)
                hit, column = tree.HitTest(position)
                self.assertEqual(hit, item)
                self.assertEqual(column.GetModelColumn(), 1)
                with patch.object(frame, '_SetTaskExpanded', wraps=frame._SetTaskExpanded) as toggle:
                    for kind in (wx.wxEVT_LEFT_DOWN, wx.wxEVT_LEFT_UP):
                        event = wx.MouseEvent(kind)
                        event.SetEventObject(window)
                        event.SetPosition(window.ScreenToClient(tree.ClientToScreen(position)))
                        window.GetEventHandler().ProcessEvent(event)
                        self.assertFalse(event.GetSkipped())
                    toggle.assert_called_once_with(item)
                self.assertEqual(tree.IsExpanded(item), expanded)
                self.app.Yield()
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

    def test_sequence_column_does_not_grow_with_segment_count(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            initial = frame.mcTree.GetColumn(0).GetWidth()
            self.task.childs = [FileItem(fileName=f'task/{i}.ts') for i in range(1000)]
            frame._RefreshWithState()
            width = frame.mcTree.GetColumn(0).GetWidth()
            self.assertEqual(width, initial)
            self.assertLessEqual(sum(frame.mcTree.GetColumn(i).GetWidth() for i in range(7)),
                                 frame.mcTree.GetClientSize().width)
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_visible_row_numbers_follow_expansion_filter_and_file_insertion(self):
        self.task.manifests[-2] = FileItem(fileName='task/download.m3u8', displayName='download.m3u8')
        self.tree.items.append(TreeItem(parent=FileItem(fileName='other/download.m3u8')))
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            model = frame.model
            def item(*key):
                return model.ObjectToItem(model._BuildKey(key))
            def numbers(*keys):
                return [model.GetValue(item(*key), 0) for key in keys]

            self.assertEqual(numbers((0,), (1,)), ['1', '2'])
            for key in ((0,), (0, -4), (0, -1)):
                frame._SetTaskExpanded(item(*key), True)
            self.app.ProcessPendingEvents()
            keys = ((0,), (0, -4), (0, -2), (0, -1), (0, 0), (0, 1), (1,))
            self.assertEqual(numbers(*keys), ['1', '2', '3', '4', '5', '6', '7'])
            # 重绘只读序号缓存，不反复遍历可见分片。
            with patch.object(model, 'GetChildren', side_effect=AssertionError('must use cached numbers')):
                self.assertEqual(numbers(*keys), ['1', '2', '3', '4', '5', '6', '7'])

            self.task.outputs.append(FileItem(fileName='task.mp4'))
            frame._RefreshWithState()
            self.assertEqual(numbers((0,), (0, 0), (0, -4), (0, -2), (0, -1), (0, 1), (0, 2), (1,)),
                             ['1', '2', '3', '4', '5', '6', '7', '8'])
            frame.mcTree.Collapse(item(0, -4))
            self.app.ProcessPendingEvents()
            self.assertEqual(numbers((0,), (0, 0), (0, -4), (1,)), ['1', '2', '3', '4'])
            self.assertEqual(numbers((0, -1), (0, 1)), ['', ''])
            frame._SearchItems('other')
            self.assertEqual(numbers((1,)), ['1'])
            self.assertEqual(model.ParseKey(model.ItemToObject(item(1))), (1,))
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_tree_indentation_is_in_task_name_column(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            model, tree = frame.model, frame.mcTree
            column = tree.GetExpanderColumn()
            self.assertEqual(column.GetTitle(), '名称')
            self.assertEqual(column.GetModelColumn(), 1)
            self.assertEqual(tree.GetIndent(), frame.FromDIP(16))
            nodes = [model.ObjectToItem(model._BuildKey(key)) for key in ((0,), (0, -4), (0, -1), (0, 0))]
            for node in nodes[:-1]:
                frame._SetTaskExpanded(node, True)
            self.app.ProcessPendingEvents()
            self.assertEqual(model.GetValue(nodes[0], 0), '1')
            for number, (parent, child) in enumerate(zip(nodes, nodes[1:]), start=2):
                self.assertEqual(model.GetParent(child), parent)
                self.assertEqual(model.GetValue(child, 0), str(number))
                self.assertGreater(tree.GetItemRect(child, column).x, tree.GetItemRect(parent, column).x)
            for node, bold in zip(nodes, (True, False, False, False)):
                attr = wx.dataview.DataViewItemAttr()
                self.assertTrue(model.GetAttr(node, 1, attr))
                self.assertEqual(attr.GetBold(), bold)
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
            for index, (normal, expanded) in enumerate(zip(layouts[1024], layouts[1400])):
                if wx.Platform == '__WXMAC__' and index != 1:
                    self.assertEqual(expanded, normal)
                else:
                    self.assertGreater(expanded, normal)
            if wx.Platform == '__WXMAC__':
                self.assertLessEqual(layouts[1024][0], frame.FromDIP(50))
                self.assertGreaterEqual(layouts[1024][1], frame.FromDIP(180))
                self.assertLess(layouts[1024][6], frame.FromDIP(240))
                dc = wx.ClientDC(frame.mcTree)
                dc.SetFont(frame.mcTree.GetFont())
                self.assertGreaterEqual(layouts[1024][2], frame.FromDIP(180))
                self.assertGreaterEqual(layouts[1024][2],
                                        dc.GetTextExtent(frame.model.TaskInfo(0)['progress'])[0]
                                        + frame.FromDIP(20))
                buttons = frame._actionRenderer._ActionRects(wx.Rect(0, 0, layouts[1024][6], 24))
                for label in ('开始', '暂停', '继续', '重试', '删除', '更多', '录制', '停止'):
                    self.assertGreaterEqual(min(rect.width for rect in buttons),
                                            dc.GetTextExtent(label)[0] + frame.FromDIP(8))
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

    @unittest.skipUnless(wx.Platform == '__WXMAC__', 'Cocoa 原生列间距')
    def test_mac_action_column_stays_inside_visible_table_after_show(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            frame.Show()
            for width in (1024, 1400, 1024):
                frame.SetSize(frame.FromDIP(wx.Size(width, 700)))
                self.app.Yield()
                frame._FitShownTaskColumns()
                dc = wx.ClientDC(frame.mcTree)
                dc.SetFont(frame.mcTree.GetFont())
                for digit in '0123456789':
                    date = f'{digit * 4}-{digit * 2}-{digit * 2} {digit * 2}:{digit * 2}'
                    self.assertGreaterEqual(frame.mcTree.GetColumn(5).GetWidth(),
                                            dc.GetTextExtent(date)[0] + frame.FromDIP(12))
                item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
                rect = frame.mcTree.GetItemRect(item, frame.mcTree.GetColumn(6))
                self.assertGreater(rect.width, 0)
                self.assertLessEqual(rect.x + rect.width, frame.mcTree.GetClientSize().width)
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_close_stops_timers_before_native_destruction_and_ignores_queued_callbacks(self):
        with patch.object(MainFrame, 'Show'):
            frame = MainFrame(None, 'test', self.service, self.tree)
        try:
            frame._searchTimer.StartOnce(500)
            def inspect_before_destroy(window):
                self.assertTrue(window._closing)
                self.assertFalse(window._progressTimer.IsRunning())
                self.assertFalse(window._searchTimer.IsRunning())
                return True
            with patch.object(frame.runner, 'shutdown') as shutdown:
                with patch.object(wx.Frame, 'Destroy', new=inspect_before_destroy):
                    frame.Destroy()
                    frame.OnClose(SimpleNamespace(Skip=lambda: None))
                shutdown.assert_called_once()
            with patch.object(frame, '_SyncDownloads') as sync, \
                    patch.object(frame, '_SearchItems') as search, \
                    patch.object(frame.mcTree, 'GetColumn') as column:
                frame.OnTaskProgress(None)
                frame.OnSearchTimer(None)
                frame._FitTaskColumns()
                frame._FitShownTaskColumns()
                sync.assert_not_called()
                search.assert_not_called()
                column.assert_not_called()
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
