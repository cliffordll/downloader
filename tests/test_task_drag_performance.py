"""拖动时暂缓进度计算，结束后合并重绘。"""
from unittest.mock import Mock, patch

import pytest

from src.views.controllers.download_controller import DownloadController
from src.views.controllers.task_list_controller import TaskListController


def test_drag_defers_progress_and_resumes_after_mouse_release():
    frame = Mock()
    frame._closing = False
    frame.task_list._task_drag_active = True
    controller = DownloadController(frame)
    with patch('src.views.controllers.download_controller.wx.GetMouseState') as mouse:
        mouse.return_value.LeftIsDown.return_value = True
        controller.OnTaskProgress(None)
        frame._SyncDownloads.assert_not_called()
        # 即使取消拖放没有清理标志，松手后也必须恢复同步。
        mouse.return_value.LeftIsDown.return_value = False
        frame._SyncDownloads.side_effect = RuntimeError('sync reached')
        with pytest.raises(RuntimeError, match='sync reached'):
            controller.OnTaskProgress(None)
        frame._SyncDownloads.assert_called_once()


def test_drag_repaint_is_coalesced_without_synchronous_update():
    frame = Mock()
    frame._closing = False
    frame.IsBeingDeleted.return_value = False
    controller = TaskListController(frame)
    with patch('src.views.controllers.task_list_controller.wx.CallLater') as later:
        controller._ScheduleTaskDragRefresh()
        controller._ScheduleTaskDragRefresh()
        later.return_value.Stop.assert_called_once()
        assert later.call_count == 2
    controller._RefreshAfterTaskDrag()
    frame.mcTree.GetMainWindow.return_value.Refresh.assert_called_once_with(True)
    frame.mcTree.Update.assert_not_called()
    frame.mcTree.GetMainWindow.return_value.Update.assert_not_called()


def test_drop_defers_model_changes_until_native_callback_returns():
    frame = Mock()
    frame._closing = False
    frame.IsBeingDeleted.return_value = False
    controller = TaskListController(frame)
    controller._dragged_task_id = 'task'
    controller._dragged_source_group_id = 'source'
    controller._drag_payload = 'task:nonce'
    event = Mock()
    event.GetDataBuffer.return_value = b'task:nonce'
    event.GetDataSize.return_value = len(b'task:nonce')
    with patch.object(controller, '_DropTargetGroup', return_value=(True, 'target')), patch(
            'src.views.controllers.task_list_controller.wx.CallAfter') as later:
        controller.OnTaskDrop(event)
        frame.task_actions._MoveTaskToGroup.assert_not_called()
        later.assert_called_once_with(controller._MoveTaskAfterDrop, 'task', 'target')
    with patch.object(controller, '_ScheduleTaskDragRefresh') as refresh:
        controller._MoveTaskAfterDrop('task', 'target')
        frame.task_actions._MoveTaskToGroup.assert_called_once_with('task', 'target')
        refresh.assert_called_once()


def test_deferred_drop_does_not_access_closed_window():
    frame = Mock()
    frame._closing = True
    controller = TaskListController(frame)
    controller._MoveTaskAfterDrop('task', 'target')
    frame.task_actions._MoveTaskToGroup.assert_not_called()


def move_frame():
    from src.views.controllers.task_actions_controller import TaskActionsController
    frame = Mock()
    frame._closing = False
    frame.IsBeingDeleted.return_value = False
    frame._groupFilter = 'all'
    frame.task_actions = TaskActionsController(frame)
    return frame


def test_move_returns_while_worker_is_blocked_and_finishes_on_ui_thread():
    import threading
    frame = move_frame()
    started, release, dispatched = threading.Event(), threading.Event(), threading.Event()
    callbacks = []

    def move(*_args):
        started.set()
        assert release.wait(5)

    def enqueue(*args):
        callbacks.append(args)
        dispatched.set()

    frame.runner.move_task_to_group.side_effect = move
    with patch('src.views.controllers.task_actions_controller.wx.BusyCursor'), patch(
            'src.views.controllers.task_actions_controller.wx.CallAfter', side_effect=enqueue):
        try:
            frame.task_actions._MoveTaskToGroup('task', 'target')
            assert started.wait(2)
            assert not dispatched.is_set()
            frame.OnRefresh.assert_not_called()
            frame._SetPrimaryStatusText.assert_called_with('正在移动任务，请稍候…')
            frame.Disable.assert_called_once()
            frame.task_actions._MoveTaskToGroup('task', 'target')
            frame.runner.move_task_to_group.assert_called_once()
        finally:
            release.set()
            assert dispatched.wait(2)
    callback, *args = callbacks[0]
    callback(*args)
    frame.OnRefresh.assert_called_once()
    frame._SetPrimaryStatusText.assert_called_with('任务移动完成')
    frame.mcTree.Thaw.assert_called_once()
    frame.Enable.assert_called_once()
    assert not frame.task_actions._group_move_pending


def test_move_failure_sets_status_and_restores_controls_before_error():
    frame = move_frame()
    controller = frame.task_actions
    with patch('src.views.controllers.task_actions_controller.Thread') as worker, patch(
            'src.views.controllers.task_actions_controller.wx.BusyCursor'), patch(
            'src.views.controllers.task_actions_controller.wx.CallAfter') as later:
        controller._MoveTaskToGroup('task', 'target')
        frame.runner.move_task_to_group.side_effect = OSError('disk unavailable')
        worker.call_args.kwargs['target']()
    callback, *args = later.call_args.args

    def show_error(*_args, **_kwargs):
        frame.Enable.assert_called_once()
        assert not controller._group_move_pending

    with patch('src.views.controllers.task_actions_controller.wx.MessageBox', side_effect=show_error):
        callback(*args)
    frame._SetPrimaryStatusText.assert_called_with('移动任务失败：disk unavailable')
    frame.OnRefresh.assert_not_called()
    frame.mcTree.Thaw.assert_called_once()


def test_worker_start_failure_restores_interface():
    frame = move_frame()
    with patch('src.views.controllers.task_actions_controller.Thread') as worker, patch(
            'src.views.controllers.task_actions_controller.wx.BusyCursor'), patch(
            'src.views.controllers.task_actions_controller.wx.MessageBox'):
        worker.return_value.start.side_effect = RuntimeError('cannot start')
        frame.task_actions._MoveTaskToGroup('task', 'target')
    frame.Enable.assert_called_once()
    assert not frame.task_actions._group_move_pending


def test_close_waits_for_move_and_late_completion_avoids_destroyed_controls():
    from src.views.main_frame import MainFrame
    frame = move_frame()
    frame.task_actions._group_move_pending = True
    event = Mock()
    event.CanVeto.return_value = True
    MainFrame.OnClose(frame, event)
    event.Veto.assert_called_once()
    frame._PrepareClose.assert_not_called()
    frame._closing = True
    frame.task_actions._FinishGroupMove('target', None)
    frame.Enable.assert_not_called()
    frame.OnRefresh.assert_not_called()


def test_progress_and_toolbar_updates_do_not_wait_on_move_lock():
    frame = move_frame()
    frame.task_actions._group_move_pending = True
    controller = DownloadController(frame)
    controller.OnTaskProgress(None)
    frame._SyncDownloads.assert_not_called()
    for update in (controller.OnUpdateGlobalDownloadAction, controller.OnUpdatePauseDownloads):
        event = Mock()
        update(event)
        event.Enable.assert_called_once_with(False)
    frame.runner.assert_not_called()


def test_native_frame_keeps_processing_events_while_runner_is_locked(tmp_path, monkeypatch):
    import threading
    import time
    import wx
    from src.core.sys_setting import SysSetting
    from src.core.task_service import TaskService
    from src.models.tree_model import load_tree
    from src.storage.task_repository import TaskRepository
    from src.views.main_frame import MainFrame

    app = wx.GetApp() or wx.App(False)
    monkeypatch.setattr(SysSetting, '_values', dict(SysSetting.Defaults(), download_dir=str(tmp_path)))
    monkeypatch.setattr(SysSetting, 'ConfigPath', lambda: tmp_path / 'settings.json')
    monkeypatch.setattr('src.views.main_frame.pairing_path', lambda: tmp_path / 'no-pairing')
    service = TaskService(TaskRepository(tmp_path / 'tasks.db'))
    task = service.create_m3u8(tmp_path / 'source', 'https://example.com/index.m3u8',
                              '#EXTM3U\n#EXTINF:4,\na.ts\n', filename='video')
    group = service.repository.create_group('target', tmp_path / 'target')
    with patch.object(MainFrame, 'Show'):
        frame = MainFrame(None, 'async move test', service, load_tree(service))
    started, release = threading.Event(), threading.Event()
    move = frame.runner.move_task_to_group

    def blocked_move(*args):
        with frame.runner._lock:
            started.set()
            assert release.wait(5)
            return move(*args)

    monkeypatch.setattr(frame.runner, 'move_task_to_group', blocked_move)
    watchdog = threading.Timer(3, release.set)
    watchdog.start()
    try:
        frame.task_actions._MoveTaskToGroup(task.id, group.id)
        assert started.wait(1)
        before = time.monotonic()
        frame.OnTaskProgress(None)
        frame.task_list.OnSearchTimer(None)
        frame._FitTaskColumns()
        for handler in (frame.OnUpdateGlobalDownloadAction, frame.OnUpdatePauseDownloads):
            handler(wx.UpdateUIEvent())
        heartbeat = []
        wx.CallAfter(heartbeat.append, True)
        app.Yield()
        assert heartbeat == [True]
        assert time.monotonic() - before < 1
        release.set()
        deadline = time.monotonic() + 5
        while frame.task_actions._group_move_pending and time.monotonic() < deadline:
            app.Yield()
            time.sleep(0.01)
        assert not frame.task_actions._group_move_pending
        assert frame.IsEnabled()
        assert not frame.mcTree.IsFrozen()
        assert service.repository.get(task.id).group_id == group.id
    finally:
        release.set()
        watchdog.cancel()
        # 出错时也先让工作线程释放 runner 锁，再销毁原生窗口。
        deadline = time.monotonic() + 5
        while frame.task_actions._group_move_pending and time.monotonic() < deadline:
            app.Yield()
            time.sleep(0.01)
        frame.Destroy()
        app.ProcessPendingEvents()
