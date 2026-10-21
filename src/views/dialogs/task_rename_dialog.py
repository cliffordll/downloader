"""任务重命名弹窗。"""
import wx

from src.views.components.icons import app_icons


class TaskRenameDialog(wx.Dialog):
    """重命名任务；不让用户输入输出文件扩展名。"""

    def __init__(self, parent, current_name):
        super().__init__(parent, title='重命名任务', style=wx.DEFAULT_DIALOG_STYLE)
        self.SetIcons(app_icons())
        border = self.FromDIP(5)
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(wx.StaticText(self, label='任务名称：'),
                   flag=wx.LEFT | wx.RIGHT | wx.TOP, border=border)
        self.name = wx.TextCtrl(self, value=current_name)
        layout.Add(self.name, flag=wx.EXPAND | wx.ALL, border=border)
        layout.Add(wx.StaticText(self, label='文件扩展名会自动保留。'),
                   flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=border)

        actions = wx.BoxSizer(wx.HORIZONTAL)
        actions.AddStretchSpacer()
        ok = wx.Button(self, wx.ID_OK, '确定')
        cancel = wx.Button(self, wx.ID_CANCEL, '取消')
        actions.Add(ok, flag=wx.RIGHT, border=border)
        actions.Add(cancel)
        layout.Add(actions, flag=wx.EXPAND | wx.ALL, border=border)
        ok.SetDefault()
        self.SetEscapeId(wx.ID_CANCEL)
        self.SetSizerAndFit(layout)
        client_size = wx.Size(self.FromDIP(440), layout.GetMinSize().height)
        self.SetClientSize(client_size)
        self.SetMinSize(client_size)
        self.CenterOnParent()
        self.name.SetFocus()
        self.name.SetSelection(-1, -1)

    def GetName(self):
        return self.name.GetValue()
