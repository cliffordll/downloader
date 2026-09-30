import sqlite3
import wx

from src.core.task_service import TaskService
from src.storage.task_repository import TaskDataError
from src.views.dialogs.panels.path_picker import DownloadPath


class DownloadDialogMP4(wx.Dialog):
    """MP4 直链只需网址和任务目录，不显示分片编辑器。"""
    def __init__(self, parent, work_path, task_service=None):
        super().__init__(parent, title='下载 MP4')
        self.tasks = task_service or TaskService()
        self.task_id = None
        layout = wx.BoxSizer(wx.VERTICAL)
        margin = self.FromDIP(10)
        self.downPath = DownloadPath(self, work_path)
        self.downPath.Bind(wx.EVT_BUTTON, self.OnDownload)
        self.downPath.lblPath.SetBackgroundColour(self.downPath.GetBackgroundColour())
        # 此弹窗的路径前缀和按钮固定宽度，仅子目录输入框伸展。
        # 原表单的 5:100:1 比例会把长路径的最小宽度放大，不能拿它决定窗口宽度。
        pathSizer = self.downPath.GetSizer()
        for index, proportion in ((1, 0), (2, 1), (3, 0)):
            pathSizer.GetItem(index).SetProportion(proportion)
        prefix = self.downPath.lblPath
        prefix.SetWindowStyleFlag(prefix.GetWindowStyleFlag() | wx.ST_ELLIPSIZE_MIDDLE)
        prefix.SetMinSize((min(prefix.GetSize().width, self.FromDIP(240)), prefix.GetSize().height))
        prefix.SetToolTip(work_path)
        row = wx.BoxSizer(wx.HORIZONTAL)
        # 与保存目录行共用相同的标签宽度和起始间距。
        pathLabel = self.downPath.GetSizer().GetItem(0).GetWindow()
        label = wx.StaticText(self, label='视频网址：', size=(pathLabel.GetSize().width, -1),
                              style=wx.ST_NO_AUTORESIZE)
        row.Add(label, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=10)
        self.url = wx.TextCtrl(self)
        self.url.SetHint('填写 HTTP / HTTPS 视频文件直链')
        row.Add(self.url, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL)
        layout.Add(row, flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT, border=margin)
        layout.AddSpacer(self.FromDIP(8))
        layout.Add(self.downPath, flag=wx.EXPAND|wx.BOTTOM|wx.LEFT|wx.RIGHT, border=margin)
        self.SetSizer(layout)
        # 高度由两行内容及边距决定，避免固定高度在底部留下空白。
        best = layout.GetMinSize()
        displayIndex = wx.Display.GetFromWindow(parent) if parent is not None else wx.NOT_FOUND
        screen = wx.Display(displayIndex if displayIndex != wx.NOT_FOUND else 0).GetClientArea()
        width = min(self.FromDIP(720), screen.width - 2 * margin)
        self.SetClientSize((width, best.height))
        self.Layout()
        self.downPath.Layout()
        self.CenterOnParent()

    def OnDownload(self, event):
        try:
            task = self.tasks.create_mp4(self.downPath.GetDownPath(), self.url.GetValue())
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f'创建 MP4 任务失败：{error}', '提示', wx.OK|wx.ICON_WARNING, parent=self)
            return
        self.task_id = task.id
        self.EndModal(wx.OK)
