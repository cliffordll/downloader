"""应用说明窗口：Logo、左对齐正文与可滚动的阅读区域。"""
import wx

from src.views.components.icons import app_icons, app_logo


class InformationDialog(wx.Dialog):
    def __init__(self, parent, title, text, *, usage=False):
        super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.SetIcons(app_icons())
        layout = wx.BoxSizer(wx.VERTICAL)
        header = wx.BoxSizer(wx.HORIZONTAL)
        header.Add(wx.StaticBitmap(self, bitmap=app_logo(40)), flag=wx.ALIGN_CENTER_VERTICAL)
        heading = wx.StaticText(self, label=title)
        font = wx.Font(heading.GetFont())
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        heading.SetFont(font)
        header.Add(heading, flag=wx.ALIGN_CENTER_VERTICAL | wx.LEFT, border=self.FromDIP(16))
        layout.Add(header, flag=wx.EXPAND | wx.ALL, border=self.FromDIP(20))

        self.body = wx.TextCtrl(self, value=text,
                                style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_LEFT | wx.TE_RICH2)
        self.body.SetInsertionPoint(0)
        layout.Add(self.body, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT,
                   border=self.FromDIP(20))
        buttons = self.CreateButtonSizer(wx.CLOSE)
        close_button = self.FindWindow(wx.ID_CLOSE)
        close_button.SetLabel('关闭')
        close_button.SetDefault()
        self.SetEscapeId(wx.ID_CLOSE)
        layout.Add(buttons, flag=wx.EXPAND | wx.ALL, border=self.FromDIP(16))
        self.Bind(wx.EVT_BUTTON, lambda event: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        self.SetSizer(layout)
        display_index = wx.Display.GetFromWindow(parent) if parent else 0
        screen = wx.Display(max(0, display_index)).GetClientArea()
        desired = self.FromDIP(wx.Size(900, 680) if usage else wx.Size(620, 360))
        size = wx.Size(min(desired.width, int(screen.width * 0.9)),
                       min(desired.height, int(screen.height * 0.9)))
        self.SetSize(size)
        minimum = self.FromDIP(wx.Size(600, 360) if usage else wx.Size(440, 300))
        self.SetMinSize(wx.Size(min(minimum.width, size.width), min(minimum.height, size.height)))
        self.CenterOnParent() if parent else self.Center()
