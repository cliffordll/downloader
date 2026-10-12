import sqlite3
import wx

from src.core.task_service import TaskService
from src.storage.task_repository import TaskDataError
from src.views.dialogs.panels.path_picker import DownloadPath
from src.views.dialogs.panels.mp4_form import DownloadEditMP4


class DownloadDialogMP4(wx.Dialog):
    """MP4 直链只需网址、保存目录和文件名，不显示分片编辑器。"""
    def __init__(self, parent, work_path, task_service: TaskService):
        super().__init__(parent=parent, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.tasks = task_service  # 复用主窗口传入的服务，不创建另一份任务库入口。
        self.task_id = None
        self.SetTitle('下载 MP4')

        sizer = wx.BoxSizer(wx.VERTICAL)
        self.downEdit = DownloadEditMP4(self)
        self.downPath = DownloadPath(self, work_path)
        self.downPath.Bind(wx.EVT_BUTTON, self.OnDownBtnClicked)
        self.downEdit.tcURI.Bind(wx.EVT_TEXT, self.downPath.OnSourceChanged)

        # MP4 没有清单编辑区，各行按内容排列，不分配额外纵向空间。
        border = self.FromDIP(5)
        sizer.Add(self.downEdit, proportion=0, flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT, border=border)
        sizer.Add(self.downPath, proportion=0, flag=wx.EXPAND|wx.ALL, border=border)

        self.SetSizer(sizer)
        # # MP4 按表单内容，按内容计算高度，不预留 TS 清单编辑区的空间。
        height = self.ClientToWindowSize(wx.Size(0, sizer.GetMinSize().height)).height
        self.SetSize(width=self.FromDIP(720), height=height)
        self.SetMinSize(wx.Size(self.FromDIP(540), height))
        self.SetMaxSize(wx.Size(-1, height))  # 仅允许横向拉伸，避免表单中间出现大块留白。
        # self.SetSize(width=self.FromDIP(720), height=self.FromDIP(108))
        # self.SetSize(self.FromDIP((720, 108)))
        # self.Layout()
        self.Center()

    def OnDownBtnClicked(self, event):
        try:
            downPath = self.downPath.GetDownPath()
            source_url = self.downEdit.GetBaseURI()
            task = self.tasks.create_mp4(downPath, source_url, filename=self.downPath.GetFileName())
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f'创建 MP4 任务失败：{error}', '提示', wx.OK|wx.ICON_WARNING, parent=self)
            return
        self.task_id = task.id
        self.EndModal(wx.OK)
