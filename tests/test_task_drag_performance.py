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


def test_move_shows_status_before_work_and_reports_completion():
    from src.views.controllers.task_actions_controller import TaskActionsController
    frame = Mock()
    frame._groupFilter = 'all'

    def move(*_args):
        frame._SetPrimaryStatusText.assert_called_once_with('正在移动任务，请稍候…')
        frame.statusBar.Update.assert_called_once()

    frame.runner.move_task_to_group.side_effect = move
    with patch('src.views.controllers.task_actions_controller.wx.BusyCursor'):
        TaskActionsController(frame)._MoveTaskToGroup('task', 'target')
    frame._SetPrimaryStatusText.assert_called_with('任务移动完成')


def test_move_failure_sets_status_and_shows_error():
    from src.views.controllers.task_actions_controller import TaskActionsController
    frame = Mock()
    frame.runner.move_task_to_group.side_effect = OSError('disk unavailable')
    with patch('src.views.controllers.task_actions_controller.wx.BusyCursor'), patch(
            'src.views.controllers.task_actions_controller.wx.MessageBox') as message:
        TaskActionsController(frame)._MoveTaskToGroup('task', 'target')
    frame._SetPrimaryStatusText.assert_called_with('移动任务失败：disk unavailable')
    assert 'disk unavailable' in message.call_args.args[0]
    frame.OnRefresh.assert_not_called()
