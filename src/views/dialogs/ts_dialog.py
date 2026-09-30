import wx

from src.views.dialogs.panels.ts_form import DownloadEditTS
from src.views.dialogs.panels.path_picker import DownloadPath
import sqlite3
from src.core.task_service import TaskService
from src.storage.task_repository import TaskDataError
from src.schemas.task import SourceType
from src.core.sys_setting import SysSetting

class DownloadDialogTS(wx.Dialog):
    def __init__(self, parent, title, workPath, task_service: TaskService):
        # super(ModalDialog, self).__init__(parent, title=title)
        super().__init__(parent=parent)
        self.tasks = task_service  # 复用主窗口传入的服务，不创建另一份任务库入口。
        self.SetTitle(title)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.downEdit = DownloadEditTS(self)
        self.downPath = DownloadPath(self, workPath)
        self.downEdit.Bind(wx.EVT_BUTTON, self.OnEditBtnClicked)
        self.downPath.Bind(wx.EVT_BUTTON, self.OnDownBtnClicked)

        # 两块之间只保留保存目录行的顶部边距，避免多层底部边距叠加。
        sizer.Add(self.downEdit, proportion=1, flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(self.downPath, proportion=0, flag=wx.EXPAND|wx.ALL, border=5)
 
        self.SetSizer(sizer)
        # self.SetSize(width=728, height=450)
        # self.SetSize(width=728, height=600)
        # self.SetSize(width=1024, height=700)
        self.SetSize(width=960, height=600)
        # self.Fit()
        self.Center()

    def OnClose(self, event):
        self.Destroy()

    def OnEditBtnClicked(self, event):
        baseUri = self.downEdit.GetBaseURI()
        self.downPath.SetDownPath(baseUri=baseUri)

    def OnDownBtnClicked(self, event):

        # 获取下载参数
        baseUri = self.downEdit.GetBaseURI()
        basePath = self.downEdit.GetBasePath()
        content = self.downEdit.GetContent()

        try:
            detect_duration = self.downEdit.detectDuration.GetValue()
            if detect_duration and not SysSetting.GetFFprobe():
                raise ValueError('找不到 ffprobe，请放入 scripts 或 FFmpeg 所在目录，或取消检测时长。')
            downPath = self.downPath.GetDownPath()
            self.tasks.create_m3u8(downPath, baseUri, content, basePath,
                                   source_type=SourceType.TS_PATTERN,
                                   ts_pattern=self.downEdit.tcReg.GetValue().strip() or None,
                                   detect_duration=detect_duration,
                                   request_headers=self.downEdit.GetRequestHeaders())
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f"下载任务创建失败：{error}", "提示", wx.ICON_WARNING, parent=self)
            return
        self.EndModal(wx.OK)
