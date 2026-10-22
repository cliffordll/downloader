"""Shared visual defaults for application-owned dialogs."""

import wx

from src.views.components.icons import app_icons
from src.views.ui_metrics import OUTER_MARGIN_DIP as SHARED_OUTER_MARGIN_DIP


class AppDialog(wx.Dialog):
    """Dialog base that applies the app icon and the shared 8-DIP outer margin."""

    OUTER_MARGIN_DIP = SHARED_OUTER_MARGIN_DIP

    def __init__(self, parent, *, title='', style=wx.DEFAULT_DIALOG_STYLE, **kwargs):
        super().__init__(parent, title=title, style=style, **kwargs)
        self.SetIcons(app_icons())
        self.dialog_spacing = self.FromDIP(self.OUTER_MARGIN_DIP)

