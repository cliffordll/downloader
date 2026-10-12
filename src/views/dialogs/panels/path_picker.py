import wx
from pathlib import Path
from src.core.path_manager import PathManager
from src.views.dialogs.information_dialog import InformationDialog

def form_label_size(window, width):
    """Windows 保留原宽度；Mac 同列标签统一预留完整文字与留白。"""
    if wx.Platform != '__WXMAC__':
        return window.FromDIP((width, -1))
    labels = ('列表网址：', '视频网址：', '路径改写：', '保存目录：', '文件名称：', '播放时长:', '段名规则:') if width == 60 else ('开始:', '结束:')
    measured = max(window.GetTextExtent(label).width for label in labels)
    return wx.Size(max(window.FromDIP(width), measured + window.FromDIP(4)), -1)


def form_action_size(window):
    """保存表单右侧的帮助、浏览和下载使用同一列尺寸。"""
    native = wx.Button.GetDefaultSize()
    return wx.Size(max(window.FromDIP(75), native.width), native.height)


class DownloadHelpDialog(InformationDialog):
    def __init__(self, parent, title, text):
        super().__init__(parent, title, text)


class DownloadPath(wx.Panel):
    """三个下载弹窗共用保存目录、文件名和下载按钮。"""
    def __init__(self, parent, workPath):
        super().__init__(parent=parent, id=wx.ID_ANY)
        self._suggested = ''
        sizer = wx.BoxSizer(wx.VERTICAL)
        directory_row = wx.BoxSizer(wx.HORIZONTAL)
        label = wx.StaticText(self, label='保存目录：', size=form_label_size(self, 60))
        self.directory = wx.TextCtrl(self, value=workPath)
        self.directory.SetToolTip('默认使用系统配置的下载目录，可为当前任务单独选择。')
        self.browseButton = wx.Button(self, label='浏览', size=form_action_size(self))
        self.browseButton.Bind(wx.EVT_BUTTON, self.OnBrowse)
        directory_row.Add(label, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.FromDIP(10))
        directory_row.Add(self.directory, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT,
                          border=self.FromDIP(10))
        directory_row.Add(self.browseButton, flag=wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(directory_row, flag=wx.EXPAND|wx.BOTTOM, border=self.FromDIP(5))

        filename_row = wx.BoxSizer(wx.HORIZONTAL)
        label = wx.StaticText(self, label='文件名称：', size=form_label_size(self, 60))
        self.tcDown = wx.TextCtrl(self)
        self.tcDown.SetHint('自动从网址生成，可修改，例如 video.mp4')
        self.tcDown.SetToolTip('重名时自动追加序号；M3U8、TS 的分片保存在同名子目录。')
        button = wx.Button(self, label='下载', size=form_action_size(self))
        button.Bind(wx.EVT_BUTTON, self.OnBtnDownClicked)
        filename_row.Add(label, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.FromDIP(10))
        filename_row.Add(self.tcDown, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.FromDIP(10))
        filename_row.Add(button, flag=wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(filename_row, flag=wx.EXPAND)
        self.SetSizer(sizer)

    def OnBrowse(self, event):
        dialog = wx.DirDialog(self, '选择保存目录', defaultPath=self.directory.GetValue())
        try:
            if dialog.ShowModal() == wx.ID_OK:
                self.directory.SetValue(dialog.GetPath())
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
