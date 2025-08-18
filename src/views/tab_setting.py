import wx


class CustomStaticBox(wx.StaticBox):
    def __init__(self, parent, label=""):
        super().__init__(parent, label=label)

        # self.SetBackgroundColour(wx.WHITE)
        self.SetFont(wx.Font(1, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
    #     self.Bind(wx.EVT_PAINT, self.on_paint)

    # def on_paint(self, event):
    #     # 自定义绘制逻辑（隐藏或重定位标签）
    #     dc = wx.PaintDC(self)
    #     dc.Clear()
    #     # 跳过默认标签绘制
    #     event.Skip()

class TabSetting(wx.Panel):
    """
    This will be the third notebook tab
    """
    def __init__(self, parent):
        super().__init__(parent=parent, id=wx.ID_ANY)
        # 主布局
        sizer = wx.BoxSizer(wx.VERTICAL)

        # sbox = wx.StaticBox(self, label="系统设置", size=(240, 90))
        sbSetting = wx.StaticBox(self, label="系统设置")
        # sbSetting.SetFont(wx.Font(1, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))

        # sbox.Enable(False)ss
        # 内部布局
        inner = wx.StaticBoxSizer(sbSetting, wx.VERTICAL)

        sizerDir = wx.BoxSizer(wx.HORIZONTAL)
        lblDir = wx.StaticText(self, -1, label="下载目录:", size=(80, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        # lbl.SetBackgroundColour(wx.RED)
        self.uriBase = wx.TextCtrl(self)
        sizerDir.Add(lblDir, proportion=1, flag=wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL, border=5) 
        sizerDir.Add(self.uriBase, proportion=50, flag=wx.EXPAND|wx.ALIGN_LEFT|wx.ALL, border=5)
        # sizerDir.Add(self.dir_ctrl, proportion=50, flag=wx.EXPAND|wx.ALIGN_LEFT|wx.ALL, border=5)
        inner.Add(sizerDir, 0, flag=wx.EXPAND|wx.ALL, border=5)


        sizer.Add(inner, proportion=1, flag=wx.EXPAND|wx.ALL, border=0)

        self.SetSizer(sizer)