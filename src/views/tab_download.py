import wx
from src.views.downloads.m3u8_uri_download import M3U8URIDownload
from src.views.downloads.m3u8_ts_download import M3U8TSDownload
from src.managers.sys_setting import SysSetting
from src.managers.file_manager import FileManager

class TabDownload(wx.Panel):
    """
    This will be the second notebook tab
    """
    def __init__(self, parent):
        super().__init__(parent=parent, id=wx.ID_ANY)
        # 主布局
        sizer = wx.BoxSizer(wx.VERTICAL)

        # 下载详情页
        self.books = wx.Notebook(self)
        uriDown = M3U8URIDownload(self.books)
        tsDown = M3U8TSDownload(self.books)
        self.books.AddPage(uriDown, "M3U8 下载")
        self.books.AddPage(tsDown, "TS 下载")
        self.books.SetSelection(0)
        uriDown.Bind(wx.EVT_BUTTON, self.OnBookBtnClicked)
        tsDown.Bind(wx.EVT_BUTTON, self.OnBookBtnClicked)
        
        # 下载按钮行
        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        lblName = wx.StaticText(self, -1, label="下载地址：", size=(60, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        
        # 根据内容自动调整宽度
        workPath = SysSetting.GetWorkPath()
        dc = wx.ClientDC(self)
        width, height = dc.GetTextExtent(workPath)
        # self.lblPath.SetSize((width + 10, height + 10))  # 加一些边距
        # self._SetDefaultValue()
        self.lblPath = wx.StaticText(self, -1, label=workPath, size=(width, height), style=wx.ALIGN_LEFT|wx.VERTICAL|wx.ST_NO_AUTORESIZE)
        self.lblPath.SetBackgroundColour(wx.LIGHT_GREY)
        self.tcDown = wx.TextCtrl(self)

        btnDown = wx.Button(self, label="下载")
        btnDown.Bind(wx.EVT_BUTTON, self.OnBtnDownClicked)
        btnSizer.Add(lblName, flag=wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL|wx.TOP|wx.LEFT|wx.RIGHT, border=5)
        btnSizer.Add(self.lblPath, proportion=5, flag=wx.ALIGN_CENTER_VERTICAL|wx.TOP|wx.LEFT, border=5)
        btnSizer.Add(self.tcDown, proportion=100, flag=wx.EXPAND|wx.ALIGN_LEFT|wx.TOP|wx.RIGHT, border=5)
        # btnSizer.AddStretchSpacer(prop=100)
        btnSizer.Add(btnDown, proportion=1, flag=wx.ALIGN_LEFT|wx.TOP|wx.LEFT, border=5)

        # 主布局
        sizer.Add(self.books, proportion=1, flag=wx.EXPAND|wx.ALL, border=0)
        sizer.Add(btnSizer, border=0)
        self.SetSizer(sizer)


    def _SetDefaultValue(self):
        '''测试提供个默认值'''
        # m3u8_url = "https://yzzy.play-cdn10.com/20230104/21337_a024ad0f/1000k/hls/mixed.m3u8"
        workPath = SysSetting.GetWorkPath()
        self.lblPath.SetLabel(workPath)
        # self.lblPath.Fit()  # 调整控件大小以适应内容
        # self.lblPath.Wrap(-1)   # -1 表示根据内容自动调整宽度
        # # self.tcDown.SetValue(workPath)


    def GetDownPath(self):
        downPath = self.lblPath.GetLabel().strip()
        downPath += self.tcDown.GetValue().strip()
        print(downPath)
        return downPath

    def OnBookBtnClicked(self, event):
        '''提供默认下载路径'''
        baseUri = self.books.GetCurrentPage().GetBaseURI()

        filePath, fileName, absName = FileManager.GetPathFromURI(baseUri=baseUri)
        # print("TabDownload.OnBtnDownClicked", filePath)
        # print("TabDownload.OnBtnDownClicked", fileName)
        # print("TabDownload.OnBtnDownClicked", absName)
        self.tcDown.SetValue(filePath)

    def OnBtnDownClicked(self, event):
        """按钮点击事件处理函数"""
        downPath = self.GetDownPath()
        # 获取下载参数
        baseUri = self.books.GetCurrentPage().GetBaseURI()
        basePath = self.books.GetCurrentPage().GetBasePath()
        content = self.books.GetCurrentPage().GetContent()

        # 创建下载种子
        flag = FileManager().CreateSeedFile(downPath, basePath, baseUri, content)
        if flag:
            wx.MessageBox(f"下载任务创建成功，请到首页查看。", "提示", wx.ICON_INFORMATION)
        else:
            wx.MessageBox(f"下载任务创建失败，请稍后重试。", "提示", wx.ICON_WARNING)