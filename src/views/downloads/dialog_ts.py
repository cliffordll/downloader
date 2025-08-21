import wx

from src.views.downloads.edit_ts import DownloadEditTS
from src.views.downloads.download_path import DownloadPath
from src.managers.file_manager import FileManager

class DownloadDialogTS(wx.Dialog):
    def __init__(self, parent, title, workPath):
        # super(ModalDialog, self).__init__(parent, title=title)
        super().__init__(parent=parent)
        self.SetTitle(title)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.downEdit = DownloadEditTS(self)
        self.downPath = DownloadPath(self, workPath)
        self.downEdit.Bind(wx.EVT_BUTTON, self.OnEditBtnClicked)
        self.downPath.Bind(wx.EVT_BUTTON, self.OnDownBtnClicked)

        sizer.Add(self.downEdit, proportion=100, flag=wx.ALIGN_CENTER|wx.ALL, border=5)
        sizer.Add(self.downPath, proportion=1, flag=wx.ALIGN_CENTER|wx.ALL, border=5)
 
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
        downPath = self.downPath.GetDownPath() 
        print("OnDownBtnClicked", downPath)

        # 获取下载参数
        baseUri = self.downEdit.GetBaseURI()
        basePath = self.downEdit.GetBasePath()
        content = self.downEdit.GetContent()

        # 创建下载种子
        flag = FileManager().CreateSeedFile(downPath, basePath, baseUri, content)
        if flag:
            wx.MessageBox(f"下载任务创建成功，请到首页查看。", "提示", wx.ICON_INFORMATION)
            # self.OnClose(None)
            self.EndModal(wx.OK)
        else:
            wx.MessageBox(f"下载任务创建失败，请稍后重试。", "提示", wx.ICON_WARNING)