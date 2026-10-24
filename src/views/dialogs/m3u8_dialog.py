import wx

from src.views.dialogs.panels.m3u8_form import DownloadEditMU
from src.views.dialogs.panels.path_picker import DownloadPath
import sqlite3
from src.core.task_service import TaskService
from src.storage.task_repository import TaskDataError
from src.views.dialogs.base_dialog import AppDialog


class DownloadDialogMU(AppDialog):
    def __init__(self, parent, work_path, task_service: TaskService, *, title='新建 M3U8 下载', capture=None):
        # super(ModalDialog, self).__init__(parent, title=title)
        super().__init__(parent=parent, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.tasks = task_service  # 复用主窗口传入的服务，不创建另一份任务库入口。
        self.request_headers = {'Referer': capture['referer']} if capture and capture.get('referer') else {}
        self.SetTitle(title)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.downEdit = DownloadEditMU(self)
        self.downEdit.request_headers = self.request_headers
        self.downPath = DownloadPath(self, work_path)
        self.downEdit.Bind(wx.EVT_BUTTON, self.OnEditBtnClicked)
        self.downPath.Bind(wx.EVT_BUTTON, self.OnDownBtnClicked)
        self.downEdit.tcURI.Bind(wx.EVT_TEXT, self.downPath.OnSourceChanged)
        if capture:
            self.downEdit.tcURI.SetValue(capture['url'])
            self.downPath.SetDownPath(capture['url'])

        # sizer.Add(uriSizer, flag=wx.ALIGN_CENTER|wx.TOP|wx.BOTTOM, border=5)
        panel_flag = wx.EXPAND
        sizer.Add(self.downEdit, proportion=100, flag=panel_flag|wx.ALL, border=self.dialog_spacing)
        # sizer.Add(btnSizer, proportion=1, flag=wx.ALIGN_CENTER|wx.TOP|wx.BOTTOM, border=5)
        sizer.Add(self.downPath, proportion=0, flag=panel_flag|wx.ALL, border=self.dialog_spacing)
 
        self.SetSizer(sizer)
        # self.SetSize(width=728, height=450)
        self.SetSize(self.FromDIP((728, 600)))
        self.SetMinSize(self.FromDIP((640, 400)))
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
            self.tasks.create_m3u8(downPath, baseUri, content, basePath,
                                   filename=self.downPath.GetFileName(), request_headers=self.request_headers)
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f"下载任务创建失败：{error}", "提示", wx.ICON_WARNING, parent=self)
            return
        self.EndModal(wx.OK)
