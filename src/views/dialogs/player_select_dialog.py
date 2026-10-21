"""已发现播放器选择弹窗。"""
from pathlib import Path

import wx

from src.views.components.icons import app_icons


class PlayerSelectDialog(wx.Dialog):
    """选择已发现的播放器，或交由调用方打开系统文件选择器。"""

    def __init__(self, parent, players, current_player=None):
        super().__init__(parent, title='选择播放器', style=wx.DEFAULT_DIALOG_STYLE)
        self.SetIcons(app_icons())
        self.players = players
        self.browse_requested = False
        border = self.FromDIP(5)
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(wx.StaticText(self, label='选择用于打开视频的播放器：'),
                   flag=wx.LEFT | wx.RIGHT | wx.TOP, border=border)
        self.choices = wx.ListBox(self, choices=[label for label, _ in players],
                                  size=self.FromDIP(wx.Size(480, 220)))
        layout.Add(self.choices, proportion=1, flag=wx.EXPAND | wx.ALL, border=border)

        footer = wx.BoxSizer(wx.HORIZONTAL)
        self.browse = wx.Button(self, label='浏览…')
        footer.Add(self.browse, flag=wx.ALIGN_CENTER_VERTICAL)
        footer.AddStretchSpacer()
        ok = wx.Button(self, wx.ID_OK, '确定')
        cancel = wx.Button(self, wx.ID_CANCEL, '取消')
        footer.Add(ok, flag=wx.RIGHT, border=border)
        footer.Add(cancel)
        layout.Add(footer, flag=wx.EXPAND | wx.ALL, border=border)
        self.SetSizerAndFit(layout)
        self.SetMinSize(self.FromDIP(wx.Size(520, 300)))
        self.CenterOnParent()
        self.browse.Bind(wx.EVT_BUTTON, self._OnBrowse)
        if players:
            try:
                current = Path(current_player).resolve() if current_player else None
            except (OSError, TypeError, ValueError):
                current = current_player
            selected = next((index for index, (_, path) in enumerate(players)
                             if current and Path(path).resolve() == current), 0)
            self.choices.SetSelection(selected)
            ok.SetDefault()
        else:
            ok.Disable()
            self.browse.SetDefault()
        self.SetEscapeId(wx.ID_CANCEL)

    def _OnBrowse(self, event):
        self.browse_requested = True
        self.EndModal(wx.ID_OK)

    def GetSelection(self):
        return self.choices.GetSelection()
