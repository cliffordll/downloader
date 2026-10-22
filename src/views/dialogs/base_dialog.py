"""Shared visual defaults for application-owned dialogs."""

import wx

from src.views.components.icons import app_icons


class AppDialog(wx.Dialog):
    """Dialog base that applies the app icon and the shared 5-DIP outer margin."""

    OUTER_MARGIN_DIP = 5

    def __init__(self, parent, *, title='', style=wx.DEFAULT_DIALOG_STYLE, **kwargs):
        super().__init__(parent, title=title, style=style, **kwargs)
        self.SetIcons(app_icons())
        self.dialog_spacing = self.FromDIP(self.OUTER_MARGIN_DIP)

