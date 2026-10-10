from src.models.tree_model import load_tree
"""混合类型任务的列表投影及事件分发；不请求网络、不修改用户任务库。"""
from pathlib import Path
import os
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import wx

from src.storage.task_repository import TaskRepository
from src.core.task_service import TaskService
from src.models.tree_model import MultiColumnTreeModel
from src.schemas.task import MP4Task, MP4Details, RTMPTask, RTMPDetails, TaskProgress, TaskStatus
from src.views.main_frame import MainFrame, TaskActionRenderer, TaskProgressRenderer


class TaskTypesUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.GetApp() or wx.App(False)

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.repo = TaskRepository(self.root / 'downloads.db')
        self.service = TaskService(self.repo)

    def mp4(self, **kwargs):
        return self.repo.create(MP4Task(name='MP4', save_dir=self.root / 'mp4',
            source_url='https://example.com/video.mp4',
            details=MP4Details(target_path='nested/video.mp4', temporary_path='video.part'), **kwargs))

    def live(self, **kwargs):
        return self.repo.create(RTMPTask(name='RTMP', save_dir=self.root / 'live',
            source_url='rtmp://example.com/live', details=RTMPDetails(target_path='video.flv'), **kwargs))

    def model(self):
        return MultiColumnTreeModel(load_tree(self.service))

    def test_mixed_list_has_children_for_m3u8_and_mp4(self):
        self.service.create_m3u8(self.root / 'hls', 'https://example.com/index.m3u8',
                                 '#EXTM3U\n#EXTINF:4,\na.ts\n')
        self.mp4(progress=TaskProgress(downloaded_bytes=1024, total_bytes=2048))
        self.live(progress=TaskProgress(downloaded_bytes=1024, recorded_seconds=3661))
        model = self.model()
        for index, expected in enumerate((True, True, False)):
            item = model.ObjectToItem(model._BuildKey((index,)))
            self.assertEqual(model.IsContainer(item), expected)
            children = []
            self.assertEqual(model.GetChildren(item, children), 1 if expected else 0)
        self.assertEqual(model.TaskInfo(1)['progress'], '50% · 1.0 KB / 2.0 KB')
        self.assertEqual(model.TaskInfo(2)['progress'], '01:01:01 · 1.0 KB')
        self.assertIsNone(model.TaskInfo(2)['percent'])

    def test_unknown_size_and_renderer_do_not_invent_percent(self):
        self.mp4(progress=TaskProgress(downloaded_bytes=1234))
        info = self.model().TaskInfo(0)
        self.assertIsNone(info['percent'])
        self.assertIn('未知', info['progress'])
        renderer = TaskProgressRenderer(SimpleNamespace(FromDIP=lambda x: x))
        for label, expected in [('50% · 1/2', 50), (info['progress'], None),
                                 ('00:01:00 · 2.0 MB', None), ('', None)]:
            renderer.SetValue(label)
            self.assertEqual(renderer.percent, expected)

    def test_mp4_action_click_includes_toggle_and_task_dispatch(self):
        self.mp4()
        model = self.model()
        item = model.ObjectToItem(model._BuildKey((0,)))
        frame = SimpleNamespace(FromDIP=lambda x: x, OnTaskAction=Mock(), OnSegmentDownload=Mock())
        renderer = TaskActionRenderer(frame)
        cell = wx.Rect(0, 0, 200, 24)
        actions = model.TaskActions(0)
        self.assertEqual([a['id'] for a in actions], ['start', 'retry', 'delete', 'toggle', 'more'])
        for action, rect in zip(actions, renderer._ActionRects(cell, len(actions))):
            mouse = Mock(GetPosition=Mock(return_value=wx.Point(rect.x + rect.width // 2, 12)))
            self.assertEqual(renderer.ActivateCell(cell, model, item, 4, mouse), action['enabled'])
        self.assertEqual([call.args[1] for call in frame.OnTaskAction.call_args_list], ['start', 'delete', 'toggle', 'more'])
        frame.OnSegmentDownload.assert_not_called()

    def test_manual_mouse_route_converts_coordinates_and_dispatches_once(self):
        self.mp4()
        model = self.model()
        item = model.ObjectToItem(model._BuildKey((0,)))
        cell = wx.Rect(400, 60, 240, 24)
        frame = SimpleNamespace(FromDIP=lambda x: x, model=model,
                               _manualActionClicks=True, OnTaskAction=Mock(), OnSegmentDownload=Mock())
        renderer = frame._actionRenderer = TaskActionRenderer(frame)
        renderer.GetSize = Mock(return_value=wx.Size(cell.width - 8, cell.height))
        column = Mock(GetModelColumn=Mock(return_value=4))
        tree = frame.mcTree = Mock()
        tree.HitTest.return_value = (item, column)
        tree.GetItemRect.return_value = cell
        # 模拟内部窗口与列表相差 20 像素，不能直接使用事件的局部坐标。
        source = Mock()
        source.ClientToScreen.side_effect = lambda p: wx.Point(p.x + 100, p.y + 120)
        tree.ScreenToClient.side_effect = lambda p: wx.Point(p.x - 100, p.y - 100)
        actions = model.TaskActions(0)
        for width in (200, 360, 600, 200):
            cell.width = width
            renderer.GetSize.return_value = wx.Size(width - 8, cell.height)
            content = wx.Rect(cell.x + 4, cell.y, cell.width - 8, cell.height)
            for action, rect in zip(actions, renderer._ActionRects(content, len(actions))):
                for click_x in (rect.x, rect.x + rect.width // 2, rect.x + rect.width - 1):
                    frame.OnTaskAction.reset_mock()
                    event = Mock()
                    event.GetEventObject.return_value = source
                    event.GetPosition.return_value = wx.Point(click_x, rect.y - 20 + 12)
                    with patch('wx.GetMousePosition', side_effect=AssertionError('不应读取实时鼠标')):
                        MainFrame.OnTaskListClick(frame, event)
                        # 即使原生控件又回调，也不能再次执行操作。
                        self.assertFalse(renderer.ActivateCell(cell, model, item, 4, event))
                    event.Skip.assert_called_once_with(False)
                    tree.HitTest.assert_called_with(wx.Point(click_x, rect.y + 12))
                    if action['enabled']:
                        frame.OnTaskAction.assert_called_once_with(item, action['id'])
                    else:
                        frame.OnTaskAction.assert_not_called()
                frame.OnTaskAction.reset_mock()
                self.assertTrue(renderer.ActivateCell(cell, model, item, 4, None))
                frame.OnTaskAction.assert_called_once_with(item, 'start')
                frame.OnSegmentDownload.assert_not_called()

    @unittest.skipUnless(wx.Platform == '__WXMAC__', 'Mac 原生列表鼠标路径')
    def test_mac_native_cells_route_task_and_segment_mouse_clicks(self):
        self.service.create_m3u8(self.root / 'hls', 'https://example.com/index.m3u8',
                                 '#EXTM3U\n#EXTINF:4,\na.ts\n')
        self.mp4()
        frame = MainFrame(None, 'test', self.service, load_tree(self.service))
        try:
            tree = frame.mcTree
            column = tree.GetColumn(6)
            window = tree.GetMainWindow()
            renderer = frame._actionRenderer
            wx.YieldIfNeeded()
            for index in range(2):
                item = frame.model.ObjectToItem(frame.model._BuildKey((index,)))
                cell = tree.GetItemRect(item, column)
                self.assertFalse(cell.IsEmpty())
                actions = frame.model.TaskActions(index)
                with patch.object(frame, 'OnTaskAction') as dispatch:
                    for action, rect in zip(actions, renderer._ActionRects(cell, len(actions))):
                        dispatch.reset_mock()
                        position = wx.Point(rect.x + rect.width // 2, rect.y + rect.height // 2)
                        hit_item, hit_column = tree.HitTest(position)
                        self.assertEqual(hit_item, item)
                        self.assertEqual(hit_column.GetModelColumn(), 4)
                        event = wx.MouseEvent(wx.wxEVT_LEFT_UP)
                        event.SetEventObject(window)
                        event.SetPosition(window.ScreenToClient(tree.ClientToScreen(position)))
                        window.GetEventHandler().ProcessEvent(event)
                        self.assertFalse(event.GetSkipped())
                        renderer.ActivateCell(cell, frame.model, item, 4, event)
                        if action['enabled']:
                            dispatch.assert_called_once_with(item, action['id'])
                        else:
                            dispatch.assert_not_called()
            parent = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
            tree.Expand(parent)
            wx.YieldIfNeeded()
            child = frame.model.ObjectToItem(frame.model._BuildKey((0, 0)))
            cell = tree.GetItemRect(child, column)
            self.assertFalse(cell.IsEmpty())
            event = wx.MouseEvent(wx.wxEVT_LEFT_UP)
            event.SetEventObject(window)
            event.SetPosition(window.ScreenToClient(tree.ClientToScreen(
                wx.Point(cell.x + cell.width // 2, cell.y + cell.height // 2))))
            with patch.object(frame, 'OnSegmentDownload') as dispatch:
                window.GetEventHandler().ProcessEvent(event)
                dispatch.assert_called_once_with(child)
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    @unittest.skipUnless(wx.Platform == '__WXMAC__', 'Mac 原生弹窗事件路径')
    def test_mac_mouse_release_opens_more_and_delete_after_event_returns(self):
        record = self.mp4()
        frame = MainFrame(None, 'test', self.service, load_tree(self.service))
        try:
            tree = frame.mcTree
            window = tree.GetMainWindow()
            wx.YieldIfNeeded()
            item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
            cell = tree.GetItemRect(item, tree.GetColumn(6))
            actions = frame.model.TaskActions(0)

            def release(action_id):
                index = next(i for i, action in enumerate(actions) if action['id'] == action_id)
                rect = frame._actionRenderer._ActionRects(cell, len(actions))[index]
                event = wx.MouseEvent(wx.wxEVT_LEFT_UP)
                event.SetEventObject(window)
                event.SetPosition(window.ScreenToClient(tree.ClientToScreen(
                    wx.Point(rect.x + rect.width // 2, rect.y + rect.height // 2))))
                window.GetEventHandler().ProcessEvent(event)
                self.assertFalse(event.GetSkipped())

            def inspect_menu(menu):
                self.assertEqual([entry.GetItemLabelText() for entry in menu.GetMenuItems()],
                                 ['打开文件夹', '播放视频', '另存为…'])

            with patch.object(tree, 'PopupMenu', side_effect=inspect_menu) as menu:
                release('more')
                menu.assert_not_called()
                self.app.ProcessPendingEvents()
                menu.assert_called_once()
            with patch('wx.MessageDialog') as dialog:
                dialog.return_value.ShowModal.return_value = wx.ID_NO
                release('delete')
                dialog.assert_not_called()
                self.app.ProcessPendingEvents()
                dialog.return_value.ShowModal.assert_called_once()
                self.assertIsNotNone(self.repo.get(record.id))
            with patch('wx.MessageDialog') as dialog:
                dialog.return_value.ShowModal.return_value = wx.ID_YES
                release('delete')
                self.app.ProcessPendingEvents()
                dialog.return_value.ShowModal.assert_called_once()
                self.assertIsNone(self.repo.get(record.id))
                self.assertFalse(frame.model.fileTree.items)
        finally:
            frame.Destroy()
            self.app.ProcessPendingEvents()

    @unittest.skipUnless(wx.Platform == '__WXMAC__', 'Mac 原生列表首次布局')
    def test_mac_action_width_survives_first_show_without_window_resize(self):
        # 首次进入 Cocoa 主循环使用独立进程，避免其他测试待销毁窗口的影响。
        if os.environ.get('AVDOWNLOADER_TEST_NATIVE_LAYOUT') != '1':
            result = subprocess.run(
                [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                 '-p', 'test_task_types_ui.py', '-k',
                 'test_mac_action_width_survives_first_show_without_window_resize', '-v'],
                cwd=Path(__file__).resolve().parents[1],
                env=dict(os.environ, AVDOWNLOADER_TEST_NATIVE_LAYOUT='1'),
                capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return
        self.service.create_m3u8(self.root / 'hls', 'https://example.com/index.m3u8',
                                 '#EXTM3U\n#EXTINF:4,\na.ts\n')
        frame = MainFrame(None, 'test', self.service, load_tree(self.service))
        measurements = []
        timers = []
        try:
            tree = frame.mcTree
            item = frame.model.ObjectToItem(frame.model._BuildKey((0,)))
            dc = wx.ClientDC(tree)
            dc.SetFont(tree.GetFont())
            required = frame._actionRenderer.MinimumWidth(dc)
            initial_size = frame.GetSize()

            def measure():
                widths = [tree.GetColumn(i).GetWidth() for i in range(7)]
                measurements.append((widths, tree.GetItemRect(item, tree.GetColumn(6)).width,
                                     tree.GetClientSize().width, frame.GetSize()))

            measure()
            # 真正运行原生事件循环，覆盖 Show 后的 Cocoa 布局；整个过程不调整窗口大小。
            timers = [wx.CallLater(50, measure), wx.CallLater(150, measure),
                      wx.CallLater(200, self.app.ExitMainLoop)]
            self.app.MainLoop()
            self.assertEqual(len(measurements), 3)
            self.assertGreaterEqual(tree.GetColumn(6).GetMinWidth(), required)
            for widths, visible_width, available, size in measurements:
                self.assertEqual(size, initial_size)
                self.assertGreaterEqual(widths[6], required)
                self.assertGreaterEqual(visible_width, required)
                self.assertLessEqual(sum(widths), available)
        finally:
            for timer in timers:
                timer.Stop()
            frame.Destroy()
            self.app.ProcessPendingEvents()

    def test_restart_recovers_live_and_mp4_without_losing_progress(self):
        mp4 = self.mp4(status=TaskStatus.DOWNLOADING, progress=TaskProgress(downloaded_bytes=100))
        live = self.live(status=TaskStatus.RECORDING, progress=TaskProgress(recorded_seconds=42))
        load_tree(self.service)
        for task in (mp4, live):
            saved = self.repo.get(task.id)
            self.assertEqual(saved.status, TaskStatus.INTERRUPTED)
            self.assertEqual(saved.progress, task.progress)
        self.repo.mutate(live.id, lambda task: setattr(task, 'status', TaskStatus.RECORDING))
        tree = load_tree(self.service)
        self.assertEqual(next(row for row in tree.items if row.task_id == live.id).task_status, TaskStatus.RECORDING)
        model = self.model()
        index = next(i for i, row in enumerate(model.fileTree.items) if row.task_id == live.id)
        self.assertEqual(model.TaskInfo(index)['status'], '录制中')
        self.assertFalse(next(a for a in model.TaskActions(index) if a['id'] == 'delete')['enabled'])

    def test_existing_unfinished_file_is_not_treated_as_completed(self):
        task = self.mp4()
        target = task.save_dir / task.details.target_path
        target.parent.mkdir(parents=True)
        target.write_bytes(b'partial')
        row = load_tree(self.service).items[0]
        self.assertEqual(row.task_status, TaskStatus.NEW)
        self.assertEqual(row.outputs[0].displayName, 'video.part')
        self.assertEqual(row.outputs[0].fileSize, '-')
        self.repo.mutate(task.id, lambda task: setattr(task, 'status', TaskStatus.COMPLETED))
        row = load_tree(self.service).items[0]
        self.assertEqual(len(row.outputs), 1)
        self.assertEqual(row.save_dir, task.save_dir)
        target.unlink()
        self.assertEqual(load_tree(self.service).items[0].task_status, TaskStatus.INTERRUPTED)

    def test_mp4_child_tracks_partial_and_completed_file(self):
        task = self.mp4(status=TaskStatus.PAUSED)
        task.save_dir.mkdir()
        partial = task.save_dir / task.details.temporary_path
        partial.write_bytes(b'video')
        model = self.model()
        child = model.ObjectToItem(model._BuildKey((0, 0)))
        self.assertEqual(model.GetValue(child, 0), '1.1')
        self.assertEqual(model.GetValue(child, 1), 'video.part')
        self.assertEqual(model.GetValue(child, 2), '5.00 B')
        self.assertEqual(model.GetValue(child, 6), '已暂停')
        self.assertEqual(model.GetValue(child, 5), '')
        target = task.save_dir / task.details.target_path
        target.parent.mkdir()
        partial.rename(target)
        self.repo.mutate(task.id, lambda current: setattr(current, 'status', TaskStatus.COMPLETED))
        model.ApplyTaskRecord(self.repo.get(task.id))
        self.assertEqual(model.GetValue(child, 1), 'nested/video.mp4')
        self.assertEqual(model.GetValue(child, 6), '已完成')
        self.assertEqual(model.GetValue(child, 5), '')
        self.assertEqual(model.GetValue(child, 4), '')

    def test_segment_and_merge_operations_reject_single_file_tasks(self):
        for task in (self.mp4(), self.live()):
            for operation in (lambda: self.service.begin_download(task.id, [0]),
                              lambda: self.service.finish_segment(task.id, 0, 'unused', True),
                              lambda: self.service.begin_merge(task.id),
                              lambda: self.service.finish_merge(task.id, 'unused', True)):
                with self.assertRaisesRegex(ValueError, '仅适用于 M3U8'):
                    operation()
            self.assertEqual(self.repo.get(task.id).status, TaskStatus.NEW)

    def test_save_as_dialog_copies_file_and_cancel_leaves_destination_unchanged(self):
        source = self.root / 'source.mp4'
        source.write_bytes(b'complete video')
        directory = self.root / 'export'
        directory.mkdir()
        destination = directory / 'renamed.mp4'
        frame = SimpleNamespace()
        with patch('src.views.main_frame.wx.FileDialog') as dialog, \
                patch('src.views.main_frame.wx.ProgressDialog') as progress, \
                patch('src.views.main_frame.wx.MessageBox') as message:
            dialog.return_value.ShowModal.return_value = wx.ID_OK
            dialog.return_value.GetPath.return_value = str(destination)
            MainFrame._SaveFileAs(frame, source)
            self.assertEqual(destination.read_bytes(), b'complete video')
            self.assertEqual(source.read_bytes(), b'complete video')
            self.assertTrue(dialog.call_args.kwargs['style'] & wx.FD_OVERWRITE_PROMPT)
            dialog.return_value.Destroy.assert_called_once()
            progress.return_value.Destroy.assert_called_once()
            message.assert_not_called()
            progress.reset_mock()
            dialog.return_value.ShowModal.return_value = wx.ID_CANCEL
            source.write_bytes(b'new video')
            MainFrame._SaveFileAs(frame, source)
            self.assertEqual(destination.read_bytes(), b'complete video')
            progress.assert_not_called()

    def test_completed_video_menu_exports_exact_output_path(self):
        task = self.mp4(status=TaskStatus.COMPLETED)
        target = task.save_dir / task.details.target_path
        target.parent.mkdir(parents=True)
        target.write_bytes(b'video')
        model = self.model()
        item = model.ObjectToItem(model._BuildKey((0,)))
        export = Mock()
        def select_save(menu):
            entry = next(entry for entry in menu.GetMenuItems()
                         if entry.GetItemLabelText() == '另存为…')
            self.assertTrue(entry.IsEnabled())
            menu.ProcessEvent(wx.CommandEvent(wx.EVT_MENU.typeId, entry.GetId()))
        frame = SimpleNamespace(model=model, mcTree=Mock(PopupMenu=select_save),
                                _SaveFileAs=export, _OpenLocalPath=Mock())
        MainFrame.OnTaskMenu(frame, item)
        export.assert_called_once_with(target)

    def test_single_file_menu_and_dispatch_do_not_offer_merge(self):
        task = self.mp4()
        model = self.model()
        item = model.ObjectToItem(model._BuildKey((0,)))
        opened = Mock()
        def inspect_menu(menu):
            labels = [entry.GetItemLabelText() for entry in menu.GetMenuItems()]
            self.assertEqual(labels, ['打开文件夹', '播放视频', '另存为…'])
            entry = menu.GetMenuItems()[0]
            menu.ProcessEvent(wx.CommandEvent(wx.EVT_MENU.typeId, entry.GetId()))
        frame = SimpleNamespace(model=model, mcTree=Mock(PopupMenu=inspect_menu),
                                _OpenLocalPath=opened, _CreateMP4File=Mock(), _DownloadFiles=Mock(),
                                runner=Mock(), _completion_notified=set(), _SyncDownloads=Mock(),
                                _SetTaskExpanded=Mock())
        MainFrame.OnTaskMenu(frame, item)
        opened.assert_called_once_with(task.save_dir)
        for action in ('merge', 'start', 'retry', 'toggle'):
            MainFrame.OnTaskAction(frame, item, action)
        frame._CreateMP4File.assert_not_called()
        frame._DownloadFiles.assert_not_called()
        frame.runner.activate.assert_called_once_with(task.id, retry=False)
