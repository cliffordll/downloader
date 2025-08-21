import wx
import re

from src.managers.file_manager import FileManager
from src.managers.m3m8_parser import M3U8Parser

class DownloadEditTS(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent=parent, id=wx.ID_ANY)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # base uri
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

        tsSizer.Add(lblPlay, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL|wx.TOP|wx.BOTTOM|wx.RIGHT, border=5)
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
        tsSizer.Add(btnAppend, proportion=1, flag=wx.EXPAND|wx.TOP|wx.BOTTOM|wx.LEFT, border=5)

        # m3u8 file
        listSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.tsList = wx.TextCtrl(self, style=wx.TE_MULTILINE|wx.TE_LEFT|wx.TE_RICH2)
        listSizer.Add(self.tsList, proportion=10, flag=wx.EXPAND|wx.TOP, border=5)

        sizer.Add(uriSizer, border=0)
        sizer.Add(pathSizer, border=0)
        sizer.Add(tsSizer, border=0)
        sizer.Add(listSizer, proportion=10, flag=wx.EXPAND, border=0)

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
        # m3u8_url = "https://yzzy.play-cdn10.com/20230104/21337_a024ad0f/1000k/hls/mixed.m3u8"
        m3u8_url = "http://127.0.0.1:8000/videos/2025/test2/index.m3u8"
        self.tcURI.SetValue(m3u8_url)

        # self.tcPlay.SetValue(f"{9:.6f}")
        self.tcPlay.SetValue(f"#EXTINF:{5:.6f},")
        self.tcReg.SetValue("segment_{idx}_a1_v1.ts")
        self.tcStart.SetValue(f"10")
        self.tcEnd.SetValue(f"15")

        self.tsList.SetValue("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-PLAYLIST-TYPE:VOD")

    
    def GetBaseURI(self):
        return self.tcURI.GetValue().strip()
    
    def GetBasePath(self):
        return self.tcPath.GetValue().strip()

    def GetContent(self):
        return self.tsList.GetValue().strip()

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