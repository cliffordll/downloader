"""单个 TS 分片下载地址编辑窗口。"""
import wx

from src.views.components.icons import app_icons


class SegmentURLDialog(wx.Dialog):
    def __init__(self, parent, url):
        super().__init__(parent, title='修改下载地址')
        self.SetIcons(app_icons())
        border = self.FromDIP(5)
        layout = wx.BoxSizer(wx.VERTICAL)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(self, label='下载地址：'),
                flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=border)
        self.url = wx.TextCtrl(self, value=url)
        self.url.SetHint('填写完整的 HTTP / HTTPS 分片下载地址')
        row.Add(self.url, proportion=1, flag=wx.EXPAND)
        layout.Add(row, flag=wx.EXPAND | wx.ALL, border=border)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.AddStretchSpacer()
        save = wx.Button(self, wx.ID_OK, '保存')
        cancel = wx.Button(self, wx.ID_CANCEL, '取消')
        buttons.Add(save, flag=wx.RIGHT, border=border)
        buttons.Add(cancel)
        layout.Add(buttons, flag=wx.EXPAND | wx.ALL, border=border)
        save.SetDefault()
        self.SetEscapeId(wx.ID_CANCEL)
        self.SetSizer(layout)
        height = self.ClientToWindowSize(wx.Size(0, layout.GetMinSize().height)).height
        self.SetSize(width=self.FromDIP(720), height=height)
        self.CenterOnParent()
        self.url.SetFocus()
        self.url.SetInsertionPoint(0)

    def GetValue(self):
        return self.url.GetValue()
