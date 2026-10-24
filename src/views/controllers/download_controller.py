"""下载状态同步、任务进度和暂停/继续操作的界面控制器。"""

import json
import sqlite3
from queue import Empty
from threading import Thread

import wx

from src.media.m3u8.m3u8_downloader import M3U8Downloader
from src.schemas.task import TaskStatus, TaskType
from src.storage.task_repository import TaskDataError


class DownloadController:
    """同步 TaskRunner 状态到主列表并处理全局下载控制。"""

    def __init__(self, frame):
        self.frame = frame
        self._all_download_running = False

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

    def _NewDownloadTargets(self):
        if self._all_download_running or not hasattr(self.frame, 'model'):
            return []
        return [(index, task) for index, task in enumerate(self.frame.model.fileTree.items)
                if task.task_id and task.task_type in (TaskType.MP4, TaskType.M3U8)
                and task.task_status == TaskStatus.NEW
                and not self.frame.runner.is_deleting(task.task_id)
                and (not task.encrypted or self.frame._encryption_context is not None)]

    def CanDownloadAll(self):
        return bool(self._NewDownloadTargets())

    def OnDownloadAll(self, _event):
        targets = self._NewDownloadTargets()
        if not targets:
            return
        self._all_download_running = True
        total = len(targets)
        self.frame._SetPrimaryStatusText(f'正在启动全部下载：0/{total}')
        self.frame.UpdateWindowUI(wx.UPDATE_UI_RECURSE)

        def start_tasks():
            started = []
            failed = []
            for ordinal, (_index, task) in enumerate(targets, 1):
                try:
                    result = self.frame.runner.start(task.task_id)
                    if not result:
                        failed.append(f'{task.name}: 没有可下载内容')
                    else:
                        started.append(task)
                except Exception as error:
                    failed.append(f'{task.name}: {error}')
                wx.CallAfter(self.frame._SetPrimaryStatusText,
                             f'正在启动全部下载：{ordinal}/{total}')
            wx.CallAfter(self._FinishDownloadAll, started, failed, total)

        worker = Thread(target=start_tasks, name='start-all-new-tasks')
        self._all_download_thread = worker
        try:
            worker.start()
        except Exception as error:
            self._all_download_running = False
            self.frame.UpdateWindowUI(wx.UPDATE_UI_RECURSE)
            wx.MessageBox(str(error), '全部下载失败', wx.OK | wx.ICON_ERROR, parent=self.frame)

    def _FinishDownloadAll(self, started, failed, total):
        if self.frame._closing or self.frame.IsBeingDeleted():
            return
        self._all_download_running = False
        self.frame._completion_notified.difference_update(task.task_id for task in started)
        self.frame._SyncDownloads()
        self.frame.UpdateWindowUI(wx.UPDATE_UI_RECURSE)
        self.frame._SetPrimaryStatusText(f'全部下载已启动：{len(started)}/{total}')
        if failed:
            details = '\n'.join(failed[:5])
            if len(failed) > 5:
                details += f'\n另有 {len(failed) - 5} 项失败。'
            wx.MessageBox(f'成功启动 {len(started)} 个，失败 {len(failed)} 个。\n\n{details}',
                          '全部下载', wx.OK | wx.ICON_WARNING, parent=self.frame)

    def OnUpdateGlobalDownloadAction(self, event):
        download_all = getattr(self.frame, 'downloadAllItem', None)
        download_tool = getattr(self.frame, '_downloadAllTool', None)
        if ((download_all is not None and event.GetId() == download_all.GetId())
                or (download_tool is not None and event.GetId() == download_tool.GetId())):
            event.Enable(self.CanDownloadAll())
            return
        can_pause, can_resume = self.frame._GlobalDownloadActions()
        pause_tool = getattr(self.frame, '_pauseTool', None)
        is_pause = event.GetId() == self.frame.pauseAllItem.GetId() or (
            pause_tool is not None and event.GetId() == pause_tool.GetId())
        if is_pause:
            event.Enable(can_pause)
        else:
            event.Enable(can_resume)

    def OnUpdatePauseDownloads(self, event):
        paused = M3U8Downloader.IsPaused()
        busy = M3U8Downloader.IsBusy() or any(task.task_type == TaskType.MP4 and
            self.frame.runner.mp4.busy(task.task_id) for task in self.frame.model.fileTree.items)
        can_pause, can_resume = self.frame._GlobalDownloadActions()
        event.Enable(can_pause if event.GetId() == self.frame._pauseTool.GetId() else can_resume)
        self.frame._UpdatePauseTool()
        snapshot = M3U8Downloader.Snapshot()
        self.frame._SetPrimaryStatusText(('暂停中' if snapshot['requesting'] else '已暂停') if paused
                                   else ('正在下载' if busy else '就绪'))

    def _UpdatePauseTool(self):
        """更新独立的全局暂停与继续按钮，混合状态下两者都可以使用。"""
        if self.frame.toolBar is None:
            return
        can_pause, can_resume = self.frame._GlobalDownloadActions()
        state = (can_pause, can_resume)
        if self.frame._pauseToolState == state:
            return
        self.frame.toolBar.EnableTool(self.frame._pauseTool.GetId(), can_pause)
        self.frame.toolBar.EnableTool(self.frame._resumeTool.GetId(), can_resume)
        self.frame._pauseToolState = state

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

