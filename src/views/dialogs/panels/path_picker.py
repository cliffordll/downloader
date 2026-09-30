import wx
from pathlib import Path
from src.config.sys_setting import SysSetting
from src.core.path_manager import PathManager

class DownloadHelpDialog(wx.MessageDialog):
    def __init__(self, parent, title, text):
        super().__init__(parent, "", title, wx.OK | wx.ICON_INFORMATION)
        # Keep the native dialog, using body text instead of a large main instruction.
        self.SetExtendedMessage(text)
        self.SetOKLabel("关闭")


class DownloadPath(wx.Panel):
    def __init__(self, parent, workPath):
        super().__init__(parent=parent, id=wx.ID_ANY)
        # 主布局
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        lblName = wx.StaticText(self, -1, label="保存目录：", size=(60, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        
        # 根据内容自动调整宽度
        # workPath = SysSetting.GetWorkPath()
        dc = wx.ClientDC(self)
        width, height = dc.GetTextExtent(workPath)
        self.lblPath = wx.StaticText(self, -1, label=workPath, size=(width, height), style=wx.ALIGN_LEFT|wx.VERTICAL|wx.ST_NO_AUTORESIZE)
        self.lblPath.SetBackgroundColour(wx.LIGHT_GREY)
        self.tcDown = wx.TextCtrl(self)
        self.tcDown.SetHint("任务子目录，例如 video01")
        self.tcDown.SetToolTip("填写尚不存在的任务子目录，文件保存在左侧下载根目录与此子目录组合的位置。")

        btnDown = wx.Button(self, label="下载")
        btnDown.Bind(wx.EVT_BUTTON, self.OnBtnDownClicked)

        sizer.Add(lblName, flag=wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=5)
        sizer.Add(self.lblPath, proportion=5, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT, border=5)
        sizer.Add(self.tcDown, proportion=100, flag=wx.EXPAND|wx.ALIGN_LEFT|wx.RIGHT, border=5)
        sizer.Add(btnDown, proportion=1, flag=wx.ALIGN_LEFT|wx.LEFT, border=5)

        self.SetSizer(sizer)

    def SetDownPath(self, baseUri):
        filePath, fileName, absName = PathManager.GetPathFromURI(baseUri)
        self.tcDown.SetValue(filePath)

    def _GetDownPath(self):
        '''返回相对路径'''
        return self.tcDown.GetValue().strip()
    
    def GetDownPath(self):
        '''返回绝对路径'''
        root = Path(self.lblPath.GetLabel().strip()).resolve()
        directory = (root / self._GetDownPath()).resolve()
        if root not in directory.parents:
            raise ValueError('请填写下载根目录内的任务子目录。')
        return str(directory)

    def OnBtnDownClicked(self, event):
        """按钮点击事件处理函数"""
        downPath = self._GetDownPath()
        if not downPath:
            wx.MessageBox(f"请输入保存子目录，或获取播放列表后自动生成。", "警告", wx.ICON_WARNING)
        else:
            try:
                self.GetDownPath()
            except ValueError as error:
                wx.MessageBox(str(error), '警告', wx.ICON_WARNING, parent=self)
                return
            event.Skip()
