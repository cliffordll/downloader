import wx

from src.core.sys_setting import SysSetting


class TabSetting(wx.Panel):
    """设置表单，由 SettingsDialog 承载，也可单独嵌入面板。"""

    def __init__(self, parent):
        super().__init__(parent)
        layout = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, vgap=self.FromDIP(10), hgap=self.FromDIP(12))
        grid.AddGrowableCol(1, 1)
        self.controls = {}

        def row(key, label, control):
            grid.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(control, 1, wx.EXPAND)
            self.controls[key] = control

        row('download_dir', '默认下载目录', wx.DirPickerCtrl(self, message='选择默认下载文件夹', style=wx.DIRP_USE_TEXTCTRL))
        self.controls['download_dir'].GetPickerCtrl().SetLabel('选择文件夹')
        self.controls['download_dir'].SetToolTip('用于之后新建任务；已有任务仍保存到各自的原目录。')
        row('max_workers', '最大并发数（1～16）', wx.SpinCtrl(self, min=1, max=16))
        row('request_interval', '请求启动间隔（秒）', wx.SpinCtrlDouble(self, min=0, max=60, inc=0.1))
        self.controls['request_interval'].SetDigits(1)
        row('max_retries', '失败后重试次数（0～10）', wx.SpinCtrl(self, min=0, max=10))
        row('connect_timeout', '连接超时（秒）', wx.SpinCtrl(self, min=1, max=300))
        row('read_timeout', '读取超时（秒）', wx.SpinCtrl(self, min=1, max=600))
        row('ffmpeg_path', 'FFmpeg 路径（留空自动检测）', wx.FilePickerCtrl(
            self, message='选择 FFmpeg 可执行文件', wildcard='可执行文件 (*.exe)|*.exe|所有文件 (*.*)|*.*',
            style=wx.FLP_OPEN | wx.FLP_USE_TEXTCTRL))
        self.controls['ffmpeg_path'].GetPickerCtrl().SetLabel('选择文件')
        self.controls['ffmpeg_path'].SetToolTip('留空时优先使用项目 scripts 目录中的 FFmpeg，找不到再查找系统 PATH。')
        row('auto_merge', '下载完成后', wx.CheckBox(self, label='自动合并为 MP4'))
        layout.Add(grid, 0, wx.EXPAND | wx.TOP | wx.LEFT | wx.RIGHT, self.FromDIP(12))
        note = wx.StaticText(self, label=(
            '设置对后续请求生效，正在进行的请求正常完成。\n'
            '默认下载目录仅影响新任务，可在下载或合并期间修改。\n'
            '遇到 429 自动等待；403 不自动重试。自动合并默认关闭。'))
        layout.Add(note, 0, wx.ALL, self.FromDIP(12))
        self.status = wx.StaticText(self)
        layout.Add(self.status, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, self.FromDIP(12))
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        reset = wx.Button(self, label='恢复默认值')
        reset.Bind(wx.EVT_BUTTON, lambda event: self.LoadValues(SysSetting.Defaults()))
        buttons.Add(reset, 0)
        buttons.AddStretchSpacer()
        save = wx.Button(self, wx.ID_SAVE, '保存')
        save.Bind(wx.EVT_BUTTON, self.OnSave)
        buttons.Add(save, 0)
        if isinstance(parent, wx.Dialog):
            cancel = wx.Button(self, wx.ID_CANCEL, '取消')
            cancel.Bind(wx.EVT_BUTTON, lambda event: parent.EndModal(wx.ID_CANCEL))
            buttons.Add(cancel, 0, wx.LEFT, self.FromDIP(8))
            save.SetDefault()
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, self.FromDIP(12))
        self.SetSizer(layout)
        self.LoadValues(SysSetting.GetAll())
        self.status.SetLabel(SysSetting._load_error)

    def LoadValues(self, values):
        for key, control in self.controls.items():
            if key in ('download_dir', 'ffmpeg_path'):
                control.SetPath(values[key])
            elif key in ('max_workers', 'max_retries', 'connect_timeout', 'read_timeout'):
                control.SetValue(int(values[key]))
            else:
                control.SetValue(values[key])
        self.status.SetLabel('')

    def OnSave(self, event):
        # 保留由查看菜单管理的偏好，避免保存下载设置时重置它们。
        values = SysSetting.GetAll()
        values.update({key: control.GetPath() if key in ('download_dir', 'ffmpeg_path') else control.GetValue()
                       for key, control in self.controls.items()})
        try:
            values = SysSetting.Validate(values)
            # 新版任务已固定保存绝对目录；这里修改默认值，不移动文件或重建活动队列。
            SysSetting.Save(values)
        except (ValueError, OSError) as error:
            wx.MessageBox(str(error), '设置未保存', wx.OK | wx.ICON_WARNING, self)
            return
        if isinstance(self.GetParent(), wx.Dialog):
            self.GetParent().EndModal(wx.ID_OK)
        else:
            self.status.SetLabel('设置已保存。')


class SettingsDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title='设置', style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        layout = wx.BoxSizer(wx.VERTICAL)
        self.form = TabSetting(self)
        layout.Add(self.form, 1, wx.EXPAND)
        self.SetSizerAndFit(layout)
        self.SetMinSize(self.GetSize())
        self.CenterOnParent()
