import wx
import wx.adv


class HelpButton(wx.Panel):
    """统一帮助入口的外观；具体说明由表单的点击回调负责。"""

    def __init__(self, parent, name, on_click):
        super().__init__(parent)
        link = wx.adv.HyperlinkCtrl(self, label='?', url='',
                                   style=wx.BORDER_NONE|wx.adv.HL_ALIGN_LEFT)
        link.SetNormalColour(wx.Colour('#666666'))
        link.SetVisitedColour(wx.Colour('#666666'))
        link.SetHoverColour(wx.Colour('#333333'))
        font = link.GetFont()
        font.SetUnderlined(False)
        link.SetFont(font)
        link.SetName(name)
        link.SetToolTip('点击查看完整说明')
        link.Bind(wx.adv.EVT_HYPERLINK, on_click)
        self.SetName(name)
        self.SetToolTip('点击查看完整说明')

        # Windows 原生超链接不支持文字居中，让自然宽度的链接在固定列内居中。
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        sizer.AddStretchSpacer()
        sizer.Add(link, flag=wx.ALIGN_CENTER_VERTICAL)
        sizer.AddStretchSpacer()
        self.SetSizer(sizer)
        self.SetInitialSize(self.FromDIP((24, -1)))
