"""单个 TS 分片的下载地址和播放时长编辑窗口。"""
import wx

from src.views.dialogs.base_dialog import AppDialog


class SegmentEditDialog(AppDialog):
    def __init__(self, parent, url, duration, *, completed=False):
        super().__init__(parent, title='修改分片信息',
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        border = self.dialog_spacing
        layout = wx.BoxSizer(wx.VERTICAL)
        # 长 URL 仅视觉换行，GetValue 仍返回原始地址，不插入换行符。
        self.url = wx.TextCtrl(self, value=url, style=wx.TE_MULTILINE | wx.TE_CHARWRAP)
        self.url.SetHint('填写完整的 HTTP / HTTPS 分片下载地址')
        self.url.SetEditable(not completed)
        if completed:
            self.url.SetToolTip('已完成分片的下载地址只读。')
        # 与“查看下载地址”窗口一样，正文直接放在顶部，按内容预留紧凑高度。
        width = self.FromDIP(620)
        text_width = width - self.FromDIP(32)
        lines = max(1, (self.url.GetTextExtent(url).width + text_width - 1) // text_width)
        text_height = min(self.FromDIP(160), max(self.FromDIP(60),
                          lines * self.url.GetCharHeight() + self.FromDIP(16)))
        self.url.SetMinSize(wx.Size(-1, text_height))
        layout.Add(self.url, proportion=1, flag=wx.EXPAND | wx.TOP | wx.LEFT | wx.RIGHT, border=border)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(self, label='播放时长：'),
                flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=border)
        self.duration = wx.TextCtrl(self, value='' if duration is None else str(duration),
                                    size=self.FromDIP(wx.Size(110, -1)))
        self.duration.SetHint('大于 0 的秒数，支持小数；留空保留原时长')
        row.Add(self.duration, flag=wx.EXPAND)
        row.Add(wx.StaticText(self, label='秒'), flag=wx.ALIGN_CENTER_VERTICAL | wx.LEFT, border=border)
        row.AddStretchSpacer()
        save = wx.Button(self, wx.ID_OK, '保存')
        cancel = wx.Button(self, wx.ID_CANCEL, '取消')
        row.Add(save, flag=wx.LEFT | wx.RIGHT, border=border)
        row.Add(cancel)
        layout.Add(row, flag=wx.EXPAND | wx.ALL, border=border)
        save.SetDefault()
        self.SetEscapeId(wx.ID_CANCEL)
        self.SetSizer(layout)
        height = layout.GetMinSize().height
        self.SetClientSize(wx.Size(width, height))
        self.SetMinSize(self.ClientToWindowSize(wx.Size(max(self.FromDIP(480), layout.GetMinSize().width), height)))
        self.CenterOnParent()
        (self.duration if completed else self.url).SetFocus()
        self.url.SetInsertionPoint(0)

    def GetURL(self):
        return self.url.GetValue()

    def GetDuration(self):
        return self.duration.GetValue().strip() or None
