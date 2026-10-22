"""浏览器配对窗口，沿用现有弹窗的间距、图标和按钮布局。"""
import wx

from src.views.dialogs.base_dialog import AppDialog
from src.views.dialogs.panels.path_picker import form_action_size


class BrowserConnectDialog(AppDialog):
    def __init__(self, parent, token, *, reset_token, port=17839, apply_port=None, listening=True):
        super().__init__(parent, title='浏览器扩展配对',
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, spacing=12)
        self.reset_token = reset_token
        self.apply_port = apply_port
        border = self.dialog_spacing
        layout = wx.BoxSizer(wx.VERTICAL)
        self.description = wx.StaticText(self, label=(
            '本机接收服务已开启，可接收浏览器扩展发送的视频地址。' if listening else
            '本机接收服务未开启，请调整监听端口并点击“应用”。'))
        layout.Add(self.description, flag=wx.EXPAND | wx.ALL, border=border)

        # 两行共用列宽：标签、输入框、主要操作、次要操作。
        form = wx.FlexGridSizer(cols=4, vgap=self.FromDIP(8), hgap=self.FromDIP(8))
        form.AddGrowableCol(1, 1)
        form.Add(wx.StaticText(self, label='监听端口：'), flag=wx.ALIGN_CENTER_VERTICAL)
        self.port = wx.TextCtrl(self, value=str(port))
        form.Add(self.port, flag=wx.EXPAND | wx.ALIGN_CENTER_VERTICAL)
        self.apply_button = wx.Button(self, label='应用', size=form_action_size(self))
        self.apply_button.Enable(apply_port is not None)
        self.apply_button.Bind(wx.EVT_BUTTON, self.OnApplyPort)
        form.Add(self.apply_button, flag=wx.ALIGN_CENTER_VERTICAL)
        form.AddSpacer(1)

        form.Add(wx.StaticText(self, label='配对码：'), flag=wx.ALIGN_CENTER_VERTICAL)
        self.code = wx.TextCtrl(self, value=token, style=wx.TE_READONLY)
        code_width = self.code.GetTextExtent(token).width + self.FromDIP(24)
        self.code.SetMinSize(wx.Size(code_width, -1))
        form.Add(self.code, flag=wx.EXPAND | wx.ALIGN_CENTER_VERTICAL)
        self.copy_button = wx.Button(self, label='复制', size=form_action_size(self))
        self.copy_button.Bind(wx.EVT_BUTTON, self.OnCopy)
        form.Add(self.copy_button, flag=wx.ALIGN_CENTER_VERTICAL)
        reset = wx.Button(self, label='重置', size=form_action_size(self))
        reset.Bind(wx.EVT_BUTTON, self.OnReset)
        form.Add(reset, flag=wx.ALIGN_CENTER_VERTICAL)
        layout.Add(form, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=border)

        self._port_help = '修改后点击“应用”，并同步更新扩展'
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
            f'端口：{self._port_help}；被占用时请更换端口。\n'
            f'配对码：{self._code_help}。\n'
            '服务：打开窗口时尝试开启；关闭窗口继续监听，退出下载器时停止。\n'
            '启动：首次启用后，重启下载器会尝试恢复监听，无需再次打开窗口。')

    def OnApplyPort(self, event):
        try:
            value = self.port.GetValue().strip()
            if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 65535:
                raise ValueError('端口必须是 1～65535 的整数')
            port = self.apply_port(int(value))
        except (OSError, ValueError, RuntimeError) as error:
            wx.MessageBox(f'端口应用失败，原监听和配置保持不变：{error}', '应用失败', wx.OK | wx.ICON_WARNING, parent=self)
            return
        self.port.ChangeValue(str(port))
        self.description.SetLabel('本机接收服务已开启，可接收浏览器扩展发送的视频地址。')
        self._port_help = f'已应用 {port}，请同步更新扩展端口'
        self._UpdateGuide()

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
        if not wx.TheClipboard.Open():
            wx.MessageBox('暂时无法访问剪贴板，请选中配对码手动复制。', '复制失败',
                          wx.OK | wx.ICON_WARNING, parent=self)
            return
        try:
            copied = wx.TheClipboard.SetData(wx.TextDataObject(self.code.GetValue()))
            if copied:
                wx.TheClipboard.Flush()
        finally:
            wx.TheClipboard.Close()
        if copied:
            self.copy_button.SetLabel('已复制')
        else:
            wx.MessageBox('复制失败，请选中配对码手动复制。', '复制失败',
                          wx.OK | wx.ICON_WARNING, parent=self)
