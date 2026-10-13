"""浏览器配对窗口，沿用现有弹窗的间距、图标和按钮布局。"""
import wx

from src.views.components.icons import app_icons
from src.views.dialogs.panels.path_picker import form_action_size


class BrowserConnectDialog(wx.Dialog):
    def __init__(self, parent, token):
        super().__init__(parent, title='连接浏览器扩展',
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.SetIcons(app_icons())
        border = self.FromDIP(5)
        layout = wx.BoxSizer(wx.VERTICAL)
        description = wx.StaticText(self, label='在浏览器扩展中展开“连接下载器”，粘贴以下配对码并保存。')
        layout.Add(description, flag=wx.EXPAND | wx.ALL, border=border)

        row = wx.BoxSizer(wx.HORIZONTAL)
        label = wx.StaticText(self, label='配对码：')
        row.Add(label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=border)
        self.code = wx.TextCtrl(self, value=token, style=wx.TE_READONLY)
        code_width = self.code.GetTextExtent(token).width + self.FromDIP(24)
        self.code.SetMinSize(wx.Size(code_width, -1))
        row.Add(self.code, proportion=1, flag=wx.EXPAND | wx.RIGHT, border=border)
        self.copy_button = wx.Button(self, label='复制', size=form_action_size(self))
        self.copy_button.Bind(wx.EVT_BUTTON, self.OnCopy)
        row.Add(self.copy_button, flag=wx.ALIGN_CENTER_VERTICAL)
        layout.Add(row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=border)

        note = wx.StaticText(self, label='程序退出后配对码失效，重启后需要重新配对。')
        layout.Add(note, flag=wx.EXPAND | wx.ALL, border=border)
        self.Bind(wx.EVT_CHAR_HOOK, self.OnKey)
        self.SetSizer(layout)
        minimum = layout.GetMinSize()
        self.SetClientSize(wx.Size(max(self.FromDIP(620), minimum.width), minimum.height))
        self.SetMinSize(self.GetSize())
        self.SetMaxSize(wx.Size(-1, self.GetSize().height))
        self.CenterOnParent()
        self.code.SetFocus()
        self.code.SelectAll()

    def OnKey(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
        else:
            event.Skip()

    def OnCopy(self, event):
        if not wx.TheClipboard.Open():
            wx.MessageBox('暂时无法访问剪贴板，请选中配对码手动复制。', '复制失败',
                          wx.OK | wx.ICON_WARNING, parent=self)
            return
        try:
            copied = wx.TheClipboard.SetData(wx.TextDataObject(self.code.GetValue()))
            if copied:
                wx.TheClipboard.Flush()
        finally:
            wx.TheClipboard.Close()
        if copied:
            self.copy_button.SetLabel('已复制')
        else:
            wx.MessageBox('复制失败，请选中配对码手动复制。', '复制失败',
                          wx.OK | wx.ICON_WARNING, parent=self)
