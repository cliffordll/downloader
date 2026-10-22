"""浏览器配对窗口，沿用现有弹窗的间距、图标和按钮布局。"""
import wx

from src.views.dialogs.base_dialog import AppDialog
from src.views.dialogs.panels.path_picker import form_action_size


class BrowserConnectDialog(AppDialog):
    def __init__(self, parent, token, *, reset_token, port=17839, listening=True,
                 port_reassigned=False):
        super().__init__(parent, title='浏览器扩展配对',
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.reset_token = reset_token
        border = self.dialog_spacing
        layout = wx.BoxSizer(wx.VERTICAL)
        description = ('本机接收服务已开启，可接收浏览器扩展发送的视频地址。' if listening else
                       '本机接收服务未开启，打开此窗口时会尝试启动。')
        if port_reassigned:
            description = '上次使用的端口不可用，已自动分配新端口；请同步更新浏览器扩展。'
        self.description = wx.StaticText(self, label=description)
        layout.Add(self.description, flag=wx.EXPAND | wx.ALL, border=border)

        # 两行共用列宽：标签、值、操作。
        form = wx.FlexGridSizer(cols=4, vgap=self.FromDIP(8), hgap=self.FromDIP(8))
        form.AddGrowableCol(1, 1)
        form.Add(wx.StaticText(self, label='监听端口：'), flag=wx.ALIGN_CENTER_VERTICAL)
        self.port = wx.TextCtrl(self, value=str(port), style=wx.TE_READONLY)
        form.Add(self.port, flag=wx.EXPAND | wx.ALIGN_CENTER_VERTICAL)
        self.copy_port_button = wx.Button(self, label='复制', size=form_action_size(self))
        self.copy_port_button.Bind(wx.EVT_BUTTON, self.OnCopyPort)
        form.Add(self.copy_port_button, flag=wx.ALIGN_CENTER_VERTICAL)
        form.Add((0, 0))

        form.Add(wx.StaticText(self, label='配对码：'), flag=wx.ALIGN_CENTER_VERTICAL)
        self.code = wx.TextCtrl(self, value=token, style=wx.TE_READONLY)
        code_width = self.code.GetTextExtent(token).width + self.FromDIP(24)
        self.code.SetMinSize(wx.Size(code_width, -1))
        form.Add(self.code, flag=wx.EXPAND | wx.ALIGN_CENTER_VERTICAL)
        self.copy_button = wx.Button(self, label='复制', size=form_action_size(self))
        self.copy_button.Bind(wx.EVT_BUTTON, self.OnCopy)
        form.Add(self.copy_button, flag=wx.ALIGN_CENTER_VERTICAL)
        self.reset_button = wx.Button(self, label='重置', size=form_action_size(self))
        self.reset_button.Bind(wx.EVT_BUTTON, self.OnReset)
        form.Add(self.reset_button, flag=wx.ALIGN_CENTER_VERTICAL)
        layout.Add(form, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=border)

        self._port_help = '自动分配并记住上次端口；若被占用会重新分配，请同步更新扩展'
        self._code_help = '长期保存，重置后旧码失效，需在扩展中保存新码'
        self.note = wx.StaticText(self)
        self._UpdateGuide()
        layout.Add(self.note, flag=wx.EXPAND | wx.ALL, border=border)
        self.Bind(wx.EVT_CHAR_HOOK, self.OnKey)
        self.SetSizer(layout)
        minimum = layout.GetMinSize()
        self.SetClientSize(wx.Size(max(self.FromDIP(620), minimum.width), minimum.height))
        self.SetMinSize(self.GetSize())
        self.SetMaxSize(wx.Size(-1, self.GetSize().height))
        self.CenterOnParent()
        self.code.SetFocus()
        self.code.SelectAll()

    def _UpdateGuide(self):
        self.note.SetLabel(
            '配对：在扩展“连接下载器”中填写相同端口，粘贴配对码并保存配置。\n'
            f'端口：{self._port_help}。\n'
            f'配对码：{self._code_help}。\n'
            '服务：打开配对设置时尝试开启；关闭窗口继续监听，退出下载器时停止。\n'
            '启动：首次启用后，重启下载器会尝试恢复监听，无需再次打开窗口。')

    def OnCopyPort(self, _event):
        self._CopyText(self.port.GetValue(), self.copy_port_button)

    def OnReset(self, event):
        try:
            token = self.reset_token()
        except (OSError, ValueError) as error:
            wx.MessageBox(f'无法保存新配对码：{error}', '重置失败', wx.OK | wx.ICON_WARNING, parent=self)
            return
        self.code.ChangeValue(token)
        self.code.SetMinSize(wx.Size(self.code.GetTextExtent(token).width + self.FromDIP(24), -1))
        self.SetClientSize(wx.Size(max(self.GetClientSize().width, self.GetSizer().GetMinSize().width),
                                  self.GetClientSize().height))
        self.SetMinSize(self.GetSize())
        self.Layout()
        self.copy_button.SetLabel('复制')
        self._code_help = '已重置，旧码已失效；请复制新码并在扩展中保存'
        self._UpdateGuide()
        self.code.SetFocus()
        self.code.SelectAll()

    def OnKey(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
        else:
            event.Skip()

    def OnCopy(self, event):
        self._CopyText(self.code.GetValue(), self.copy_button)

    def _CopyText(self, value, button):
        if not wx.TheClipboard.Open():
            wx.MessageBox('暂时无法访问剪贴板，请选中内容手动复制。', '复制失败',
                          wx.OK | wx.ICON_WARNING, parent=self)
            return
        try:
            copied = wx.TheClipboard.SetData(wx.TextDataObject(value))
            if copied:
                wx.TheClipboard.Flush()
        finally:
            wx.TheClipboard.Close()
        if copied:
            button.SetLabel('已复制')
        else:
            wx.MessageBox('复制失败，请选中配对码手动复制。', '复制失败',
                          wx.OK | wx.ICON_WARNING, parent=self)
