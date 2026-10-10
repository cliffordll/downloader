"""紧凑说明窗口：标题栏、左对齐正文与可滚动的阅读区域。"""
import wx

from src.views.components.icons import app_icons


class InformationDialog(wx.Dialog):
    def __init__(self, parent, title, text, *, usage=False):
        super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.SetIcons(app_icons())
        spacing = self.FromDIP(5)
        layout = wx.BoxSizer(wx.VERTICAL)
        self.body = wx.TextCtrl(self, value=text,
                                style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_LEFT
                                | wx.TE_RICH2 | wx.TE_NO_VSCROLL)
        self.body.SetInsertionPoint(0)
        layout.Add(self.body, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP,
                   border=spacing)
        # 原生标准按钮 sizer 会额外插入平台留白，单按钮采用紧凑布局。
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.AddStretchSpacer()
        close_button = wx.Button(self, wx.ID_CLOSE, '关闭')
        buttons.Add(close_button)
        close_button.SetDefault()
        self.SetEscapeId(wx.ID_CLOSE)
        layout.Add(buttons, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=spacing)
        # 按钮上下只预留少量空间，不再叠加标准按钮栏的内边距。
        layout.InsertSpacer(1, self.FromDIP(6))
        layout.AddSpacer(self.FromDIP(6))
        self.Bind(wx.EVT_BUTTON, lambda event: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        self.SetSizer(layout)
        display_index = wx.Display.GetFromWindow(parent) if parent else 0
        screen = wx.Display(max(0, display_index)).GetClientArea()
        desired = self.FromDIP(wx.Size(900, 680) if usage else wx.Size(620, 280))
        # 所有说明窗口采用同一规则，包括使用帮助和关于。
        desired.width = min(desired.width, int(screen.width * 0.9))
        text_width = max(1, desired.width - self.FromDIP(32))
        lines = sum(max(1, (self.body.GetTextExtent(line).width + text_width - 1)
                        // text_width) for line in text.split('\n'))
        line_height = self.body.GetCharHeight() + self.FromDIP(1)
        # 文本控件有内部留白；先算客户区，再补上系统标题栏的高度。
        body_height = lines * line_height + self.FromDIP(16)
        client_height = (body_height + spacing + close_button.GetBestSize().height
                         + self.FromDIP(12))
        desired.height = max(self.FromDIP(140), min(self.FromDIP(680),
                             self.ClientToWindowSize(wx.Size(0, client_height)).height))
        size = wx.Size(min(desired.width, int(screen.width * 0.9)),
                       min(desired.height, int(screen.height * 0.9)))
        self.SetSize(size)
        minimum = self.FromDIP(wx.Size(600, 130) if usage else wx.Size(440, 130))
        self.SetMinSize(wx.Size(min(minimum.width, size.width), min(minimum.height, size.height)))
        self.CenterOnParent() if parent else self.Center()
