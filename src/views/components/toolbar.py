"""放在窗口内容区域的工具栏，避免 macOS 标题栏接管布局。"""

import wx
import wx.aui as aui


class _LightToolBarArt(aui.AuiDefaultToolBarArt):
    def Clone(self):
        return _LightToolBarArt()

    def DrawBackground(self, dc, window, rect):
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.SetBrush(wx.Brush('#F0F0F0'))
        dc.DrawRectangle(rect)

    def DrawPlainBackground(self, dc, window, rect):
        self.DrawBackground(dc, window, rect)


class ContentToolBar(aui.AuiToolBar):
    def __init__(self, parent):
        super().__init__(parent, style=aui.AUI_TB_PLAIN_BACKGROUND)
        #self.SetArtProvider(_LightToolBarArt())

    def AddTool(self, tool_id, label, bitmap, shortHelp=''):
        return super().AddTool(tool_id, label, bitmap, short_help_string=shortHelp)

    def SetToolNormalBitmap(self, tool_id, bitmap):
        self.SetToolBitmap(tool_id, bitmap)
