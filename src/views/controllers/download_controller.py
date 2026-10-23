"""下载状态同步、任务进度和暂停/继续操作的界面控制器。"""

import json
import sqlite3
from queue import Empty

import wx

from src.media.m3u8.m3u8_downloader import M3U8Downloader
from src.schemas.task import TaskStatus, TaskType
from src.storage.task_repository import TaskDataError


class DownloadController:
    """同步 TaskRunner 状态到主列表并处理全局下载控制。"""

    def __init__(self, frame):
        self.frame = frame

    def OnTaskProgress(self, event, notify=True):
        """启动只建立缓存；后续只刷新变化的单元格，不反复使整行和全部表头失效。"""
        if self.frame._closing:
            return
        self.frame._SyncDownloads()
        self.frame._CheckBrowserInbox()
        display = {}
        child_display = {}
        file_actions = {}
        snapshot = M3U8Downloader.Snapshot()
        for index, task in enumerate(self.frame.model.fileTree.items):
            info = self.frame.model.TaskInfo(index)
            state = (info['progress'], info['status'], json.dumps(self.frame.model.TaskActions(index), ensure_ascii=False))
            display[task.parent.fileName] = state
            previous = self.frame._task_display.get(task.parent.fileName)
            if notify and previous != state:
                item = self.frame.model.ObjectToItem(self.frame.model._BuildKey((index,)))
                for field, column in enumerate((5, 6, 4)):
                    if previous is None or previous[field] != state[field]:
                        self.frame.model.ValueChanged(item, column)
            root = self.frame.model.ObjectToItem(self.frame.model._BuildKey((index,)))
            if self.frame.mcTree.IsExpanded(root):
                for file_index in [-4, *range(len(task.outputs))]:
                    actions = self.frame.model.FileActions(index, file_index)
                    if not actions:
                        continue
                    key = (task.task_id or task.parent.fileName, file_index)
                    file_actions[key] = json.dumps(actions, ensure_ascii=False)
                    if notify and getattr(self.frame, '_file_actions_display', {}).get(key) != file_actions[key]:
                        node = self.frame.model.ObjectToItem(self.frame.model._BuildKey((index, file_index)))
                        self.frame.model.ValueChanged(node, 4)
                for child_index in range(len(task.outputs) + (0 if task.work_dir_missing else len(task.childs))):
                    folder = self.frame.model.ObjectToItem(self.frame.model._BuildKey((index, -1)))
                    if child_index >= len(task.outputs) and not self.frame.mcTree.IsExpanded(folder):
                        continue
                    key = (task.parent.fileName, child_index)
                    child_status = self.frame.model.ChildStatus(index, child_index, snapshot)
                    child_display[key] = child_status
                    if notify and getattr(self.frame, '_child_status_display', {}).get(key) != child_status:
                        child = self.frame.model.ObjectToItem(self.frame.model._BuildKey((index, child_index)))
                        self.frame.model.ValueChanged(child, 6)
        status_changed = any(self.frame._task_display.get(key, ('', ''))[1] != state[1]
                             for key, state in display.items())
        self.frame._task_display = display
        self.frame._child_status_display = child_display
        self.frame._file_actions_display = file_actions
        if status_changed and self.frame.statusFilter.GetSelection() > 0:
            self.frame._ApplyTaskFilter()

    def _StartMP4(self, task_id):
        try:
            self.frame.runner.start(task_id)
        except (ValueError, OSError, RuntimeError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f'无法启动 MP4 下载：{error}', '下载失败', parent=self.frame)
        self.frame._SyncDownloads()

    def _SyncDownloads(self):
        """只消费协调对象的通知；窗口负责展示结果，不调度线程或选择下载器。"""
        while True:
            try:
                task_id, error = self.frame.runner.duration_results.get_nowait()
            except Empty:
                break
            self.frame._DurationChecked(task_id, None, error)
        structure_changed = False
        restored_tasks = set()
        deferred_m3u8 = []
        for task_id in self.frame.runner.changes():
            try:
                record = self.frame.tasks.repository.get(task_id)
                if record is None:
                    continue  # 删除后的迟到通知不能重新添加任务。
                previous = next((task for task in self.frame.model.fileTree.items if task.task_id == task_id), None)
                index = self.frame.model.ApplyTaskRecord(record)
                if index is None:
                    continue
                current = self.frame.model.fileTree.items[index]
                task_structure_changed = previous is not None and (
                    len(previous.outputs) != len(current.outputs)
                    or previous.work_dir_missing != current.work_dir_missing
                    or previous.manifests.keys() != current.manifests.keys())
                structure_changed = structure_changed or task_structure_changed
                if previous is not None and previous.work_dir_missing and not current.work_dir_missing:
                    restored_tasks.add(task_id)
                if task_structure_changed:
                    # 新节点尚未进入原生控件，必须先重建，不能提前发送 ItemChanged。
                    if record.type == TaskType.M3U8:
                        deferred_m3u8.append(task_id)
                    continue
                if record.type == TaskType.M3U8:
                    # 只刷新变化的分片，不因每次状态变化重绘整棵树。
                    visible = self.frame.model.visible_tasks is None or index in self.frame.model.visible_tasks
                    expanded = visible and not current.work_dir_missing and all(
                        self.frame.mcTree.IsExpanded(self.frame.model.ObjectToItem(self.frame.model._BuildKey(key)))
                        for key in ((index,), (index, -4), (index, -1)))
                    for offset, child in enumerate(current.childs if expanded else ()):
                        old = previous.childs[offset] if previous and offset < len(previous.childs) else None
                        if old is None or (old.fileSize, old.status) != (child.fileSize, child.status):
                            item = self.frame.model.ObjectToItem(self.frame.model._BuildKey((index, len(current.outputs) + offset)))
                            self.frame.model.ItemChanged(item)
                    self.frame._M3U8Changed(task_id)
                elif record.type == TaskType.MP4:
                    root = self.frame.model.ObjectToItem(self.frame.model._BuildKey((index,)))
                    if current.outputs and self.frame.mcTree.IsExpanded(root):
                        self.frame.model.ItemChanged(self.frame.model.ObjectToItem(self.frame.model._BuildKey((index, 0))))
                    self.frame.model.ItemChanged(self.frame.model.ObjectToItem(self.frame.model._BuildKey((index,))))
                elif record.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.PAUSED):
                    self.frame.model.ItemChanged(self.frame.model.ObjectToItem(self.frame.model._BuildKey((index,))))
            except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
                self.frame._SetPrimaryStatusText(f'读取下载进度失败：{error}')
        if structure_changed:
            # 合并完成会新增 MP4 子节点，保留展开状态后重建树，不能只重绘单元格。
            self.frame._RefreshWithState()
            for index, task in enumerate(self.frame.model.fileTree.items):
                if task.task_id in restored_tasks and (
                        self.frame.model.visible_tasks is None or index in self.frame.model.visible_tasks):
                    self.frame._SetTaskExpanded(self.frame.model.ObjectToItem(self.frame.model._BuildKey((index,))), True)
            for task_id in deferred_m3u8:
                self.frame._M3U8Changed(task_id)
        while True:
            try:
                error = self.frame.runner.errors.get_nowait()
            except Empty:
                break
            wx.MessageBox(error, '保存失败', parent=self.frame)

    def _GlobalDownloadActions(self):
        """固定菜单项各自判定可用状态；混合运行/暂停时两项均可用。"""
        snapshot = M3U8Downloader.Snapshot()
        can_pause = not snapshot['paused'] and bool(snapshot['pending'] - snapshot['paused_files'])
        tasks = self.frame.model.fileTree.items if hasattr(self.frame, 'model') else []
        can_pause = can_pause or any(task.task_type == TaskType.MP4 and task.task_status in
                                    (TaskStatus.QUEUED, TaskStatus.DOWNLOADING) for task in tasks)
        can_resume = snapshot['paused'] or bool(snapshot['pending'] & snapshot['paused_files']) or any(
            task.task_type == TaskType.M3U8 and task.task_status in (TaskStatus.PAUSED, TaskStatus.INTERRUPTED, TaskStatus.PARTIAL)
            and any(child.fileSize == '-' for child in task.childs) for task in tasks)
        can_resume = can_resume or any(task.task_type == TaskType.MP4 and task.task_status in
                                      (TaskStatus.PAUSED, TaskStatus.INTERRUPTED) for task in tasks)
        return can_pause, can_resume

    def OnPauseDownloads(self, event):
        # 工具栏维持单按钮；所有待处理任务都已单独暂停时，也应显示并执行继续。
        can_pause, can_resume = self.frame._GlobalDownloadActions()
        if can_pause:
            self.frame.OnPauseAllDownloads(event)
        elif can_resume:
            self.frame.OnResumeAllDownloads(event)

    def OnUpdateGlobalDownloadAction(self, event):
        can_pause, can_resume = self.frame._GlobalDownloadActions()
        if event.GetId() == self.frame.pauseAllItem.GetId():
            event.Enable(can_pause)
        else:
            event.Enable(can_resume)

    def OnUpdatePauseDownloads(self, event):
        paused = M3U8Downloader.IsPaused()
        busy = M3U8Downloader.IsBusy() or any(task.task_type == TaskType.MP4 and
            self.frame.runner.mp4.busy(task.task_id) for task in self.frame.model.fileTree.items)
        can_pause, can_resume = self.frame._GlobalDownloadActions()
        event.Enable(can_pause or can_resume)
        self.frame._UpdatePauseTool()
        snapshot = M3U8Downloader.Snapshot()
        self.frame._SetPrimaryStatusText(('暂停中' if snapshot['requesting'] else '已暂停') if paused
                                   else ('正在下载' if busy else '就绪'))

    def _UpdatePauseTool(self):
        """菜单和工具栏共用下载状态；状态未变时不重复设置位图，避免工具栏闪动。"""
        if self.frame.toolBar is None:
            return
        can_pause, can_resume = self.frame._GlobalDownloadActions()
        paused = not can_pause and can_resume
        enabled = can_pause or can_resume
        if self.frame._pauseToolState == (paused, enabled):
            return
        label = '全部继续' if paused else '全部暂停'
        tool_id = self.frame._pauseTool.GetId()
        self.frame.toolBar.EnableTool(tool_id, enabled)
        if self.frame._pauseToolState is None or self.frame._pauseToolState[0] != paused:
            self.frame.toolBar.SetToolNormalBitmap(tool_id, self.frame._pauseIcons[paused])
            self.frame._pauseTool.SetLabel(label)
            self.frame.toolBar.SetToolShortHelp(tool_id, label + '（包括筛选隐藏的任务）')
        self.frame._pauseToolState = (paused, enabled)

    def OnPauseAllDownloads(self, event):
        try:
            self.frame.runner.pause_all()
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(str(error), '暂停失败', parent=self.frame)
        self.frame._SyncDownloads()
        self.frame.UpdateWindowUI(wx.UPDATE_UI_RECURSE)

    def OnResumeAllDownloads(self, event):
        try:
            if self.frame._GlobalDownloadActions()[1]:
                self.frame._completion_notified.difference_update(self.frame.runner.resume_all())
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(str(error), '继续失败', parent=self.frame)
        self.frame._SyncDownloads()
        self.frame.UpdateWindowUI(wx.UPDATE_UI_RECURSE)

