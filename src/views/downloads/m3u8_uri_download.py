import wx

from src.managers.file_manager import FileManager
from src.managers.m3m8_parser import M3U8Parser
from src.managers.downloader import Downloader

class M3U8URIDownload(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent=parent, id=wx.ID_ANY)
        # self.SetBackgroundColour(wx.RED)
        sizer = wx.BoxSizer(wx.VERTICAL)

        uriSizer = wx.BoxSizer(wx.HORIZONTAL)
        lblUri = wx.StaticText(self, -1, label="Base URI:", size=(60, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        # lblUri.SetBackgroundColour(wx.RED)
        self.tcURI = wx.TextCtrl(self)
        # btnM3U8 = wx.Button(self, label="获取 M3U8")
        # print(btnM3U8.GetSize())
        uriSizer.Add(lblUri, proportion=1, flag=wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL|wx.ALL, border=5) 
        uriSizer.Add(self.tcURI, proportion=50, flag=wx.EXPAND|wx.ALIGN_LEFT|wx.ALL, border=5)
        # uriSizer.Add(btnM3U8, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)
        # uriSizer.AddStretchSpacer(prop=84)
        # uriSizer.AddSpacer(84+10)       # 占位Buttion大小 和 两个border
        # uriSizer.Add(btnM3U8, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)

        pathSizer = wx.BoxSizer(wx.HORIZONTAL)
        lblPath = wx.StaticText(self, -1, label="Base Path:", size=(60, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        # lblUri.SetBackgroundColour(wx.RED)
        self.tcPath = wx.TextCtrl(self)
        # btnM3U8 = wx.Button(self, label="获取 M3U8")
        pathSizer.Add(lblPath, proportion=1, flag=wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL|wx.ALL, border=5) 
        pathSizer.Add(self.tcPath, proportion=50, flag=wx.EXPAND|wx.ALIGN_LEFT|wx.ALL, border=5)
        # pathSizer.Add(btnM3U8, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)

        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        btnM3U8 = wx.Button(self, label="获取 M3U8")
        btnSizer.AddStretchSpacer(prop=84)
        btnSizer.Add(btnM3U8, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)

        # self.tsList = wx.TextCtrl(self, style=wx.TE_MULTILINE|wx.TE_LEFT|wx.TE_READONLY|wx.TE_RICH2)
        listSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.tsList = wx.TextCtrl(self, style=wx.TE_MULTILINE|wx.TE_LEFT|wx.TE_RICH2)
        # listSizer.Add(self.tsList, proportion=10, flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        listSizer.Add(self.tsList, proportion=10, flag=wx.EXPAND|wx.ALL, border=5)

        # # Download
        # btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        # self.lblStatus = wx.StaticText(self, -1, label="", size=(200, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        # self.lblStatus.SetBackgroundColour(wx.GREEN)
        # btnDown = wx.Button(self, label="下载")
        
        # btnSizer.Add(self.lblStatus, flag=wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL|wx.ALL, border=5)
        # btnSizer.AddStretchSpacer(prop=100)
        # btnSizer.Add(btnDown, proportion=1, flag=wx.ALIGN_LEFT|wx.ALL, border=5)

        sizer.Add(uriSizer, border=0)
        sizer.Add(pathSizer, border=0)
        sizer.Add(btnSizer, border=0)
        # sizer.Add(self.tsList, proportion=10, flag=wx.EXPAND, border=5)
        sizer.Add(listSizer, proportion=10, flag=wx.EXPAND, border=0)
        # sizer.Add(btnSizer, border=0)
        self.SetSizer(sizer)

        btnM3U8.Bind(wx.EVT_BUTTON, self.OnBtnM3U8Clicked)
        # btnDown.Bind(wx.EVT_BUTTON, self.OnBtnDownClicked)
        self._SetDefaultValue()

    def _SetDefaultValue(self):
        '''测试提供个默认值'''
        # m3u8_url = "https://yzzy.play-cdn10.com/20230104/21337_a024ad0f/1000k/hls/mixed.m3u8"
        m3u8_url = "http://127.0.0.1:8000/videos/2025/test2/index.m3u8"
        # m3u8_url = "https://test-streams.mux.dev/x36xhzz/url_2/193039199_mp4_h264_aac_ld_7.m3u8"
        self.tcURI.SetValue(m3u8_url)

    def GetBaseURI(self):
        return self.tcURI.GetValue().strip()
    
    def GetBasePath(self):
        return self.tcPath.GetValue().strip()

    def GetContent(self):
        return self.tsList.GetValue().strip()

    # def GetSeedContent(self):
    #     m3u8Uri = self.uriBase.GetValue()
    #     content = self.tsList.GetValue()
    #     if not m3u8Uri:
    #         wx.MessageBox("请输入正确的m3u8下载地址！", "警告", wx.OK|wx.ICON_WARNING)
    #         return False, "", ""
    #     if not content:
    #         wx.MessageBox("请点击“获取 M3U8”按钮，下载m3u8内容！", "警告", wx.OK|wx.ICON_WARNING)
    #         return False, "", ""
        
    #     # 解析 m3u8 内容是否合法
    #     try:
    #         parser = M3U8Parser(content=content, m3u8_uri=m3u8Uri)
    #         isAbs, tsList = parser.parse_media()
    #         # print(tsList)
    #         if len(tsList) <= 0:
    #             wx.MessageBox("M3U8内容解析错误！未找到TS文件", "警告", wx.OK|wx.ICON_WARNING)
    #             return False, "", ""
    #         return True, m3u8Uri, content
    #     except Exception as ex:
    #         print(f"M3U8URIDownload.CreateSeedFile except:{str(ex)}")
    #     return False, "", ""

    # def CreateSeedFile(self):
    #     return FileManager().CreateSeedFile(baseUri=m3u8Uri, content=content.encode())

    def OnBtnM3U8Clicked(self, event):
        # text = """第一行文本\n第二行文本
        #     第三行文本...
        #     可以显示大量文本内容，支持滚动查看"""
        m3u8Url = self.tcURI.GetValue()
        if not m3u8Url:
            wx.MessageBox("请输入正确的m3u8下载地址！", "警告", wx.OK|wx.ICON_WARNING)
            return

        content = Downloader.DownloadContent(m3u8Url)
        self.tsList.SetValue(content)

        # 传递事件，通知下载页修改下载路径
        event.Skip()

    # def OnBtnDownClicked(self, event):
    #     """按钮点击事件处理函数"""
    #     nLine = len(self.GetTSList())
    #     wx.MessageBox(f"即将下载{nLine}个TS文件！", "提示", wx.OK|wx.ICON_INFORMATION)