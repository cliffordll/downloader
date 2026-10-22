"""设置加密任务共用密码的前端对话框。"""

import wx

from src.views.components.icons import app_icons
from src.views.dialogs.panels.password_entry import PasswordEntry


PASSWORD_DIALOG_WIDTH = 440

def add_password_dialog_control(parent, layout, label, control):
    row = wx.BoxSizer(wx.HORIZONTAL)
    label_control = wx.StaticText(parent, label=label,
                                  size=parent.FromDIP(wx.Size(60, -1)),
                                  style=wx.ALIGN_LEFT | wx.ST_NO_AUTORESIZE)
    row.Add(label_control, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
            border=parent.FromDIP(10))
    row.Add(control, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL)
    layout.Add(row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
               border=parent.FromDIP(5))


def add_password_dialog_actions(parent, layout):
    row = wx.BoxSizer(wx.HORIZONTAL)
    row.AddStretchSpacer()
    ok = wx.Button(parent, wx.ID_OK, '确定')
    cancel = wx.Button(parent, wx.ID_CANCEL, '取消')
    size = parent.FromDIP(wx.Size(75, -1))
    ok.SetMinSize(size)
    cancel.SetMinSize(size)
    row.Add(ok, flag=wx.RIGHT, border=parent.FromDIP(5))
    row.Add(cancel)
    layout.Add(row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
               border=parent.FromDIP(5))
    ok.SetDefault()
    parent.SetEscapeId(wx.ID_CANCEL)
    return ok, cancel


class TaskPasswordDialog(wx.Dialog):
    def __init__(self, parent, *, has_existing=False):
        title = '修改加密任务密码' if has_existing else '设置加密任务密码'
        super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE)
        self.SetIcons(app_icons())
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(wx.StaticText(self, label='所有加密任务共用此密码。'),
                   flag=wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, border=self.FromDIP(5))

        self.current_password = None
        if has_existing:
            self.current_password = PasswordEntry(self)
            add_password_dialog_control(self, layout, '当前密码：', self.current_password)
        self.password = PasswordEntry(self)
        add_password_dialog_control(self, layout, '新密码：', self.password)
        self.confirm_password = PasswordEntry(self)
        add_password_dialog_control(self, layout, '再次输入：', self.confirm_password)
        self.error = wx.StaticText(self, label='')
        self.error.SetForegroundColour(wx.Colour(190, 35, 35))
        self.error.Hide()
        error_row = wx.BoxSizer(wx.HORIZONTAL)
        error_row.AddSpacer(self.FromDIP(70))
        error_row.Add(self.error, proportion=1)
        layout.Add(error_row, flag=wx.EXPAND | wx.BOTTOM, border=self.FromDIP(5))
        self.ok, _cancel = add_password_dialog_actions(self, layout)

        self.SetSizer(layout)
        client_size = wx.Size(self.FromDIP(PASSWORD_DIALOG_WIDTH), layout.GetMinSize().height)
        self.SetClientSize(client_size)
        self.SetMinSize(client_size)
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
        message = ('两次输入的密码不一致。'
                   if password and confirmation and password != confirmation else '')
        self.error.SetLabel(message)
        self.error.Show(bool(message))
        self.Layout()
        event.Skip()

    def _on_enter(self, _event):
        if self.ok.IsEnabled():
            self.EndModal(wx.ID_OK)

    def GetPassword(self):
        return self.password.GetValue()

    def GetCurrentPassword(self):
        return self.current_password.GetValue() if self.current_password else None
