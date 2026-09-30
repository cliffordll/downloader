import wx

from src.views.dialogs.panels.m3u8_form import DownloadEditMU
from src.views.dialogs.panels.path_picker import DownloadPath
import sqlite3
from src.core.task_service import TaskService
from src.storage.task_repository import TaskDataError

class DownloadDialogMU(wx.Dialog):
    def __init__(self, parent, title, workPath, task_service: TaskService):
        # super(ModalDialog, self).__init__(parent, title=title)
        super().__init__(parent=parent)
        self.tasks = task_service  # 复用主窗口传入的服务，不创建另一份任务库入口。
        self.SetTitle(title)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.downEdit = DownloadEditMU(self)
        self.downPath = DownloadPath(self, workPath)
        self.downEdit.Bind(wx.EVT_BUTTON, self.OnEditBtnClicked)
        self.downPath.Bind(wx.EVT_BUTTON, self.OnDownBtnClicked)

        # sizer.Add(uriSizer, flag=wx.ALIGN_CENTER|wx.TOP|wx.BOTTOM, border=5)
        sizer.Add(self.downEdit, proportion=100, flag=wx.ALIGN_CENTER|wx.ALL, border=5)
        # sizer.Add(btnSizer, proportion=1, flag=wx.ALIGN_CENTER|wx.TOP|wx.BOTTOM, border=5)
        sizer.Add(self.downPath, proportion=1, flag=wx.ALIGN_CENTER|wx.ALL, border=5)
 
        self.SetSizer(sizer)
        # self.SetSize(width=728, height=450)
        self.SetSize(width=728, height=600)
        # self.Fit()
        self.Center()

        # # 设置对话框为模态
        # self.ShowModal()

    def OnClose(self, event):
        self.Destroy()

    def OnEditBtnClicked(self, event):
        baseUri = self.downEdit.GetBaseURI()
        self.downPath.SetDownPath(baseUri=baseUri)

    def OnDownBtnClicked(self, event):
        # print("OnDownBtnClicked", downPath)

        # 获取下载参数
        baseUri = self.downEdit.GetBaseURI()
        basePath = self.downEdit.GetBasePath()
        content = self.downEdit.GetContent()

        try:
            downPath = self.downPath.GetDownPath()
            self.tasks.create_m3u8(downPath, baseUri, content, basePath)
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f"下载任务创建失败：{error}", "提示", wx.ICON_WARNING, parent=self)
            return
        self.EndModal(wx.OK)
