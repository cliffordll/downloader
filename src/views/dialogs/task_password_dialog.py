"""设置加密任务共用密码的前端对话框。"""

import wx

from src.views.components.icons import app_icons


class TaskPasswordDialog(wx.Dialog):
    def __init__(self, parent, *, has_existing=False):
        title = '修改加密任务密码' if has_existing else '设置加密任务密码'
        super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE)
        self.SetIcons(app_icons())
        border = self.FromDIP(8)
        layout = wx.BoxSizer(wx.VERTICAL)

        self.current_password = None
        if has_existing:
            layout.Add(wx.StaticText(self, label='当前密码：'),
                       flag=wx.LEFT | wx.RIGHT | wx.TOP, border=border)
            self.current_password = wx.TextCtrl(self, style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER)
            layout.Add(self.current_password, flag=wx.EXPAND | wx.ALL, border=border)
        layout.Add(wx.StaticText(self, label='设置所有加密任务共用的密码：'),
                   flag=wx.LEFT | wx.RIGHT | wx.TOP, border=border)
        self.password = wx.TextCtrl(self, style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER)
        layout.Add(self.password, flag=wx.EXPAND | wx.ALL, border=border)
        layout.Add(wx.StaticText(self, label='再次输入密码：'),
                   flag=wx.LEFT | wx.RIGHT, border=border)
        self.confirm_password = wx.TextCtrl(self, style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER)
        layout.Add(self.confirm_password, flag=wx.EXPAND | wx.ALL, border=border)
        self.error = wx.StaticText(self, label='')
        self.error.SetForegroundColour(wx.Colour(190, 35, 35))
        layout.Add(self.error, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=border)

        buttons = wx.StdDialogButtonSizer()
        self.ok = wx.Button(self, wx.ID_OK, '确定')
        cancel = wx.Button(self, wx.ID_CANCEL, '取消')
        buttons.AddButton(self.ok)
        buttons.AddButton(cancel)
        buttons.Realize()
        layout.Add(buttons, flag=wx.EXPAND | wx.ALL, border=border)

        self.ok.SetDefault()
        self.SetEscapeId(wx.ID_CANCEL)
        self.SetSizerAndFit(layout)
        self.SetMinSize(wx.Size(self.FromDIP(400), self.GetSize().height))
        self.CenterOnParent()
        self.password.Bind(wx.EVT_TEXT, self._on_text)
        self.confirm_password.Bind(wx.EVT_TEXT, self._on_text)
        if self.current_password:
            self.current_password.Bind(wx.EVT_TEXT, self._on_text)
            self.current_password.Bind(wx.EVT_TEXT_ENTER, self._on_enter)
        self.password.Bind(wx.EVT_TEXT_ENTER, self._on_enter)
        self.confirm_password.Bind(wx.EVT_TEXT_ENTER, self._on_enter)
        self.ok.Disable()
        self.password.SetFocus()

    def _on_text(self, event):
        password = self.password.GetValue()
        confirmation = self.confirm_password.GetValue()
        current = self.current_password.GetValue() if self.current_password else True
        self.ok.Enable(bool(current) and bool(password) and password == confirmation)
        self.error.SetLabel('两次输入的密码不一致。'
                            if password and confirmation and password != confirmation else '')
        self.Layout()
        event.Skip()

    def _on_enter(self, _event):
        if self.ok.IsEnabled():
            self.EndModal(wx.ID_OK)

    def GetPassword(self):
        return self.password.GetValue()

    def GetCurrentPassword(self):
        return self.current_password.GetValue() if self.current_password else None
