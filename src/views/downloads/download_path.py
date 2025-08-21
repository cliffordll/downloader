import wx
from src.managers.sys_setting import SysSetting
from src.managers.file_manager import FileManager

class DownloadPath(wx.Panel):
    def __init__(self, parent, workPath):
        super().__init__(parent=parent, id=wx.ID_ANY)
        # 主布局
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        lblName = wx.StaticText(self, -1, label="下载地址：", size=(60, -1), style=wx.ALIGN_LEFT|wx.ST_NO_AUTORESIZE)
        
        # 根据内容自动调整宽度
        # workPath = SysSetting.GetWorkPath()
        dc = wx.ClientDC(self)
        width, height = dc.GetTextExtent(workPath)
        self.lblPath = wx.StaticText(self, -1, label=workPath, size=(width, height), style=wx.ALIGN_LEFT|wx.VERTICAL|wx.ST_NO_AUTORESIZE)
        self.lblPath.SetBackgroundColour(wx.LIGHT_GREY)
        self.tcDown = wx.TextCtrl(self)

        btnDown = wx.Button(self, label="下载")
        btnDown.Bind(wx.EVT_BUTTON, self.OnBtnDownClicked)

        sizer.Add(lblName, flag=wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=5)
        sizer.Add(self.lblPath, proportion=5, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT, border=5)
        sizer.Add(self.tcDown, proportion=100, flag=wx.EXPAND|wx.ALIGN_LEFT|wx.RIGHT, border=5)
        sizer.Add(btnDown, proportion=1, flag=wx.ALIGN_LEFT|wx.LEFT, border=5)

        self.SetSizer(sizer)

    def SetDownPath(self, baseUri):
        filePath, fileName, absName = FileManager.GetPathFromURI(baseUri)
        self.tcDown.SetValue(filePath)

    def _GetDownPath(self):
        '''返回相对路径'''
        return self.tcDown.GetValue().strip()
    
    def GetDownPath(self):
        '''返回绝对路径'''
        downPath = self.lblPath.GetLabel().strip()
        downPath += self._GetDownPath()
        # print(downPath)
        return downPath

    def OnBtnDownClicked(self, event):
        """按钮点击事件处理函数"""
        downPath = self._GetDownPath()
        if not downPath:
            wx.MessageBox(f"请输入下载地址，或编辑种子文件后自动获取。", "警告", wx.ICON_WARNING)
        else:
            event.Skip()