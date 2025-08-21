import wx

from src.managers.file_manager import FileManager
from src.managers.m3m8_parser import M3U8Parser
from src.managers.downloader import Downloader

class DownloadEditMU(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent=parent, id=wx.ID_ANY)
        sizer = wx.BoxSizer(wx.VERTICAL)

        uriSizer = wx.BoxSizer(wx.HORIZONTAL)
        lblUri = wx.StaticText(self, -1, label="Base URI:", size=(60, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        self.tcURI = wx.TextCtrl(self)
        uriSizer.Add(lblUri, proportion=1, flag=wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL|wx.BOTTOM|wx.RIGHT, border=5) 
        uriSizer.Add(self.tcURI, proportion=50, flag=wx.EXPAND|wx.ALIGN_LEFT|wx.BOTTOM|wx.LEFT, border=5)

        pathSizer = wx.BoxSizer(wx.HORIZONTAL)
        lblPath = wx.StaticText(self, -1, label="Base Path:", size=(60, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        self.tcPath = wx.TextCtrl(self)
        pathSizer.Add(lblPath, proportion=1, flag=wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL|wx.TOP|wx.BOTTOM|wx.RIGHT, border=5) 
        pathSizer.Add(self.tcPath, proportion=50, flag=wx.EXPAND|wx.ALIGN_LEFT|wx.TOP|wx.BOTTOM|wx.LEFT, border=5)

        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        btnM3U8 = wx.Button(self, label="获取 M3U8")
        btnSizer.AddStretchSpacer(prop=84)
        btnSizer.Add(btnM3U8, proportion=1, flag=wx.EXPAND|wx.TOP|wx.BOTTOM, border=5)

        listSizer = wx.BoxSizer(wx.VERTICAL)
        self.tsList = wx.TextCtrl(self, style=wx.TE_MULTILINE|wx.TE_LEFT|wx.TE_RICH2)
        listSizer.Add(self.tsList, proportion=10, flag=wx.EXPAND|wx.TOP, border=5)

        sizer.Add(uriSizer, border=0)
        sizer.Add(pathSizer, border=0)
        sizer.Add(btnSizer, border=0)
        sizer.Add(listSizer, proportion=10, flag=wx.EXPAND, border=0)
        self.SetSizer(sizer)

        btnM3U8.Bind(wx.EVT_BUTTON, self.OnBtnM3U8Clicked)
        self._SetDefaultValue()

    def _SetDefaultValue(self):
        '''测试提供个默认值'''
        m3u8_url = "https://yzzy.play-cdn10.com/20230104/21337_a024ad0f/1000k/hls/mixed.m3u8"
        # m3u8_url = "http://127.0.0.1:8000/videos/2025/test2/index.m3u8"
        # m3u8_url = "https://test-streams.mux.dev/x36xhzz/url_2/193039199_mp4_h264_aac_ld_7.m3u8"
        self.tcURI.SetValue(m3u8_url)

    def GetBaseURI(self):
        return self.tcURI.GetValue().strip()
    
    def GetBasePath(self):
        return self.tcPath.GetValue().strip()

    def GetContent(self):
        return self.tsList.GetValue().strip()


    def OnBtnM3U8Clicked(self, event):
        # text = """第一行文本\n第二行文本
        #     第三行文本...
        #     可以显示大量文本内容，支持滚动查看"""
        m3u8Url = self.tcURI.GetValue()
        if not m3u8Url:
            wx.MessageBox("请输入正确的M3U8下载地址！", "警告", wx.OK|wx.ICON_WARNING)
            return

        flag, content = Downloader.DownloadContent(m3u8Url)
        if flag:
            self.tsList.SetValue(content)
            # 传递事件，通知下载页修改下载路径
            event.Skip()
        else:
            wx.MessageBox(content, "警告", wx.OK|wx.ICON_WARNING)