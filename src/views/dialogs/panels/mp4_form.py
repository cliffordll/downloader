import wx

from src.views.components.help_button import HelpButton
from src.views.dialogs.panels.path_picker import DownloadHelpDialog, form_label_size, form_action_size


class DownloadEditMP4(wx.Panel):
    """MP4 直链输入表单；任务创建由外层对话框负责。"""
    def __init__(self, parent):
        super().__init__(parent=parent, id=wx.ID_ANY)
        sizer = wx.BoxSizer(wx.VERTICAL)

        uriSizer = wx.BoxSizer(wx.HORIZONTAL)
        lblUri = wx.StaticText(self, -1, label='直链地址：', size=form_label_size(self, 60), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        self.tcURI = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_CHARWRAP)
        self.tcURI.SetMinSize(wx.Size(-1, self.tcURI.GetCharHeight() * 2 + self.FromDIP(10)))
        self.tcURI.SetHint('填写可直接下载的文件地址，如 MP4、FLV')
        uriSizer.Add(lblUri, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.FromDIP(10))
        uriSizer.Add(self.tcURI, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.FromDIP(10))
        self.uriHelpButton = HelpButton(self, '直链地址说明', self.OnURIHelp)
        self.uriHelpButton.SetMinSize(form_action_size(self))
        uriSizer.Add(self.uriHelpButton, flag=wx.ALIGN_CENTER_VERTICAL)

        sizer.Add(uriSizer, flag=wx.EXPAND, border=0)
        self.SetSizer(sizer)

    def OnURIHelp(self, event):
        text = ('填写可直接下载的视频文件 HTTP / HTTPS 地址，例如 MP4、FLV 等格式；不是视频播放网页地址。\n'
                '服务端需要通过该地址返回文件数据。\n\n'
                '地址可以携带查询参数，例如：\n'
                'https://example.com/video.mp4?token=abc\n'
                '请保留完整地址及参数。')
        dialog = DownloadHelpDialog(self, '直链地址说明', text)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()

    def GetBaseURI(self):
        return self.tcURI.GetValue()
