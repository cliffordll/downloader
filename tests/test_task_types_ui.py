from src.models.tree_model import load_tree
"""混合类型任务的列表投影及事件分发；不请求网络、不修改用户任务库。"""
from pathlib import Path
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
        with patch('src.models.tree_model.TaskService', return_value=self.service):
            return MultiColumnTreeModel()

    def test_mixed_list_has_children_only_for_m3u8(self):
        self.service.create_m3u8(self.root / 'hls', 'https://example.com/index.m3u8',
                                 '#EXTM3U\n#EXTINF:4,\na.ts\n')
        self.mp4(progress=TaskProgress(downloaded_bytes=1024, total_bytes=2048))
        self.live(progress=TaskProgress(downloaded_bytes=1024, recorded_seconds=3661))
        model = self.model()
        for index, expected in enumerate((True, False, False)):
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

    def test_leaf_action_click_uses_four_regions_and_task_dispatch(self):
        self.mp4()
        model = self.model()
        item = model.ObjectToItem(model._BuildKey((0,)))
        frame = SimpleNamespace(FromDIP=lambda x: x, OnTaskAction=Mock(), OnSegmentDownload=Mock())
        renderer = TaskActionRenderer(frame)
        cell = wx.Rect(0, 0, 200, 24)
        actions = model.TaskActions(0)
        self.assertEqual([a['id'] for a in actions], ['start', 'retry', 'delete', 'more'])
        for action, rect in zip(actions, renderer._ActionRects(cell, len(actions))):
            mouse = Mock(GetPosition=Mock(return_value=wx.Point(rect.x + rect.width // 2, 12)))
            self.assertEqual(renderer.ActivateCell(cell, model, item, 4, mouse), action['enabled'])
        self.assertEqual([call.args[1] for call in frame.OnTaskAction.call_args_list], ['start', 'delete', 'more'])
        frame.OnSegmentDownload.assert_not_called()

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
        self.assertEqual(tree.items[1].task_status, TaskStatus.RECORDING)
        model = self.model()
        self.assertEqual(model.TaskInfo(1)['status'], '录制中')
        self.assertFalse(next(a for a in model.TaskActions(1) if a['id'] == 'delete')['enabled'])

    def test_existing_unfinished_file_is_not_treated_as_completed(self):
        task = self.mp4()
        target = task.save_dir / task.details.target_path
        target.parent.mkdir(parents=True)
        target.write_bytes(b'partial')
        row = load_tree(self.service).items[0]
        self.assertEqual(row.task_status, TaskStatus.NEW)
        self.assertFalse(row.outputs)
        self.repo.mutate(task.id, lambda task: setattr(task, 'status', TaskStatus.COMPLETED))
        row = load_tree(self.service).items[0]
        self.assertEqual(len(row.outputs), 1)
        self.assertEqual(row.save_dir, task.save_dir)
        target.unlink()
        self.assertEqual(load_tree(self.service).items[0].task_status, TaskStatus.INTERRUPTED)

    def test_segment_and_merge_operations_reject_single_file_tasks(self):
        for task in (self.mp4(), self.live()):
            for operation in (lambda: self.service.begin_download(task.id, [0]),
                              lambda: self.service.finish_segment(task.id, 0, 'unused', True),
                              lambda: self.service.begin_merge(task.id),
                              lambda: self.service.finish_merge(task.id, 'unused', True)):
                with self.assertRaisesRegex(ValueError, '仅适用于 M3U8'):
                    operation()
            self.assertEqual(self.repo.get(task.id).status, TaskStatus.NEW)

    def test_single_file_menu_and_dispatch_do_not_offer_merge(self):
        task = self.mp4()
        model = self.model()
        item = model.ObjectToItem(model._BuildKey((0,)))
        opened = Mock()
        def inspect_menu(menu):
            labels = [entry.GetItemLabelText() for entry in menu.GetMenuItems()]
            self.assertEqual(labels, ['打开文件夹', '播放视频'])
            entry = menu.GetMenuItems()[0]
            menu.ProcessEvent(wx.CommandEvent(wx.EVT_MENU.typeId, entry.GetId()))
        frame = SimpleNamespace(model=model, mcTree=Mock(PopupMenu=inspect_menu),
                                _OpenLocalPath=opened, _CreateMP4File=Mock(), _DownloadFiles=Mock(),
                                mp4=Mock(busy=Mock(return_value=False)), _StartMP4=Mock(), _SyncMP4=Mock())
        MainFrame.OnTaskMenu(frame, item)
        opened.assert_called_once_with(task.save_dir)
        for action in ('merge', 'start', 'retry', 'toggle'):
            MainFrame.OnTaskAction(frame, item, action)
        frame._CreateMP4File.assert_not_called()
        frame._DownloadFiles.assert_not_called()
        frame._StartMP4.assert_called_once_with(task.id)
