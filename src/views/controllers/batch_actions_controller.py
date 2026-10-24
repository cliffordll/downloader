"""对复选框选中的任务执行批量操作。"""

from pathlib import Path
from threading import Thread

import wx

from src.schemas.task import TaskStatus, TaskType
from src.views.controllers.encryption_controller import TaskArtifactCleanupWarning


class BatchActionsController:
    """批量入口只调度主窗口已有的任务、下载和加密逻辑。"""

    OPERATIONS = (
        ('download', '下载'),
        ('continue', '继续'),
        ('pause', '暂停'),
        ('encrypt', '加密'),
        ('decrypt', '解密'),
        ('delete', '删除'),
    )
    DOWNLOADABLE_TYPES = (TaskType.MP4, TaskType.M3U8)
    ACTIVE_STATUSES = (TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.PAUSING)
    RESUMABLE_STATUSES = (TaskStatus.PAUSED, TaskStatus.INTERRUPTED, TaskStatus.PARTIAL)

    def __init__(self, frame):
        self.frame = frame
        self._task_menu = None
        self._task_menu_items = []
        self._delete_running = False
        self._deleting_ids = set()

    def _selected_tasks(self):
        selected_ids = self.frame.model.selected_task_ids
        return [(index, task) for index, task in enumerate(self.frame.model.fileTree.items)
                if (task.task_id or task.parent.fileName) in selected_ids]

    def _is_busy(self, task):
        try:
            return self.frame.runner.busy(task.task_id)
        except Exception:
            return True

    def _eligible(self, operation):
        if self._delete_running:
            return []
        selected = self._selected_tasks()
        if operation == 'delete':
            return selected

        eligible = []
        for index, task in selected:
            if task.task_type not in self.DOWNLOADABLE_TYPES:
                continue
            info = self.frame.model.TaskInfo(index)
            if operation == 'download':
                allowed = task.task_status == TaskStatus.NEW
            elif operation == 'continue':
                allowed = (task.task_status in self.RESUMABLE_STATUSES
                           or (task.task_type == TaskType.M3U8 and info['paused']))
            elif operation == 'pause':
                if task.task_type == TaskType.M3U8:
                    allowed = bool(info['pending']) and not info['paused'] and not info['merging']
                else:
                    allowed = task.task_status in self.ACTIVE_STATUSES
            elif operation == 'encrypt':
                allowed = not task.encrypted and not self._is_busy(task)
            elif operation == 'decrypt':
                allowed = task.encrypted and not self._is_busy(task)
            else:
                allowed = False
            if allowed:
                eligible.append((index, task))
        return eligible

    def UpdateActionAvailability(self):
        tool = getattr(self.frame, '_batchActionsTool', None)
        if tool is not None and self.frame.toolBar is not None:
            self.frame.toolBar.EnableTool(tool.GetId(), not self._delete_running)
        item = getattr(self.frame, 'batchActionsItem', None)
        if item is not None:
            item.Enable(not self._delete_running)

    def BuildMenu(self):
        menu = wx.Menu()
        for index, (operation, label) in enumerate(self.OPERATIONS):
            if index in (3, 5):
                menu.AppendSeparator()
            targets = self._eligible(operation)
            item = menu.Append(wx.ID_ANY, f'{label} ({len(targets)})')
            item.Enable(bool(targets))
            menu.Bind(wx.EVT_MENU,
                      lambda event, operation=operation: self.Execute(operation), item)
        return menu

    def CreateTaskSubMenu(self):
        """Create the persistent Task-menu submenu, refreshing counts when opened."""
        menu = wx.Menu()
        self._task_menu_items = []
        for index, (operation, label) in enumerate(self.OPERATIONS):
            if index in (3, 5):
                menu.AppendSeparator()
            item = menu.Append(wx.ID_ANY, f'{label} (0)')
            item.Enable(False)
            menu.Bind(wx.EVT_MENU,
                      lambda event, operation=operation: self.Execute(operation), item)
            self._task_menu_items.append((operation, item, label))
        self._task_menu = menu
        return menu

    def RefreshTaskSubMenu(self):
        if self._task_menu is None:
            return
        for operation, item, label in self._task_menu_items:
            targets = self._eligible(operation)
            item.SetItemLabel(f'{label} ({len(targets)})')
            item.Enable(bool(targets))

    def OnMenuOpen(self, event):
        if event.GetMenu() == self._task_menu:
            self.RefreshTaskSubMenu()
        event.Skip()

    def OnShowBatchActions(self, _event):
        menu = self.BuildMenu()
        try:
            position = self.frame.ScreenToClient(wx.GetMousePosition())
            self.frame.PopupMenu(menu, position)
        finally:
            menu.Destroy()

    def _confirm_delete(self, count):
        dialog = wx.MessageDialog(
            self.frame,
            f'将删除选中的 {count} 个任务。\n\n'
            '选择“是”会将任务文件移入回收站；选择“否”只删除任务记录；取消则不执行。',
            '批量删除任务', wx.YES_NO | wx.CANCEL | wx.NO_DEFAULT | wx.ICON_WARNING)
        try:
            return dialog.ShowModal()
        finally:
            dialog.Destroy()

    def _finish(self, action, succeeded, failed, warnings=()):
        if not failed and not warnings:
            return
        message = f'{action}完成：成功 {succeeded} 个'
        if failed:
            details = '\n'.join(failed[:5])
            if len(failed) > 5:
                details += f'\n另有 {len(failed) - 5} 项失败。'
            message += f'，失败 {len(failed)} 个\n\n{details}'
        if warnings:
            details = '\n'.join(warnings[:5])
            if len(warnings) > 5:
                details += f'\n另有 {len(warnings) - 5} 项警告。'
            message += f'\n\n清理警告 {len(warnings)} 个：\n{details}'
        wx.MessageBox(message, f'批量{action}', wx.OK | (wx.ICON_WARNING if failed or warnings else wx.ICON_INFORMATION),
                      parent=self.frame)

    def Execute(self, operation):
        targets = self._eligible(operation)
        if not targets:
            return
        if operation == 'delete':
            choice = self._confirm_delete(len(targets))
            if choice == wx.ID_CANCEL:
                return
            delete_files = choice == wx.ID_YES
            self._delete_in_background(targets, delete_files)
            return

        if operation in ('encrypt', 'decrypt'):
            self._run_encryption(operation == 'encrypt', targets)
            return

        succeeded = 0
        failed = []
        for _index, task in targets:
            try:
                if operation == 'download':
                    result = self.frame.runner.start(task.task_id)
                    if not result:
                        failed.append(f'{task.name}: 没有可下载内容')
                        continue
                    self.frame._completion_notified.discard(task.task_id)
                else:
                    # activate 会依当前状态暂停或继续；上面的 eligibility 已限定目标状态。
                    self.frame.runner.activate(task.task_id)
                succeeded += 1
            except Exception as error:
                failed.append(f'{task.name}: {error}')
        self.frame._SyncDownloads()
        self.frame.UpdateWindowUI(wx.UPDATE_UI_RECURSE)
        self._finish('下载' if operation == 'download' else ('继续' if operation == 'continue' else '暂停'),
                     succeeded, failed)

    def _run_encryption(self, encrypted, targets):
        context_before = self.frame._encryption_context
        context = context_before or self.frame._GetEncryptionContextForOperation()
        if context is None:
            return
        failed = []
        warnings = []
        succeeded = 0
        action = '加密' if encrypted else '解密'
        progress = wx.ProgressDialog(
            f'批量{action}', f'准备{action}任务…', parent=self.frame,
            maximum=100, style=wx.PD_APP_MODAL | wx.PD_ELAPSED_TIME | wx.PD_SMOOTH)
        try:
            self.frame._encryption_context = context
            for ordinal, (_index, task) in enumerate(targets, 1):
                task_name = task.parent.displayName or Path(task.parent.fileName).name
                try:
                    progress.Pulse(f'正在{action} ({ordinal}/{len(targets)})：{task_name}')
                    self.frame._SetTaskEncrypted(
                        task.task_id, encrypted, progress_dialog=progress, raise_errors=True)
                    current = self.frame.tasks.repository.get(task.task_id)
                    if current is None or current.encrypted != encrypted:
                        failed.append(f'{task_name}: 操作未完成')
                    else:
                        succeeded += 1
                except Exception as error:
                    current = self.frame.tasks.repository.get(task.task_id)
                    if current is not None and current.encrypted == encrypted:
                        succeeded += 1
                        if isinstance(error, TaskArtifactCleanupWarning):
                            warnings.append(f'{task_name}: 状态已更新，但旧文件清理未完成：{error}')
                        else:
                            warnings.append(f'{task_name}: 状态已更新，但后续处理未完成：{error}')
                    else:
                        failed.append(f'{task_name}: {error}')
        finally:
            progress.Destroy()
            self.frame._encryption_context = context_before
        self._finish(action, succeeded, failed, warnings)

    def _delete_in_background(self, targets, delete_files):
        self._delete_running = True
        self.UpdateActionAvailability()
        total = len(targets)
        deleting_ids = {task.task_id for _index, task in targets if task.task_id}
        self._deleting_ids = deleting_ids
        self.frame.model.deleting_task_ids.update(deleting_ids)
        for index, task in targets:
            if not task.task_id:
                continue
            item = self.frame.model.ObjectToItem(self.frame.model._BuildKey((index,)))
            self.frame.model.ValueChanged(item, 6)
        self.frame._SetPrimaryStatusText(f'正在批量删除：0/{total}')

        def delete_tasks():
            deleted = []
            failed = []
            try:
                remaining_tasks = (self.frame.tasks.repository.list_tasks()
                                   if delete_files else None)
            except Exception as error:
                wx.CallAfter(self._complete_delete, deleted,
                             [f'读取任务文件关系失败：{error}'], total)
                return
            for ordinal, (_index, task) in enumerate(targets, 1):
                try:
                    self.frame.runner.delete(task.task_id, delete_files=delete_files,
                                              other_tasks=remaining_tasks)
                    deleted.append(task)
                    if remaining_tasks is not None:
                        remaining_tasks = [record for record in remaining_tasks
                                           if record.id != task.task_id]
                except Exception as error:
                    failed.append(f'{task.name}: {error}')
                wx.CallAfter(self._update_delete_progress, ordinal, total)
            wx.CallAfter(self._complete_delete, deleted, failed, total)

        worker = Thread(target=delete_tasks, name='batch-task-delete')
        self._delete_thread = worker
        try:
            worker.start()
        except Exception as error:
            self.frame.model.deleting_task_ids.difference_update(deleting_ids)
            self._deleting_ids.clear()
            self.frame.mcTree.Refresh()
            self._delete_running = False
            self.UpdateActionAvailability()
            self._finish('删除', 0, [str(error)])

    def _frame_is_alive(self):
        try:
            return self.frame is not None and not self.frame.IsBeingDeleted()
        except RuntimeError:
            return False

    def _update_delete_progress(self, completed, total):
        if self._frame_is_alive():
            self.frame._SetPrimaryStatusText(f'正在批量删除：{completed}/{total}')

    def _complete_delete(self, deleted, failed, total):
        if not self._frame_is_alive():
            return
        self.frame.model.deleting_task_ids.difference_update(self._deleting_ids)
        self._deleting_ids.clear()
        for task in deleted:
            self.frame.model.selected_task_ids.discard(task.task_id or task.parent.fileName)
            self.frame.model.merge_failed.discard(task.parent.fileName)
            self.frame._completion_notified.discard(task.task_id)
        self.frame.OnRefresh(None)
        self.frame._SyncDownloads()
        self._delete_running = False
        self.UpdateActionAvailability()
        succeeded = len(deleted)
        self.frame._SetPrimaryStatusText(f'批量删除完成：{succeeded}/{total}')
        self._finish('删除', succeeded, failed)
