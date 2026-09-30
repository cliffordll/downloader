import wx
import wx.adv
from urllib.parse import urljoin, urlsplit
import re

from src.views.dialogs.panels.path_picker import DownloadHelpDialog
from src.media.m3u8.m3u8_parser import M3U8Parser
from src.views.components.playlist_editor import PlaylistEditor

class DownloadEditTS(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent=parent, id=wx.ID_ANY)
        wx.ToolTip.SetAutoPop(30000)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # base uri
        uriSizer = wx.BoxSizer(wx.HORIZONTAL)
        lblUri = wx.StaticText(self, -1, label="列表网址：", size=(60, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        self.tcURI = wx.TextCtrl(self)
        self.tcURI.SetHint("填写参考列表网址，用于补全 TS 相对地址")
        self.tcURI.SetToolTip("用于补全手动添加的 TS 相对地址。本窗口不会获取此网址；分片均为完整下载网址时可留空。")
        uriSizer.Add(lblUri, proportion=1, flag=wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL|wx.BOTTOM|wx.RIGHT, border=5)
        uriSizer.Add(self.tcURI, proportion=50, flag=wx.EXPAND|wx.ALIGN_LEFT|wx.BOTTOM|wx.LEFT, border=5)
        self.uriHelpButton = wx.adv.HyperlinkCtrl(self, label="?", url="", size=self.FromDIP((24, -1)), style=wx.adv.HL_ALIGN_CENTRE)
        self.uriHelpButton.SetNormalColour(wx.Colour("#666666"))
        self.uriHelpButton.SetVisitedColour(wx.Colour("#666666"))
        self.uriHelpButton.SetHoverColour(wx.Colour("#333333"))
        helpFont = self.uriHelpButton.GetFont()
        helpFont.SetUnderlined(False)
        self.uriHelpButton.SetFont(helpFont)
        self.uriHelpButton.SetName("列表网址说明")
        self.uriHelpButton.SetToolTip("点击查看完整说明")
        self.uriHelpButton.Bind(wx.adv.EVT_HYPERLINK, self.OnURIHelp)
        uriSizer.Add(self.uriHelpButton, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT|wx.BOTTOM, border=5)

        pathSizer = wx.BoxSizer(wx.HORIZONTAL)
        lblPath = wx.StaticText(self, -1, label="路径改写：", size=(60, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        self.tcPath = wx.TextCtrl(self)
        self.tcPath.SetHint("通常留空；需要更改 TS 分片所在目录时填写")
        self.tcURI.Bind(wx.EVT_TEXT, self._UpdatePathToolTip)
        self._UpdatePathToolTip()
        pathSizer.Add(lblPath, proportion=1, flag=wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL|wx.TOP|wx.BOTTOM|wx.RIGHT, border=5)
        pathSizer.Add(self.tcPath, proportion=50, flag=wx.EXPAND|wx.ALIGN_LEFT|wx.TOP|wx.BOTTOM|wx.LEFT, border=5)
        self.pathHelpButton = wx.adv.HyperlinkCtrl(self, label="?", url="", size=self.FromDIP((24, -1)), style=wx.adv.HL_ALIGN_CENTRE)
        self.pathHelpButton.SetNormalColour(wx.Colour("#666666"))
        self.pathHelpButton.SetVisitedColour(wx.Colour("#666666"))
        self.pathHelpButton.SetHoverColour(wx.Colour("#333333"))
        helpFont = self.pathHelpButton.GetFont()
        helpFont.SetUnderlined(False)
        self.pathHelpButton.SetFont(helpFont)
        self.pathHelpButton.SetName("路径改写说明")
        self.pathHelpButton.SetToolTip("点击查看完整说明")
        self.pathHelpButton.Bind(wx.adv.EVT_HYPERLINK, self.OnPathHelp)
        pathSizer.Add(self.pathHelpButton, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT, border=5)

        # ts start and end
        lblPlay = wx.StaticText(self, -1, label="播放时长:", size=(60, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        # self.tcPlay = wx.TextCtrl(self, size=(60, -1), style=wx.TE_PROCESS_ENTER)
        self.tcPlay = wx.TextCtrl(self)
        tsSizer = wx.BoxSizer(wx.HORIZONTAL)
        lblReg = wx.StaticText(self, -1, label="段名规则:", size=(60, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        self.tcReg = wx.TextCtrl(self)
        self.tcReg.SetToolTip('使用 {idx} 表示编号；{idx:03d} 表示至少三位，例如 001、002、010。')
        lblStart = wx.StaticText(self, -1, label="开始:", size=(30, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        self.tcStart = wx.TextCtrl(self)
        lblEnd = wx.StaticText(self, -1, label="结束:", size=(30, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        self.tcEnd = wx.TextCtrl(self)        
        btnAppend = wx.Button(self, label="添加 TS")
        self.detectDuration = wx.CheckBox(self, label='')
        self.detectDuration.SetName('检测时长')
        detectLabel = wx.StaticText(self, label='检测时长', style=wx.ST_NO_AUTORESIZE)
        for control in (self.detectDuration, detectLabel):
            control.SetToolTip('下载后用 ffprobe 检测实际时长；失败保留填写值，不影响下载。')
        # 文字仍可点击切换，键盘焦点交给原生复选框，保留空格键切换能力。
        def toggleDuration(event):
            self.detectDuration.SetValue(not self.detectDuration.GetValue())
            self.detectDuration.SetFocus()
        detectLabel.Bind(wx.EVT_LEFT_UP, toggleDuration)

        tsSizer.Add(lblPlay, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL|wx.TOP|wx.BOTTOM|wx.RIGHT, border=5)
        # 横向分配宽度，纵向保留控件默认高度并居中，避免输入框被按钮撑高。
        tsSizer.Add(self.tcPlay, proportion=40, flag=wx.ALIGN_CENTER_VERTICAL|wx.ALL, border=5)
        # 与本行其他标签使用相同的 StaticText 绘制文字，不再靠平台像素偏移补偿。
        detectSizer = wx.BoxSizer(wx.HORIZONTAL)
        detectSizer.Add(self.detectDuration, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.FromDIP(2))
        detectSizer.Add(detectLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        tsSizer.Add(detectSizer, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT|wx.RIGHT, border=5)
        tsSizer.AddStretchSpacer(prop=2)
        tsSizer.Add(lblReg, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL|wx.ALL, border=5) 
        tsSizer.Add(self.tcReg, proportion=40, flag=wx.ALIGN_CENTER_VERTICAL|wx.ALL, border=5)
        tsSizer.AddStretchSpacer(prop=2)
        tsSizer.Add(lblStart, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL|wx.ALL, border=5) 
        tsSizer.Add(self.tcStart, proportion=10, flag=wx.ALIGN_CENTER_VERTICAL|wx.ALL, border=5)
        tsSizer.AddStretchSpacer(prop=2)
        tsSizer.Add(lblEnd, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL|wx.ALL, border=5) 
        tsSizer.Add(self.tcEnd, proportion=10, flag=wx.ALIGN_CENTER_VERTICAL|wx.ALL, border=5)
        tsSizer.AddStretchSpacer(prop=2)
        tsSizer.Add(btnAppend, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL|wx.TOP|wx.BOTTOM|wx.LEFT, border=5)

        # m3u8 file
        listSizer = wx.BoxSizer(wx.HORIZONTAL)
        # 与 M3U8 窗口共用带行号的编辑器，行号不混入生成或复制的正文。
        # self.tsList = wx.TextCtrl(self, style=wx.TE_MULTILINE|wx.TE_LEFT|wx.TE_RICH2)
        self.tsList = PlaylistEditor(self)
        listSizer.Add(self.tsList, proportion=10, flag=wx.EXPAND|wx.TOP, border=5)

        sizer.Add(uriSizer, flag=wx.EXPAND, border=0)
        sizer.Add(pathSizer, flag=wx.EXPAND, border=0)
        sizer.Add(tsSizer, flag=wx.EXPAND, border=0)
        sizer.Add(listSizer, proportion=10, flag=wx.EXPAND, border=0)

        # 请求头是任务选项；默认收起，给清单编辑区保留空间。
        self.advanced = wx.CollapsiblePane(self, label='高级选项：请求头', style=wx.CP_DEFAULT_STYLE|wx.CP_NO_TLW_RESIZE)
        pane = self.advanced.GetPane()
        headersSizer = wx.FlexGridSizer(cols=3, vgap=5, hgap=8)
        headersSizer.AddGrowableCol(1)
        self.tcReferer = wx.TextCtrl(pane)
        self.tcReferer.SetHint('选填，视频所在的网页网址')
        self.tcCookie = wx.TextCtrl(pane, style=wx.TE_PASSWORD)
        self.tcCookie.SetHint('选填，例如 session=xxx; token=yyy')
        self.tcCookie.SetToolTip('填写 Cookie 的值，不含 Cookie: 前缀；随任务保存在本机数据库。')
        for label, control in [('Referer', self.tcReferer), ('Cookie', self.tcCookie)]:
            headersSizer.Add(wx.StaticText(pane, label=label), flag=wx.ALIGN_CENTER_VERTICAL)
            headersSizer.Add(control, flag=wx.EXPAND)
            helpLink = wx.adv.HyperlinkCtrl(pane, label='?', url='', size=self.FromDIP((24, -1)),
                                          style=wx.adv.HL_ALIGN_CENTRE)
            helpLink.SetNormalColour(wx.Colour('#666666'))
            helpLink.SetVisitedColour(wx.Colour('#666666'))
            helpLink.SetHoverColour(wx.Colour('#333333'))
            helpFont = helpLink.GetFont()
            helpFont.SetUnderlined(False)
            helpLink.SetFont(helpFont)
            helpLink.SetName(f'{label} 说明')
            helpLink.SetToolTip('点击查看完整说明')
            helpLink.Bind(wx.adv.EVT_HYPERLINK, lambda event, name=label: self.OnHeaderHelp(name))
            headersSizer.Add(helpLink, flag=wx.ALIGN_CENTER_VERTICAL)
        pane.SetSizer(headersSizer)
        sizer.Add(self.advanced, flag=wx.EXPAND)
        self.advanced.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, self.OnAdvancedChanged)

        self.SetSizer(sizer)
        
        # # self.text_ctrl = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        # self.tcStart.Bind(wx.EVT_TEXT, self.on_check)
        self.tcStart.Bind(wx.EVT_TEXT, self.OnTCStartChanged)
        btnAppend.Bind(wx.EVT_BUTTON, self.OnBtnAppendClicked)
        # btnDown.Bind(wx.EVT_BUTTON, self.OnBtnDownClicked)
        # self.tsList.Bind(wx.EVT_TEXT, self.OnTsListTxtChanged)

        self._SetDefaultValue()
        self.autoAdds = []

    def OnHeaderHelp(self, name):
        """点击后使用持久弹窗，仅说明当前字段，不显示已填写的 Cookie。"""
        descriptions = {
            'Referer': (
                '选填。用于向服务器说明下载请求来自哪个网页。\n'
                '需要时，填写浏览器中该 TS 请求实际携带的 Referer 值。\n\n'
                '获取方式：浏览器按 F12 → 网络（Network）→ 选择 TS 请求 → '
                '请求标头（Request Headers）→ Referer。\n'
                '只复制网址，不包含 Referer: 前缀；原请求没有此项时通常留空。'),
            'Cookie': (
                '选填。用于携带网站的登录会话等信息。\n'
                '需要时，填写浏览器中该 TS 请求实际携带的 Cookie 值。\n\n'
                '获取方式：浏览器按 F12 → 网络（Network）→ 选择 TS 请求 → '
                '请求标头（Request Headers）→ Cookie。\n'
                '只复制值，不包含 Cookie: 前缀，例如 session=xxx; token=yyy。\n\n'
                'Cookie 会随任务保存在本机数据库中，输入框以掩码显示。')
        }
        dialog = DownloadHelpDialog(self, f'{name} 说明', descriptions[name])
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()

    def OnURIHelp(self, event):
        address = self.GetBaseURI()
        text = "填写分片来源的参考网址。\n程序取该网址所在目录，作为相对分片地址的基准；本窗口不会读取该网址的内容。"
        text += f"\n\n当前填写：\n{address or '未填写'}"
        try:
            parsed = urlsplit(address)
            if parsed.scheme.lower() in ("http", "https") and parsed.netloc:
                text += f"\n\n该网址所在目录：\n{urljoin(address, '.')}"
        except ValueError:
            pass
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
        # m3u8_url = "https://yzzy.play-cdn10.com/20230104/21337_a024ad0f/1000k/hls/mixed.m3u8"
        m3u8_url = "http://127.0.0.1:8000/videos/2025/test2/index.m3u8"
        self.tcURI.SetValue(m3u8_url)

        # self.tcPlay.SetValue(f"{9:.6f}")
        self.tcPlay.SetValue(f"#EXTINF:{5:.6f},")
        self.tcReg.SetValue("seg-{idx}-v1-a1.ts")
        self.tcStart.SetValue(f"1")
        self.tcEnd.SetValue(f"10")

        self.tsList.SetValue("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-PLAYLIST-TYPE:VOD")

    
    def GetBaseURI(self):
        return self.tcURI.GetValue().strip()
    
    def GetBasePath(self):
        return self.tcPath.GetValue().strip()

    def GetContent(self):
        return self.tsList.GetValue().strip()

    def GetRequestHeaders(self):
        return {name: control.GetValue().strip() for name, control in
                [('Referer', self.tcReferer), ('Cookie', self.tcCookie)] if control.GetValue().strip()}

    def OnAdvancedChanged(self, event):
        self.Layout()
        self.GetParent().Layout()  # 展开时压缩编辑区，不改变对话框大小。

    @staticmethod
    def FormatSegmentName(pattern, index):
        """仅支持编号占位符，不执行任意 format 属性访问或格式表达式。"""
        token = r'\{idx(?::0([1-9][0-9]?)d)?\}'
        matches = list(re.finditer(token, pattern))
        remainder = re.sub(token, '', pattern)
        if not matches or '{' in remainder or '}' in remainder:
            raise ValueError('段名规则需包含 {idx} 或 {idx:03d}，例如 seg-{idx:03d}.ts')
        return re.sub(token, lambda match: str(index).zfill(int(match.group(1) or 0)), pattern)

    def OnTCStartChanged(self, event):
        txt = self.tcStart.GetValue()
        self.tcEnd.SetValue(txt)

    def OnBtnAppendClicked(self, event):
        #############################################
        ### 控件判断部分
        #############################################
        '''检查ts开始和结束'''
        txtPaly = self.tcPlay.GetValue().strip()
        if not txtPaly:
            wx.MessageBox("请输入播放时长！", "提示", wx.OK|wx.ICON_WARNING)
            return
        txtReg = self.tcReg.GetValue().strip()
        try:
            self.FormatSegmentName(txtReg, 1)
        except ValueError as error:
            wx.MessageBox(str(error), "提示", wx.OK|wx.ICON_WARNING)
            return
        txtStart = self.tcStart.GetValue().strip()
        if not re.fullmatch(r'[0-9]+', txtStart):
            wx.MessageBox("开始字段必须是数字！", "警告", wx.OK|wx.ICON_WARNING)
            return
        txtEnd = self.tcEnd.GetValue().strip()
        if not re.fullmatch(r'[0-9]+', txtEnd):
            wx.MessageBox("结束字段需必须是数字！", "警告", wx.OK|wx.ICON_WARNING)
            return
        
        '''分割ts开始和结束的前缀和数字'''
        numStart = int(txtStart)
        numEnd = int(txtEnd)
        if numEnd < numStart:
            wx.MessageBox("结束字段后缀需要>=开始字段后缀！", "警告", wx.OK|wx.ICON_WARNING)
            return
        
        #############################################
        ### 逻辑执行部分
        #############################################
        # base_path = ""
        # # 其次，根据 base uri 获取前缀
        # # 会自动覆盖上面的 ts 下载地址
        # uriLine = self.GetBaseURI()
        # if uriLine and uriLine.endswith(".m3u8"):
        #     base_path = uriLine.rsplit('/', 1)[0]
        # print(f"M3U8TSDownload.OnBtnAppendClicked 2 base_path:{base_path}")
        # if not base_path:
        #     wx.MessageBox("请输入正确的m3u8下载地址！", "警告", wx.OK|wx.ICON_WARNING)
        #     return

        txtTs = self.GetContent()
        lines = []
        # 清除上一次自动添加的 ts uri
        for line in txtTs.splitlines():
            if not line.strip():
                continue
            if line in self.autoAdds:
                continue
            lines.append(line)

        
        endTs = f"#EXT-X-ENDLIST"
        self.autoAdds.clear()
        # lines = [line for line in txtTs.splitlines() if line.strip() != ""]
        for nIdx in range(numStart, numEnd+1):
            baseTs = self.FormatSegmentName(txtReg, nIdx)

            lines.append(txtPaly)
            lines.append(baseTs)
            self.autoAdds.append(txtPaly)
            self.autoAdds.append(baseTs)
        lines.append(endTs)
        self.autoAdds.append(endTs)
        self.tsList.SetValue("\n".join(lines))
        # print(self.autoAdds)

        event.Skip()
