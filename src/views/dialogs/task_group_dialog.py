"""Create and manage persisted download groups."""

from pathlib import Path
from threading import Thread

import wx

from src.schemas.task_group import TaskGroup
from src.views.dialogs.base_dialog import AppDialog


class GroupNameDialog(AppDialog):
    def __init__(self, parent, *, title, name=''):
        super().__init__(parent, title=title)
        border = self.dialog_spacing
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.AddSpacer(border)
        field_row = wx.BoxSizer(wx.HORIZONTAL)
        label = wx.StaticText(self, label='分组名称：',
                              size=self.FromDIP(wx.Size(60, -1)),
                              style=wx.ALIGN_LEFT | wx.ST_NO_AUTORESIZE)
        self.name = wx.TextCtrl(self, value=name, style=wx.TE_PROCESS_ENTER)
        field_row.Add(label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
                      border=self.FromDIP(10))
        field_row.Add(self.name, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL)
        layout.Add(field_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                   border=border)
        actions = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(self, wx.ID_OK, '确定')
        cancel = wx.Button(self, wx.ID_CANCEL, '取消')
        button_size = self.FromDIP(wx.Size(75, -1))
        ok.SetMinSize(button_size)
        cancel.SetMinSize(button_size)
        actions.AddStretchSpacer()
        actions.Add(ok, flag=wx.RIGHT, border=border)
        actions.Add(cancel)
        layout.Add(actions, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                   border=border)
        self.SetSizer(layout)
        client_size = wx.Size(self.FromDIP(440), layout.GetMinSize().height)
        self.SetClientSize(client_size)
        self.SetMinSize(client_size)
        self.CenterOnParent()
        ok.SetDefault()
        self.SetEscapeId(wx.ID_CANCEL)
        self.name.SetFocus()
        self.name.SetSelection(-1, -1)

    def GetName(self):
        return self.name.GetValue().strip()


class GroupDeleteDialog(AppDialog):
    def __init__(self, parent, group_name, task_count, *, encrypted_tasks_locked=False):
        super().__init__(parent, title='删除分组')
        border = self.dialog_spacing
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.AddSpacer(border)
        count_label = '个可见任务' if encrypted_tasks_locked else '个任务'
        description = wx.StaticText(
            self, label=f'分组“{group_name}”中有 {task_count} {count_label}。')
        layout.Add(description, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                   border=border)
        prompt = wx.StaticText(self, label='选择删除方式：')
        layout.Add(prompt, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=border)

        self.keep_option = wx.RadioButton(
            self, label='保留任务和文件，并移回默认下载目录', style=wx.RB_GROUP)
        layout.Add(self.keep_option, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM,
                   border=border)
        layout.AddSpacer(self.FromDIP(6))

        self.delete_option = wx.RadioButton(
            self, label='删除任务和文件，任务文件移入回收站')
        layout.Add(self.delete_option, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM,
                   border=border)
        layout.AddSpacer(self.FromDIP(4))
        note = wx.StaticText(self, label='组目录中不属于任务的文件会保留。')
        note.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        layout.Add(note, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                   border=border)
        if encrypted_tasks_locked:
            privacy_note = wx.StaticText(
                self, label='加密任务在锁定期间会保留，并移至未分组；其他任务按所选方式处理。')
            privacy_note.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
            layout.Add(privacy_note, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                       border=border)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(self, wx.ID_OK, '确定')
        cancel = wx.Button(self, wx.ID_CANCEL, '取消')
        button_size = self.FromDIP(wx.Size(80, -1))
        ok.SetMinSize(button_size)
        cancel.SetMinSize(button_size)
        buttons.AddStretchSpacer()
        buttons.Add(ok, flag=wx.RIGHT, border=self.FromDIP(8))
        buttons.Add(cancel)
        layout.Add(buttons, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                   border=border)
        self.SetSizer(layout)
        client_size = wx.Size(self.FromDIP(560), layout.GetMinSize().height)
        self.SetClientSize(client_size)
        self.SetMinSize(client_size)
        self.CenterOnParent()
        ok.SetDefault()
        self.SetEscapeId(wx.ID_CANCEL)
        self.keep_option.SetValue(True)
        self.keep_option.SetFocus()

    def GetDeleteTasks(self):
        return self.delete_option.GetValue()


class MoveTasksToGroupDialog(AppDialog):
    def __init__(self, parent, groups, task_count):
        super().__init__(parent, title='移动到分组')
        self.groups = list(groups)
        border = self.dialog_spacing
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.AddSpacer(border)
        description = wx.StaticText(
            self, label=f'将选中的 {task_count} 个任务移动到：')
        layout.Add(description, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                   border=border)
        self.destination = wx.Choice(self, choices=['未分组'] + [group.name for group in self.groups])
        self.destination.SetSelection(0)
        layout.Add(self.destination, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                   border=border)
        actions = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(self, wx.ID_OK, '确定')
        cancel = wx.Button(self, wx.ID_CANCEL, '取消')
        button_size = self.FromDIP(wx.Size(80, -1))
        ok.SetMinSize(button_size)
        cancel.SetMinSize(button_size)
        actions.AddStretchSpacer()
        actions.Add(ok, flag=wx.RIGHT, border=self.FromDIP(8))
        actions.Add(cancel)
        layout.Add(actions, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                   border=border)
        self.SetSizer(layout)
        client_size = wx.Size(self.FromDIP(460), layout.GetMinSize().height)
        self.SetClientSize(client_size)
        self.SetMinSize(client_size)
        self.CenterOnParent()
        ok.SetDefault()
        self.SetEscapeId(wx.ID_CANCEL)
        self.destination.SetFocus()

    def GetGroupId(self):
        selection = self.destination.GetSelection()
        return None if selection <= 0 else self.groups[selection - 1].id


def rename_task_group(parent, repository, group):
    """Show the shared rename flow and keep the group directory in sync."""
    dialog = GroupNameDialog(parent, title='重命名分组', name=group.name)
    try:
        if dialog.ShowModal() != wx.ID_OK:
            return None
        name = dialog.GetName()
        target = group.folder_path.with_name(name)
        updated = TaskGroup.model_validate({
            **group.model_dump(mode='json'), 'name': name, 'folder_path': str(target)})
        if target != group.folder_path:
            group.folder_path.rename(target)
        try:
            return repository.update_group(updated)
        except Exception:
            if target != group.folder_path:
                target.rename(group.folder_path)
            raise
    except (OSError, ValueError, RuntimeError) as error:
        wx.MessageBox(f'重命名分组失败：{error}', '分组操作失败',
                      wx.OK | wx.ICON_WARNING, parent=parent)
        return None
    finally:
        dialog.Destroy()


def delete_task_group(parent, repository, task_service, runner, group):
    """Confirm group deletion, then perform file operations off the UI thread."""
    tasks = [task for task in repository.list_tasks() if task.group_id == group.id]
    frame = getattr(parent, 'frame', parent)
    encrypted_tasks_locked = not getattr(frame, '_encrypted_tasks_unlocked', True)
    visible_tasks = [task for task in tasks
                     if not (encrypted_tasks_locked and task.encrypted)]
    dialog = GroupDeleteDialog(parent, group.name, len(visible_tasks),
                               encrypted_tasks_locked=encrypted_tasks_locked)
    try:
        if dialog.ShowModal() != wx.ID_OK:
            return False
        delete_tasks = dialog.GetDeleteTasks()
    finally:
        dialog.Destroy()

    if isinstance(parent, TaskGroupManagerDialog):
        parent.SetDeletionRunning(True)
        parent.SetDeletionProgress('准备删除分组…')

    def delete_in_background():
        deleted_count = 0
        try:
            protected_tasks = [task for task in tasks
                               if encrypted_tasks_locked and task.encrypted]
            tasks_to_delete = visible_tasks if delete_tasks else []
            if delete_tasks:
                remaining_tasks = repository.list_tasks()
                if protected_tasks:
                    _set_group_deletion_progress(
                        parent, '正在保留并移动加密任务…')
                    task_service.move_tasks_to_group(
                        [task.id for task in protected_tasks], None)
                    remaining_tasks = repository.list_tasks()
                for ordinal, task in enumerate(tasks_to_delete, 1):
                    _set_group_deletion_progress(
                        parent, f'正在删除任务 {ordinal}/{len(tasks_to_delete)}：{task.name}')
                    runner.delete(task.id, delete_files=True, scope='task',
                                  other_tasks=remaining_tasks)
                    deleted_count += 1
                    remaining_tasks = [other for other in remaining_tasks
                                       if other.id != task.id]
                if not repository.delete_group(group.id):
                    raise ValueError('任务已删除，但分组记录未能删除；请刷新后重试。')
            else:
                _set_group_deletion_progress(parent, '正在移动组内任务和文件…')
                task_service.delete_group_preserving_tasks(group.id)
            try:
                group.folder_path.rmdir()
            except OSError:
                pass  # Non-task files are intentionally preserved.
            error = None
        except Exception as caught:
            error = caught
        wx.CallAfter(_finish_group_deletion, parent, frame, repository, group,
                     encrypted_tasks_locked, any(task.encrypted for task in tasks),
                     delete_tasks, len(visible_tasks), deleted_count, error)

    worker = Thread(target=delete_in_background, name='delete-task-group', daemon=True)
    try:
        worker.start()
    except RuntimeError as error:
        if isinstance(parent, TaskGroupManagerDialog):
            parent.SetDeletionRunning(False)
        wx.MessageBox(f'无法开始删除分组：{error}', '分组操作失败',
                      wx.OK | wx.ICON_WARNING, parent=parent)
        return False
    return True


def _finish_group_deletion(parent, frame, repository, group, encrypted_tasks_locked,
                           has_encrypted_tasks, delete_tasks, visible_count,
                           deleted_count, error):
    """Update controls and report the result after background filesystem work."""
    if isinstance(parent, TaskGroupManagerDialog):
        try:
            parent.SetDeletionRunning(False)
            parent.SetDeletionProgress('')
            parent.RefreshGroups(group.id if error else None)
        except RuntimeError:
            pass
    try:
        frame.OnRefresh(None)
        frame._SyncDownloads()
        frame.UpdateWindowUI(wx.UPDATE_UI_RECURSE)
    except RuntimeError:
        pass
    if error is None:
        return
    if encrypted_tasks_locked and has_encrypted_tasks:
        message = ('删除分组未能完成。加密任务仍会保留；请检查可见任务状态后重试。'
                   if not (delete_tasks and deleted_count) else
                   f'已完成 {deleted_count}/{visible_count} 个可见任务的删除，'
                   '分组仍保留。加密任务仍会保留。')
    elif delete_tasks and deleted_count:
        message = (f'已完成 {deleted_count}/{visible_count} 个任务的删除，分组仍保留。'
                   f'\n失败原因：{error}')
    else:
        message = f'删除分组失败：{error}'
    wx.MessageBox(message, '分组操作失败', wx.OK | wx.ICON_WARNING, parent=parent)


def _set_group_deletion_progress(parent, message):
    if isinstance(parent, TaskGroupManagerDialog):
        wx.CallAfter(parent.SetDeletionProgress, message)


class TaskGroupManagerDialog(AppDialog):
    """Group CRUD UI; disk directories are created, renamed and removed safely."""

    def __init__(self, parent, repository, download_root, task_service, runner):
        super().__init__(parent, title='管理分组', size=parent.FromDIP(wx.Size(520, 420)))
        self.repository = repository
        self.task_service = task_service
        self.runner = runner
        self.frame = parent
        self.download_root = Path(download_root).expanduser().resolve()
        border = self.dialog_spacing
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(wx.StaticText(self, label='分组对应下载目录下的文件夹。删除分组时可选择保留或删除组内任务。'),
                   flag=wx.ALL, border=border)
        self.list = wx.ListBox(self)
        layout.Add(self.list, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=border)
        self.operation_status = wx.StaticText(self, label='')
        self.operation_status.SetForegroundColour(
            wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        layout.Add(self.operation_status, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=border)
        actions = wx.BoxSizer(wx.HORIZONTAL)
        self.new_button = wx.Button(self, label='新建')
        self.rename_button = wx.Button(self, label='重命名')
        self.delete_button = wx.Button(self, label='删除')
        for button in (self.new_button, self.rename_button, self.delete_button):
            actions.Add(button, flag=wx.RIGHT, border=self.FromDIP(6))
        actions.AddStretchSpacer()
        close = wx.Button(self, wx.ID_CLOSE, '关闭')
        actions.Add(close)
        layout.Add(actions, flag=wx.EXPAND | wx.ALL, border=border)
        self.SetSizer(layout)
        self.new_button.Bind(wx.EVT_BUTTON, self.OnNew)
        self.rename_button.Bind(wx.EVT_BUTTON, self.OnRename)
        self.delete_button.Bind(wx.EVT_BUTTON, self.OnDelete)
        close.Bind(wx.EVT_BUTTON, lambda event: self.EndModal(wx.ID_CLOSE))
        self.close_button = close
        self.list.Bind(wx.EVT_LISTBOX, self.UpdateActions)
        self.RefreshGroups()
        self.CentreOnParent()

    def RefreshGroups(self, select_id=None):
        self.groups = self.repository.list_groups()
        self.list.Clear()
        self._indices = {}
        selected = wx.NOT_FOUND
        for index, group in enumerate(self.groups):
            self.list.Append(f'{group.name}    —    {group.folder_path}')
            self._indices[group.id] = index
            if group.id == select_id:
                selected = index
        if selected != wx.NOT_FOUND:
            self.list.SetSelection(selected)
        self.UpdateActions()

    def UpdateActions(self, event=None):
        selected = self.list.GetSelection() != wx.NOT_FOUND
        self.rename_button.Enable(selected)
        self.delete_button.Enable(selected)

    def SetDeletionRunning(self, running):
        enabled = not running
        self.list.Enable(enabled)
        self.new_button.Enable(enabled)
        self.rename_button.Enable(enabled and self.list.GetSelection() != wx.NOT_FOUND)
        self.delete_button.Enable(enabled and self.list.GetSelection() != wx.NOT_FOUND)
        self.close_button.Enable(enabled)
        self.SetTitle('管理分组（正在处理…）' if running else '管理分组')

    def SetDeletionProgress(self, message):
        self.operation_status.SetLabel(message)
        self.Layout()

    def OnNew(self, event):
        dialog = GroupNameDialog(self, title='新建分组')
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            name = dialog.GetName()
            validated = TaskGroup(name=name, folder_path=(self.download_root / name).resolve(),
                                  position=len(self.groups))
            path = validated.folder_path
            path.mkdir(parents=True, exist_ok=False)
            try:
                group = self.repository.create_group(validated.name, path,
                    position=validated.position)
            except Exception:
                try:
                    path.rmdir()
                except OSError:
                    pass
                raise
            self.RefreshGroups(group.id)
        except (OSError, ValueError, RuntimeError) as error:
            wx.MessageBox(f'新建分组失败：{error}', '分组操作失败', wx.OK | wx.ICON_WARNING, parent=self)
        finally:
            dialog.Destroy()

    def OnRename(self, event):
        index = self.list.GetSelection()
        if index == wx.NOT_FOUND:
            return
        group = self.groups[index]
        saved = rename_task_group(self, self.repository, group)
        if saved is not None:
            self.RefreshGroups(saved.id)

    def OnDelete(self, event):
        index = self.list.GetSelection()
        if index == wx.NOT_FOUND:
            return
        group = self.groups[index]
        delete_task_group(self, self.repository, self.task_service, self.runner, group)
