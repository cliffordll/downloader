"""Password prompt for unlocking encrypted tasks."""

import wx

from src.views.components.icons import app_icons


class TaskUnlockDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title='解锁加密任务', style=wx.DEFAULT_DIALOG_STYLE)
        self.SetIcons(app_icons())
        border = self.FromDIP(8)
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(wx.StaticText(self, label='输入加密任务密码：'),
                   flag=wx.LEFT | wx.RIGHT | wx.TOP, border=border)
        self.password = wx.TextCtrl(self, style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER)
        layout.Add(self.password, flag=wx.EXPAND | wx.ALL, border=border)
        buttons = wx.StdDialogButtonSizer()
        self.unlock = wx.Button(self, wx.ID_OK, '解锁')
        self.cancel = wx.Button(self, wx.ID_CANCEL, '取消')
        buttons.AddButton(self.unlock)
        buttons.AddButton(self.cancel)
        buttons.Realize()
        layout.Add(buttons, flag=wx.EXPAND | wx.ALL, border=border)
        self.unlock.SetDefault()
        self.SetEscapeId(wx.ID_CANCEL)
        self.SetSizerAndFit(layout)
        self.SetMinSize(wx.Size(self.FromDIP(360), self.GetSize().height))
        self.CenterOnParent()
        self.password.Bind(wx.EVT_TEXT, self._on_password)
        self.password.Bind(wx.EVT_TEXT_ENTER, self._on_enter)
        self.unlock.Disable()
        self.password.SetFocus()

    def _on_password(self, event):
        self.unlock.Enable(bool(self.password.GetValue()))
        event.Skip()

    def _on_enter(self, _event):
        if self.password.GetValue():
            self.EndModal(wx.ID_OK)

