"""Uniform status view for local services owned by the application."""

import wx

from src.views.dialogs.base_dialog import AppDialog


class ServiceStatusDialog(AppDialog):
    def __init__(self, parent, listener, features):
        super().__init__(parent, title='本机服务状态')
        spacing = self.dialog_spacing
        layout = wx.BoxSizer(wx.VERTICAL)
        row = wx.FlexGridSizer(cols=3, vgap=self.FromDIP(8), hgap=self.FromDIP(12))
        row.AddGrowableCol(2, 1)
        heading = wx.StaticText(self, label='本机 HTTP 服务')
        font = heading.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        heading.SetFont(font)
        row.Add(heading, flag=wx.ALIGN_CENTER_VERTICAL)
        row.Add(wx.StaticText(self, label='运行中' if listener['running'] else '未运行'),
                flag=wx.ALIGN_CENTER_VERTICAL)
        address = (f"127.0.0.1:{listener['port']}" if listener['running'] else
                   f"上次端口 {listener['port']}" if listener.get('port') else '端口尚未分配')
        row.Add(wx.StaticText(self, label=address), flag=wx.ALIGN_CENTER_VERTICAL)
        layout.Add(row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=spacing)
        layout.Add(wx.StaticLine(self), flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP,
                   border=spacing)

        for feature in features:
            section = wx.StaticBoxSizer(wx.VERTICAL, self, feature['name'])
            state = wx.StaticText(self, label=feature['state'])
            section.Add(state, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP,
                        border=spacing)
            endpoint = wx.StaticText(self, label=f"接口：{feature['endpoint']}")
            endpoint.Wrap(self.FromDIP(640))
            section.Add(endpoint, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP,
                        border=spacing)
            if feature.get('details'):
                details = wx.StaticText(self, label=feature['details'])
                details.Wrap(self.FromDIP(640))
                section.Add(details, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM,
                            border=spacing)
            layout.Add(section, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=spacing)

        self.Bind(wx.EVT_CHAR_HOOK, self.OnKey)
        layout.Add(wx.StaticLine(self), flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP,
                   border=spacing)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.AddStretchSpacer()
        close = wx.Button(self, wx.ID_CLOSE, '关闭')
        buttons.Add(close)
        layout.Add(buttons, flag=wx.EXPAND | wx.ALL, border=spacing)
        self.Bind(wx.EVT_BUTTON, lambda _event: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        self.SetSizerAndFit(layout)
        self.SetMinSize(self.GetSize())
        self.CenterOnParent()
        close.SetDefault()

    def OnKey(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
        else:
            event.Skip()
