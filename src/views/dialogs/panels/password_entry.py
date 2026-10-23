"""Password text entry with a button to reveal or conceal the value."""

import sys

import wx

from src.views.components.icons import ICON_FILES, icon_image



class PasswordEntry(wx.Panel):
    """Password field with an inline eye button to temporarily reveal its value."""

    def __init__(self, parent):
        super().__init__(parent)
        self.visible = False
        self._focused = False
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.text = wx.TextCtrl(self, style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER | wx.BORDER_NONE)
        self.eye = wx.StaticBitmap(self, bitmap=self._eye_bitmap(False),
                                   size=self.FromDIP(wx.Size(20, 20)),
                                   style=wx.BORDER_NONE)
        self.eye.SetBackgroundColour(self.text.GetBackgroundColour())
        self.eye.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        self.eye.SetToolTip('显示密码')
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(self.text, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.TOP | wx.BOTTOM,
                border=self.FromDIP(3))
        row.Add(self.eye, flag=wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT,
                border=self.FromDIP(4))
        self.SetSizer(row)
        self.SetMinSize(wx.Size(-1, self.text.GetBestSize().height + self.FromDIP(6)))
        self.eye.Bind(wx.EVT_LEFT_UP, self._toggle_visibility)
        self.text.Bind(wx.EVT_SET_FOCUS, self._on_text_focus)
        self.text.Bind(wx.EVT_KILL_FOCUS, self._on_text_blur)
        self.Bind(wx.EVT_PAINT, self._on_paint)

    def _on_text_focus(self, event):
        self._focused = True
        self.Refresh()
        event.Skip()

    def _on_text_blur(self, event):
        self._focused = False
        self.Refresh()
        event.Skip()

    def _on_paint(self, _event):
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(self.text.GetBackgroundColour()))
        dc.Clear()
        color = wx.Colour(0, 120, 215) if self._focused else wx.Colour(150, 150, 150)
        dc.SetPen(wx.Pen(color, self.FromDIP(1)))
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        width, height = self.GetClientSize()
        if width > 1 and height > 1:
            dc.DrawRectangle(0, 0, width - 1, height - 1)

    def _eye_bitmap(self, visible):
        name = 'eye' if visible else 'eye-off'
        size = self.FromDIP(16)
        return wx.Bitmap(icon_image(ICON_FILES[name], size))

    def _toggle_visibility(self, _event):
        self.visible = not self.visible
        style = self.text.GetWindowStyleFlag()
        if self.visible:
            style &= ~wx.TE_PASSWORD
        else:
            style |= wx.TE_PASSWORD
        value = self.text.GetValue()
        insertion_point = self.text.GetInsertionPoint()
        self.text.SetWindowStyleFlag(style)
        self.text.ChangeValue(value)
        self.text.SetInsertionPoint(insertion_point)
        self._set_password_character(0 if self.visible else 0x25CF)
        self.eye.SetBitmap(self._eye_bitmap(self.visible))
        label = '隐藏密码' if self.visible else '显示密码'
        self.eye.SetToolTip(label)
        self.text.SetFocus()

    def _set_password_character(self, character):
        """wx style changes alone leave ES_PASSWORD's native mask active on Windows."""
        if sys.platform == 'win32':
            import ctypes
            send_message = ctypes.windll.user32.SendMessageW
            send_message.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                     ctypes.c_size_t, ctypes.c_ssize_t]
            send_message.restype = ctypes.c_ssize_t
            # EM_SETPASSWORDCHAR: 0 displays clear text; a bullet masks it again.
            send_message(self.text.GetHandle(), 0x00CC, character, 0)
            self.text.Refresh()

    def Bind(self, event, handler, *args, **kwargs):
        # Text and Enter events must originate from the inner native text control.
        if event in (wx.EVT_TEXT, wx.EVT_TEXT_ENTER):
            return self.text.Bind(event, handler, *args, **kwargs)
        return super().Bind(event, handler, *args, **kwargs)

    def GetValue(self):
        return self.text.GetValue()

    def SetFocus(self):
        self.text.SetFocus()

    def GetPosition(self):
        return super().GetPosition()
