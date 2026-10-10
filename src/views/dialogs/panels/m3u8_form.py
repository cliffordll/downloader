import wx
from threading import Thread
from urllib.parse import urljoin, urlsplit

from src.views.dialogs.panels.path_picker import DownloadHelpDialog, form_label_size
from src.media.m3u8.m3u8_parser import M3U8Parser
from src.media.m3u8.m3u8_downloader import M3U8Downloader
from src.views.components.help_button import HelpButton
from src.views.components.playlist_editor import PlaylistEditor

class DownloadEditMU(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent=parent, id=wx.ID_ANY)
        wx.ToolTip.SetAutoPop(30000)
        sizer = wx.BoxSizer(wx.VERTICAL)

        uriSizer = wx.BoxSizer(wx.HORIZONTAL)
        lblUri = wx.StaticText(self, -1, label="列表网址：", size=form_label_size(self, 60), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        self.tcURI = wx.TextCtrl(self)
        self.tcURI.SetHint("粘贴 M3U8 完整网址")
        self.tcURI.SetToolTip("填写 M3U8 完整网址，用于获取列表；相对 TS 地址按该网址所在目录补全。")
        uriSizer.Add(lblUri, proportion=0 if wx.Platform == '__WXMAC__' else 1, flag=wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL|wx.BOTTOM|wx.RIGHT, border=self.FromDIP(5))
        uriSizer.Add(self.tcURI, proportion=50, flag=wx.EXPAND|wx.ALIGN_LEFT|wx.BOTTOM|wx.LEFT, border=self.FromDIP(5))
        self.uriHelpButton = HelpButton(self, "列表网址说明", self.OnURIHelp)
        uriSizer.Add(self.uriHelpButton, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT|wx.BOTTOM, border=self.FromDIP(5))

        pathSizer = wx.BoxSizer(wx.HORIZONTAL)
        lblPath = wx.StaticText(self, -1, label="路径改写：", size=form_label_size(self, 60), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        self.tcPath = wx.TextCtrl(self)
        self.tcPath.SetHint("通常留空；需要更改 TS 分片所在目录时填写")
        self.tcURI.Bind(wx.EVT_TEXT, self._UpdatePathToolTip)
        self._UpdatePathToolTip()
        pathSizer.Add(lblPath, proportion=0 if wx.Platform == '__WXMAC__' else 1, flag=wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL|wx.TOP|wx.BOTTOM|wx.RIGHT, border=self.FromDIP(5))
        pathSizer.Add(self.tcPath, proportion=50, flag=wx.EXPAND|wx.ALIGN_LEFT|wx.TOP|wx.BOTTOM|wx.LEFT, border=self.FromDIP(5))
        self.pathHelpButton = HelpButton(self, "路径改写说明", self.OnPathHelp)
        pathSizer.Add(self.pathHelpButton, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT, border=self.FromDIP(5))

        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        btnM3U8 = wx.Button(self, label="获取 M3U8")
        self.fetchButton = btnM3U8
        self._fetching = False
        btnSizer.AddStretchSpacer(prop=84)
        btnSizer.Add(btnM3U8, proportion=1, flag=wx.EXPAND|wx.TOP|wx.BOTTOM, border=self.FromDIP(5))

        listSizer = wx.BoxSizer(wx.VERTICAL)
        # 独立行号栏随正文滚动，保留文本编辑、选中高亮和复制功能。
        # self.tsList = wx.TextCtrl(self, style=wx.TE_MULTILINE|wx.TE_LEFT|wx.TE_RICH2)
        self.tsList = PlaylistEditor(self)
        listSizer.Add(self.tsList, proportion=10, flag=wx.EXPAND|wx.TOP, border=self.FromDIP(5))

        sizer.Add(uriSizer, flag=wx.EXPAND if wx.Platform == '__WXMAC__' else 0, border=0)
        sizer.Add(pathSizer, flag=wx.EXPAND if wx.Platform == '__WXMAC__' else 0, border=0)
        sizer.Add(btnSizer, border=0)
        sizer.Add(listSizer, proportion=10, flag=wx.EXPAND, border=0)
        self.SetSizer(sizer)

        btnM3U8.Bind(wx.EVT_BUTTON, self.OnBtnM3U8Clicked)
        self._SetDefaultValue()

    def OnURIHelp(self, event):
        address = self.GetBaseURI()
        text = "填写 M3U8 播放列表的完整网址。\n点击“获取 M3U8”，读取该网址的播放列表内容。"
        text += f"\n\n当前填写：\n{address or '未填写'}"

        dialog = DownloadHelpDialog(self, "列表网址说明", text)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()

    def OnPathHelp(self, event):
        self._UpdatePathToolTip()
        dialog = DownloadHelpDialog(self, "路径改写说明", self.tcPath.GetToolTip().GetTip())
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()

    def _UpdatePathToolTip(self, event=None):
        tip = ("通常留空。需要改写分片地址时，有两种填写方式：\n\n"
               "1. 填写目录路径\n"
               "填写 chunks：a.ts 或 other/a.ts 都会改为 chunks/a.ts。\n"
               "填写 /chunks：表示从网站根目录下的 chunks 目录下载。\n\n"
               "2. 填写完整目录网址\n"
               "直接指定分片所在的服务器和目录，也适用于分片在其他服务器的情况。")
        try:
            address = self.tcURI.GetValue().strip()
            parsed = urlsplit(address)
            if parsed.scheme.lower() in ("http", "https") and parsed.netloc:
                relative_example = urljoin(address, "chunks/a.ts")
                root_example = urljoin(address, "/chunks/a.ts")
                marker = "\n\n2. 填写完整目录网址"
                examples = (f"\n\n以分片 a.ts 为例：\n"
                            f"填写 chunks → {relative_example}\n"
                            f"填写 /chunks → {root_example}")
                tip = tip.replace(marker, examples + marker, 1)
                example = urljoin(address, "chunks")
                tip += f"\n例如填写：\n{example}"
                tip += f"\n分片 a.ts 的下载地址为：\n{example}/a.ts"
        except ValueError:
            pass
        self.tcPath.SetToolTip(tip)
        if event is not None:
            event.Skip()

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
        if self._fetching:
            return
        m3u8Url = self.GetBaseURI()
        if not m3u8Url:
            wx.MessageBox("请输入正确的M3U8下载地址！", "警告", wx.OK|wx.ICON_WARNING)
            return

        self._fetching = True
        self.fetchButton.Disable()
        self.fetchButton.SetLabel('获取中…')
        # 网络请求和限流等待放在后台；工作线程不访问 wx 控件。
        def fetch():
            try:
                flag, content = M3U8Downloader.DownloadContent(m3u8Url)
                if flag and isinstance(content, bytes):
                    content = content.decode('utf-8-sig', errors='replace')
            except Exception as error:
                flag, content = False, str(error)
            try:
                wx.CallAfter(self._OnFetched, m3u8Url, flag, content)
            except RuntimeError:
                pass  # 应用已经退出，不能再投递界面回调。
        try:
            Thread(target=fetch, name='playlist-fetch', daemon=True).start()
        except RuntimeError as error:
            self._OnFetched(m3u8Url, False, str(error))

    def _OnFetched(self, address, flag, content):
        """主线程更新表单；关闭弹窗或更改网址后不再应用旧请求的结果。"""
        if not self or self.IsBeingDeleted():
            return
        window = self.GetTopLevelParent()
        if not window or window.IsBeingDeleted():
            return  # 父弹窗可能正在延迟销毁，子控件此时仍然存在。
        self._fetching = False
        self.fetchButton.Enable()
        self.fetchButton.SetLabel('获取 M3U8')
        if address != self.GetBaseURI():
            return
        if flag:
            self.tsList.SetValue(content)
            # 原始点击事件已结束；成功后重新通知父弹窗生成保存路径。
            event = wx.CommandEvent(wx.EVT_BUTTON.typeId, self.fetchButton.GetId())
            event.SetEventObject(self.fetchButton)
            wx.PostEvent(self, event)
        else:
            wx.MessageBox(content, "警告", wx.OK|wx.ICON_WARNING, parent=self)
