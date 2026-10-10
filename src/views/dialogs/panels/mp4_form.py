import wx

from src.views.components.help_button import HelpButton
from src.views.dialogs.panels.path_picker import DownloadHelpDialog


class DownloadEditMP4(wx.Panel):
    """MP4 直链输入表单；任务创建由外层对话框负责。"""
    def __init__(self, parent):
        super().__init__(parent=parent, id=wx.ID_ANY)
        sizer = wx.BoxSizer(wx.VERTICAL)

        uriSizer = wx.BoxSizer(wx.HORIZONTAL)
        lblUri = wx.StaticText(self, -1, label='视频网址：', size=self.FromDIP((60, -1)), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        self.tcURI = wx.TextCtrl(self)
        self.tcURI.SetHint('填写 HTTP / HTTPS 视频文件直链')
        uriSizer.Add(lblUri, proportion=1, flag=wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL|wx.BOTTOM|wx.RIGHT, border=self.FromDIP(5))
        uriSizer.Add(self.tcURI, proportion=50, flag=wx.EXPAND|wx.ALIGN_LEFT|wx.BOTTOM|wx.LEFT, border=self.FromDIP(5))
        self.uriHelpButton = HelpButton(self, '视频网址说明', self.OnURIHelp)
        uriSizer.Add(self.uriHelpButton, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT|wx.BOTTOM, border=self.FromDIP(5))

        sizer.Add(uriSizer, flag=wx.EXPAND, border=0)
        self.SetSizer(sizer)

    def OnURIHelp(self, event):
        text = ('填写直接返回 MP4 视频文件的 HTTP / HTTPS 网址，不是视频播放网页地址。\n\n'
                '网址可以携带查询参数，例如：\n'
                'https://example.com/video.mp4?token=abc\n'
                '请保留完整网址及参数。')
        dialog = DownloadHelpDialog(self, '视频网址说明', text)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()

    def GetBaseURI(self):
        return self.tcURI.GetValue()
