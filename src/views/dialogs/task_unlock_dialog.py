"""Password prompt for unlocking encrypted tasks."""

import wx

from src.views.components.icons import app_icons
from src.views.dialogs.panels.password_entry import PasswordEntry
from src.views.dialogs.task_password_dialog import (
    PASSWORD_DIALOG_WIDTH, add_password_dialog_actions, add_password_dialog_control,
)


class TaskUnlockDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title='解锁加密任务', style=wx.DEFAULT_DIALOG_STYLE)
        self.SetIcons(app_icons())
        layout = wx.BoxSizer(wx.VERTICAL)
        self.password = PasswordEntry(self)
        add_password_dialog_control(self, layout, '输入密码：', self.password)
        self.unlock, self.cancel = add_password_dialog_actions(self, layout)
        self.SetSizer(layout)
        client_size = wx.Size(self.FromDIP(PASSWORD_DIALOG_WIDTH), layout.GetMinSize().height)
        self.SetClientSize(client_size)
        self.SetMinSize(client_size)
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

