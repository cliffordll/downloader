"""带独立行号栏的播放列表文本编辑器。"""
import wx
import wx.stc as stc


class PlaylistEditor(stc.StyledTextCtrl):
    def __init__(self, parent):
        super().__init__(parent)
        self.SetCodePage(stc.STC_CP_UTF8)
        self.SetLexer(stc.STC_LEX_NULL)
        self.StyleSetFont(stc.STC_STYLE_DEFAULT, parent.GetFont())
        self.StyleSetForeground(stc.STC_STYLE_DEFAULT, wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT))
        self.StyleSetBackground(stc.STC_STYLE_DEFAULT, wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW))
        self.StyleClearAll()
        self.SetSelBackground(True, wx.SystemSettings.GetColour(wx.SYS_COLOUR_HIGHLIGHT))
        self.SetSelForeground(True, wx.SystemSettings.GetColour(wx.SYS_COLOUR_HIGHLIGHTTEXT))
        self.SetCaretForeground(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT))
        self.SetWrapMode(stc.STC_WRAP_WORD)
        # 行号由编辑器边栏绘制，与正文共用滚动位置，不会混入复制或下载内容。
        self.SetMarginType(0, stc.STC_MARGIN_NUMBER)
        self.StyleSetForeground(stc.STC_STYLE_LINENUMBER, wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        self.StyleSetBackground(stc.STC_STYLE_LINENUMBER, wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNFACE))
        self.SetMarginWidth(1, 0)
        self.SetMarginWidth(2, 0)
        self.Bind(stc.EVT_STC_CHANGE, self._UpdateLineNumbers)
        self.Bind(stc.EVT_STC_ZOOM, self._UpdateLineNumbers)
        self._UpdateLineNumbers()

    def _UpdateLineNumbers(self, event=None):
        # 行数增加或缩放时调整边栏，避免千行以上的行号被截断。
        digits = max(2, len(str(self.GetLineCount())))
        width = self.TextWidth(stc.STC_STYLE_LINENUMBER, '9' * digits) + self.FromDIP(12)
        if self.GetMarginWidth(0) != width:
            self.SetMarginWidth(0, width)
        if event is not None:
            event.Skip()
