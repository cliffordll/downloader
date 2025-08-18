import wx
import re

from src.managers.file_manager import FileManager
from src.managers.m3m8_parser import M3U8Parser

class M3U8TSDownload(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent=parent, id=wx.ID_ANY)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # base uri
        uriSizer = wx.BoxSizer(wx.HORIZONTAL)
        lblUri = wx.StaticText(self, -1, label="Base URI:", size=(60, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        # lbl.SetBackgroundColour(wx.RED)
        self.tcURI = wx.TextCtrl(self)
        uriSizer.Add(lblUri, proportion=1, flag=wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL|wx.ALL, border=5) 
        uriSizer.Add(self.tcURI, proportion=50, flag=wx.EXPAND|wx.ALIGN_LEFT|wx.ALL, border=5)

        pathSizer = wx.BoxSizer(wx.HORIZONTAL)
        lblPath = wx.StaticText(self, -1, label="Base Path:", size=(60, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        # lblUri.SetBackgroundColour(wx.RED)
        self.tcPath = wx.TextCtrl(self)
        # btnM3U8 = wx.Button(self, label="获取 M3U8")
        pathSizer.Add(lblPath, proportion=1, flag=wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL|wx.ALL, border=5) 
        pathSizer.Add(self.tcPath, proportion=50, flag=wx.EXPAND|wx.ALIGN_LEFT|wx.ALL, border=5)
        # pathSizer.Add(btnM3U8, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)

        # ts start and end
        lblPlay = wx.StaticText(self, -1, label="播放时长:", size=(60, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        # self.tcPlay = wx.TextCtrl(self, size=(60, -1), style=wx.TE_PROCESS_ENTER)
        self.tcPlay = wx.TextCtrl(self)
        tsSizer = wx.BoxSizer(wx.HORIZONTAL)
        lblReg = wx.StaticText(self, -1, label="段名规则:", size=(60, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        self.tcReg = wx.TextCtrl(self)
        lblStart = wx.StaticText(self, -1, label="开始:", size=(30, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        self.tcStart = wx.TextCtrl(self)
        lblEnd = wx.StaticText(self, -1, label="结束:", size=(30, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        self.tcEnd = wx.TextCtrl(self)        
        btnAppend = wx.Button(self, label="添加 TS")

        tsSizer.Add(lblPlay, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL|wx.ALL, border=5)
        tsSizer.Add(self.tcPlay, proportion=40, flag=wx.EXPAND|wx.ALL, border=5)
        tsSizer.AddStretchSpacer(prop=2)
        tsSizer.Add(lblReg, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL|wx.ALL, border=5) 
        tsSizer.Add(self.tcReg, proportion=40, flag=wx.EXPAND|wx.ALL, border=5)
        tsSizer.AddStretchSpacer(prop=2)
        tsSizer.Add(lblStart, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL|wx.ALL, border=5) 
        tsSizer.Add(self.tcStart, proportion=10, flag=wx.EXPAND|wx.ALL, border=5)
        tsSizer.AddStretchSpacer(prop=2)
        tsSizer.Add(lblEnd, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL|wx.ALL, border=5) 
        tsSizer.Add(self.tcEnd, proportion=10, flag=wx.EXPAND|wx.ALL, border=5)
        tsSizer.AddStretchSpacer(prop=2)
        tsSizer.Add(btnAppend, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)

        # m3u8 file
        listSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.tsList = wx.TextCtrl(self, style=wx.TE_MULTILINE|wx.TE_LEFT|wx.TE_RICH2)
        listSizer.Add(self.tsList, proportion=10, flag=wx.EXPAND|wx.ALL, border=5)

        # sbtcList = wx.StaticBox(self, label="m3u8文件")
        # # listSizer = wx.BoxSizer(sbtcList, wx.VERTICAL)
        # listSizer = wx.StaticBoxSizer(sbtcList, wx.VERTICAL)
        # self.tsList = wx.TextCtrl(self, style=wx.TE_MULTILINE|wx.TE_LEFT|wx.TE_RICH2)
        # listSizer.Add(self.tsList, proportion=10, flag=wx.EXPAND, border=5)

        # # Download
        # btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        # self.lblStatus = wx.StaticText(self, -1, label="", size=(200, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        # # self.lblStatus.SetBackgroundColour(wx.GREEN)
        # btnDown = wx.Button(self, label="下载")
        
        # btnSizer.Add(self.lblStatus, flag=wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL|wx.ALL, border=5)
        # btnSizer.AddStretchSpacer(prop=100)
        # btnSizer.Add(btnDown, proportion=1, flag=wx.ALIGN_LEFT|wx.ALL, border=5)

        sizer.Add(uriSizer, border=0)
        sizer.Add(pathSizer, border=0)
        sizer.Add(tsSizer, border=0)
        sizer.Add(listSizer, proportion=10, flag=wx.EXPAND, border=0)
        # sizer.Add(sbtcList, proportion=10, flag=wx.EXPAND, border=5)
        # sizer.Add(btnSizer, border=0)

        self.SetSizer(sizer)
        
        # # self.text_ctrl = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        # self.tcStart.Bind(wx.EVT_TEXT, self.on_check)
        self.tcStart.Bind(wx.EVT_TEXT, self.OnTCStartChanged)
        btnAppend.Bind(wx.EVT_BUTTON, self.OnBtnAppendClicked)
        # btnDown.Bind(wx.EVT_BUTTON, self.OnBtnDownClicked)
        # self.tsList.Bind(wx.EVT_TEXT, self.OnTsListTxtChanged)

        self._SetDefaultValue()
        self.autoAdds = []

    def _SetDefaultValue(self):
        '''测试提供个默认值'''
        m3u8_url = "https://yzzy.play-cdn10.com/20230104/21337_a024ad0f/1000k/hls/mixed.m3u8"
        self.tcURI.SetValue(m3u8_url)

        # self.tcPlay.SetValue(f"{9:.6f}")
        self.tcPlay.SetValue(f"#EXTINF:{5:.6f},")
        self.tcReg.SetValue("segment_{idx}_a1_v1.ts")
        self.tcStart.SetValue(f"10")
        self.tcEnd.SetValue(f"15")

        self.tsList.SetValue("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-PLAYLIST-TYPE:VOD")

    # def _SpiltText(self, text: str):
    #     '''分割ts前的前缀和数字'''
    #     import re
    #     # \d+ 匹配 1个或多个连续数字
    #     # \D+ 匹配 1个或多个连续字符
    #     strs = re.findall(r'(\d+|\D+)', text)
    #     # print(strs)
    #     prefix = "".join(strs[:-1])
    #     number = int(strs[-1])
    #     return prefix, number
    
    def GetBaseURI(self):
        return self.tcURI.GetValue().strip()
    
    def GetBasePath(self):
        return self.tcPath.GetValue().strip()

    def GetContent(self):
        return self.tsList.GetValue().strip()

    # def CreateSeedFile(self):
    #     m3u8Uri = self.uriBase.GetValue()
    #     content = self.tsList.GetValue()
    #     if not m3u8Uri:
    #         wx.MessageBox("请输入正确的M3U8下载地址！", "警告", wx.OK|wx.ICON_WARNING)
    #         return False
    #     if not content:
    #         wx.MessageBox("请点击“获取 M3U8”按钮，下载M3U8内容！", "警告", wx.OK|wx.ICON_WARNING)
    #         return False

    #     # 解析 m3u8 内容是否合法
    #     try:
    #         parser = M3U8Parser(content=content, m3u8_uri=m3u8Uri)
    #         isAbs, tsList = parser.parse_media()
    #         # print(tsList)
    #         if len(tsList) <= 0:
    #             wx.MessageBox("M3U8内容解析错误！未找到TS文件", "警告", wx.OK|wx.ICON_WARNING)
    #             return False
    #     except Exception as ex:
    #         print(f"M3U8TSDownload.CreateSeedFile except:{str(ex)}")
    #         return False

    #     return FileManager().CreateSeedFile(baseUri=m3u8Uri, content=content.encode())

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
        if not txtReg or not re.findall(r"{.+}", txtReg):
            wx.MessageBox("请输入段名规则！正则匹配'{数字}'", "提示", wx.OK|wx.ICON_WARNING)
            return
        txtStart = self.tcStart.GetValue().strip()
        if not txtStart or not txtStart[-1].isdigit():  # 检查最后一个字符是否是数字
            wx.MessageBox("开始字段必须是数字！", "警告", wx.OK|wx.ICON_WARNING)
            return
        txtEnd = self.tcEnd.GetValue().strip()
        if not txtEnd or not txtEnd[-1].isdigit():  # 检查最后一个字符是否是数字
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

        strReg = re.findall(r"{.+}", txtReg)[0]
        
        endTs = f"#EXT-X-ENDLIST"
        self.autoAdds.clear()
        # lines = [line for line in txtTs.splitlines() if line.strip() != ""]
        for nIdx in range(numStart, numEnd+1):
            baseTs = txtReg.replace(strReg, f"{nIdx}")

            lines.append(txtPaly)
            lines.append(baseTs)
            self.autoAdds.append(txtPaly)
            self.autoAdds.append(baseTs)
        lines.append(endTs)
        self.autoAdds.append(endTs)
        self.tsList.SetValue("\n".join(lines))
        # print(self.autoAdds)

        event.Skip()
    
    # def OnTsListTxtChanged(self, event):
    #     # nLine = self.tsList.GetNumberOfLines()
    #     nLine = len(self._GetTCList())

    #     # # self.lblStatus.Freeze()
    #     # self.lblStatus.SetLabelText(f"共有{nLine}个TS文件")
    #     # # self.lblStatus.SetLabel(f"共有")
    #     # # self.lblStatus.SetLabel("新内容")
    #     # # self.lblStatus.Thaw()

    #     # # event.Skip()
    #     # # 强制刷新布局
    #     # # self.lblStatus.GetParent().Layout()
    #     # # # self.Layout()
    #     # # self.static_text.Refresh()
    #     # # self.static_text.Update()

    # def OnBtnDownClicked(self, event):
    #     """按钮点击事件处理函数"""
    #     nLine = len(self._GetTCList())

    #     wx.MessageBox(f"即将下载{nLine}个TS文件！", "提示", wx.OK|wx.ICON_INFORMATION)