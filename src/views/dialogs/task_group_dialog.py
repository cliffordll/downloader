"""Create and manage persisted download groups."""

from pathlib import Path

import wx

from src.core.task_files import collect_task_trash_paths
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
    def __init__(self, parent, group_name, task_count):
        super().__init__(parent, title='删除分组')
        border = self.dialog_spacing
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.AddSpacer(border)
        description = wx.StaticText(self, label=f'分组“{group_name}”中有 {task_count} 个任务。')
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
            self, label='删除任务和文件，任务文件移入回收站', style=wx.RB_SINGLE)
        layout.Add(self.delete_option, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM,
                   border=border)
        layout.AddSpacer(self.FromDIP(4))
        note = wx.StaticText(self, label='组目录中不属于任务的文件会保留。')
        note.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        layout.Add(note, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
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


class TaskGroupManagerDialog(AppDialog):
    """Group CRUD UI; disk directories are created, renamed and removed safely."""

    def __init__(self, parent, repository, download_root, task_service, runner):
        super().__init__(parent, title='管理分组', size=parent.FromDIP(wx.Size(520, 420)))
        self.repository = repository
        self.task_service = task_service
        self.runner = runner
        self.download_root = Path(download_root).expanduser().resolve()
        border = self.dialog_spacing
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(wx.StaticText(self, label='分组对应下载目录下的文件夹。删除分组时可选择保留或删除组内任务。'),
                   flag=wx.ALL, border=border)
        self.list = wx.ListBox(self)
        layout.Add(self.list, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=border)
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
        dialog = GroupNameDialog(self, title='重命名分组', name=group.name)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            name = dialog.GetName()
            target = group.folder_path.with_name(name)
            updated = TaskGroup.model_validate({
                **group.model_dump(mode='json'), 'name': name, 'folder_path': str(target)})
            if target != group.folder_path:
                group.folder_path.rename(target)
            try:
                saved = self.repository.update_group(updated)
            except Exception:
                if target != group.folder_path:
                    target.rename(group.folder_path)
                raise
            self.RefreshGroups(saved.id)
        except (OSError, ValueError, RuntimeError) as error:
            wx.MessageBox(f'重命名分组失败：{error}', '分组操作失败', wx.OK | wx.ICON_WARNING, parent=self)
        finally:
            dialog.Destroy()

    def OnDelete(self, event):
        index = self.list.GetSelection()
        if index == wx.NOT_FOUND:
            return
        group = self.groups[index]
        tasks = [task for task in self.repository.list_tasks() if task.group_id == group.id]
        dialog = GroupDeleteDialog(self, group.name, len(tasks))
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            delete_tasks = dialog.GetDeleteTasks()
        finally:
            dialog.Destroy()
        deleted_count = 0
        try:
            busy = [task for task in tasks if self.runner.busy(task.id)]
            if busy:
                names = '、'.join(task.name for task in busy[:3])
                suffix = '等' if len(busy) > 3 else ''
                raise ValueError(f'任务{names}{suffix}正在运行或处理中，请暂停并等待后重试。')
            if delete_tasks:
                all_tasks = self.repository.list_tasks()
                for task in tasks:
                    others = [other for other in all_tasks if other.id != task.id]
                    collect_task_trash_paths(task, others)
                for task in tasks:
                    self.runner.delete(task.id, delete_files=True, scope='task')
                    deleted_count += 1
                if not self.repository.delete_group(group.id):
                    raise ValueError('任务已删除，但分组记录未能删除；请刷新后重试。')
            else:
                self.task_service.delete_group_preserving_tasks(group.id)
            try:
                group.folder_path.rmdir()
            except OSError:
                pass  # Non-task files are intentionally preserved.
            self.RefreshGroups()
        except Exception as error:
            if delete_tasks and deleted_count:
                self.RefreshGroups(group.id)
                message = (f'已完成 {deleted_count}/{len(tasks)} 个任务的删除，分组仍保留。'
                           f'\n失败原因：{error}')
            else:
                message = f'删除分组失败：{error}'
            wx.MessageBox(message, '分组操作失败', wx.OK | wx.ICON_WARNING, parent=self)
