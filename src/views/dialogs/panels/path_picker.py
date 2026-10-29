import wx
from pathlib import Path
from src.core.path_manager import PathManager
from src.core.sys_setting import SysSetting
from src.views.dialogs.information_dialog import InformationDialog

def form_label_size(window, width):
    """Windows 保留原宽度；Mac 同列标签统一预留完整文字与留白。"""
    if wx.Platform != '__WXMAC__':
        return window.FromDIP((width, -1))
    labels = ('列表网址：', '视频网址：', '路径改写：', '保存目录：', '输出文件：', '播放时长:', '段名规则:') if width == 60 else ('开始:', '结束:')
    measured = max(window.GetTextExtent(label).width for label in labels)
    return wx.Size(max(window.FromDIP(width), measured + window.FromDIP(4)), -1)


def form_action_size(window):
    """保存表单右侧的帮助、浏览和创建操作使用同一列尺寸。"""
    native = wx.Button.GetDefaultSize()
    return wx.Size(max(window.FromDIP(75), native.width), native.height)


class DownloadHelpDialog(InformationDialog):
    def __init__(self, parent, title, text):
        super().__init__(parent, title, text)


class DownloadPath(wx.Panel):
    """三个任务弹窗共用保存目录、文件名和创建按钮。"""
    def __init__(self, parent, workPath, task_service=None):
        super().__init__(parent=parent, id=wx.ID_ANY)
        self._suggested = ''
        self._default_directory = str(workPath)
        self._ungrouped_directory = None
        self._groups = task_service.list_groups() if task_service is not None else []
        self._selected_group = None
        sizer = wx.BoxSizer(wx.VERTICAL)
        group_row = wx.BoxSizer(wx.HORIZONTAL)
        group_label = wx.StaticText(self, label='分组：', size=form_label_size(self, 60))
        self.groupChoice = wx.Choice(self, choices=['未分组'] + [group.name for group in self._groups])
        self.groupChoice.SetSelection(0)
        last_group_id = SysSetting.GetAll().get('last_group_id')
        selected_group = next((group for group in self._groups
                               if str(group.id) == last_group_id), None)
        if selected_group is not None:
            self.groupChoice.SetSelection(self._groups.index(selected_group) + 1)
            self._selected_group = selected_group
        self.groupChoice.Bind(wx.EVT_CHOICE, self.OnGroupChanged)
        group_row.Add(group_label, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.FromDIP(10))
        group_row.Add(self.groupChoice, proportion=1, flag=wx.EXPAND)
        sizer.Add(group_row, flag=wx.EXPAND|wx.BOTTOM, border=self.FromDIP(5))

        directory_row = wx.BoxSizer(wx.HORIZONTAL)
        label = wx.StaticText(self, label='保存目录：', size=form_label_size(self, 60))
        self.directory = wx.TextCtrl(self, value=workPath)
        if self._selected_group is not None:
            self.directory.SetValue(str(self._selected_group.folder_path))
            self.directory.Enable(False)
        self.directory.SetToolTip('默认使用系统配置的下载目录，可为当前任务单独选择。')
        self.browseButton = wx.Button(self, label='选择目录', size=form_action_size(self))
        self.browseButton.Enable(self._selected_group is None)
        self.browseButton.Bind(wx.EVT_BUTTON, self.OnBrowse)
        directory_row.Add(label, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.FromDIP(10))
        directory_row.Add(self.directory, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT,
                          border=self.FromDIP(10))
        directory_row.Add(self.browseButton, flag=wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(directory_row, flag=wx.EXPAND|wx.BOTTOM, border=self.FromDIP(5))

        filename_row = wx.BoxSizer(wx.HORIZONTAL)
        label = wx.StaticText(self, label='输出文件：', size=form_label_size(self, 60))
        self.tcDown = wx.TextCtrl(self)
        self.tcDown.SetHint('自动从网址生成，可修改，例如 video.mp4')
        self.tcDown.SetToolTip('重名时自动追加序号；M3U8、TS 的分片保存在同名子目录。')
        self.createButton = wx.Button(self, label='创建任务', size=form_action_size(self))
        self.createButton.Bind(wx.EVT_BUTTON, self.OnBtnDownClicked)
        filename_row.Add(label, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.FromDIP(10))
        filename_row.Add(self.tcDown, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.FromDIP(10))
        filename_row.Add(self.createButton, flag=wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(filename_row, flag=wx.EXPAND)
        self.SetSizer(sizer)

    def GetGroupId(self):
        return self._selected_group.id if self._selected_group is not None else None

    def RememberGroupSelection(self):
        """Persist the last group only after a task was successfully created."""
        values = SysSetting.GetAll()
        group_id = self.GetGroupId()
        values['last_group_id'] = str(group_id) if group_id is not None else None
        try:
            SysSetting.Save(values)
            return True
        except (OSError, ValueError):
            # The task has already been created; a settings write failure must
            # not make the dialog report that task creation itself failed.
            return False

    def OnGroupChanged(self, event):
        previous_group = self._selected_group
        selection = self.groupChoice.GetSelection()
        self._selected_group = self._groups[selection - 1] if selection > 0 else None
        if self._selected_group is None:
            self.directory.SetValue(self._ungrouped_directory or self._default_directory)
            self._ungrouped_directory = None
            self.directory.Enable(True)
            self.browseButton.Enable(True)
        else:
            if previous_group is None:
                self._ungrouped_directory = self.directory.GetValue()
            self.directory.SetValue(str(self._selected_group.folder_path))
            # 分组的保存目录由分组定义，避免任务归组与落盘位置不一致。
            self.directory.Enable(False)
            self.browseButton.Enable(False)

    def OnBrowse(self, event):
        dialog = wx.DirDialog(self, '选择保存目录', defaultPath=self.directory.GetValue())
        try:
            if dialog.ShowModal() == wx.ID_OK:
                self.directory.SetValue(dialog.GetPath())
                self._default_directory = dialog.GetPath()
        finally:
            dialog.Destroy()

    def SetDownPath(self, baseUri):
        # 只更新自动建议的名称，网址变化不能覆盖用户手动填写的名称。
        if self.tcDown.GetValue().strip() not in ('', self._suggested):
            return
        try:
            self._suggested = PathManager.GetFileNameFromURI(baseUri)
        except ValueError:
            return  # 用户正在输入不完整的网址。
        self.tcDown.ChangeValue(self._suggested)

    def OnSourceChanged(self, event):
        self.SetDownPath(event.GetString())
        event.Skip()

    def GetDownPath(self):
        value = self.directory.GetValue().strip()
        directory = Path(value).expanduser()
        if not value or not directory.is_absolute() or (directory.exists() and not directory.is_dir()):
            raise ValueError('请选择有效的绝对保存目录。')
        return str(directory.resolve())

    def GetFileName(self):
        value = self.tcDown.GetValue().strip()
        return PathManager.ValidateFileName(value) if value else None

    def OnBtnDownClicked(self, event):
        try:
            self.GetDownPath()
            self.GetFileName()
        except ValueError as error:
            wx.MessageBox(str(error), '提示', wx.ICON_WARNING, parent=self)
            return
        event.Skip()
