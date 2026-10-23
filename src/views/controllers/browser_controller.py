"""浏览器插件连接恢复和捕获地址接收控制器。"""

from queue import Empty

import wx

from src.core.services.browser_bridge import BrowserBridge
from src.core.sys_setting import SysSetting
from src.core.services.browser_pairing import pairing_path
from src.views.dialogs.browser_connect_dialog import BrowserConnectDialog
from src.views.dialogs.mp4_dialog import DownloadDialogMP4
from src.views.dialogs.m3u8_dialog import DownloadDialogMU


class BrowserController:
    """管理插件接收服务，并把捕获内容交给任务创建弹窗。"""

    def __init__(self, frame):
        self.frame = frame

    def OnBrowserConnect(self, event):
        if self.frame.browser_bridge is None:
            try:
                self.frame.browser_bridge = BrowserBridge(start=False, http_service=self.frame._local_http_service)
            except (OSError, ValueError) as error:
                wx.MessageBox(f'无法读取浏览器配对配置：{error}', '连接失败', parent=self.frame)
                return
        if self.frame.browser_bridge.server is None:
            try:
                self.frame.browser_bridge.start()
            except (OSError, RuntimeError) as error:
                self.frame._SetPrimaryStatusText(f'本机接收服务未开启：{error}，请检查端口是否可用')
        if self.frame.browser_bridge.port_changed:
            self.frame._SetPrimaryStatusText(
                f'浏览器插件端口已自动调整为 {self.frame.browser_bridge.port}，请同步更新扩展。')
        dialog = BrowserConnectDialog(self.frame, self.frame.browser_bridge.token, reset_token=self.frame.browser_bridge.reset_token,
                                      port=self.frame.browser_bridge.port,
                                      listening=self.frame.browser_bridge.server is not None,
                                      port_reassigned=self.frame.browser_bridge.port_changed)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()

    def RestoreBrowserConnection(self):
        """首次启用过连接后，重启恢复服务，不弹出配对窗口。"""
        if self.frame.browser_bridge is not None or not pairing_path().is_file():
            return
        try:
            self.frame.browser_bridge = BrowserBridge(http_service=self.frame._local_http_service)
            if self.frame.browser_bridge.port_changed:
                self.frame._SetPrimaryStatusText(
                    f'浏览器插件端口已自动调整为 {self.frame.browser_bridge.port}，请同步更新扩展。')
        except (OSError, ValueError) as error:
            self.frame._SetPrimaryStatusText(f'浏览器连接未恢复：{error}')

    def _CheckBrowserInbox(self):
        if self.frame.browser_bridge is None or self.frame._capture_dialog_open:
            return
        if any(isinstance(window, wx.Dialog) and window.IsModal() for window in wx.GetTopLevelWindows()):
            return
        try:
            capture = self.frame.browser_bridge.inbox.get_nowait()
        except Empty:
            return
        self.frame._capture_dialog_open = True
        # 程序通知界面线程打开创建任务弹窗
        # 相当于告诉 wxPython：“下一次处理界面事件时，执行 self.frame._OpenBrowserCapture(capture)。”
        wx.CallAfter(self.frame._OpenBrowserCapture, capture)

    def _OpenBrowserCapture(self, capture):
        if self.frame._closing or not self.frame or self.frame.IsBeingDeleted():
            return
        dialog = None
        try:
            # 表示取消主窗口的最小化状态。如果它原本没有最小化，通常不会改变当前状态。
            self.frame.Iconize(False)
            # 将窗口提升到其他窗口前面
            self.frame.Raise()
            if capture['type'] == 'm3u8':
                dialog = DownloadDialogMU(self.frame, SysSetting.GetWorkPath(), self.frame.tasks,
                                           title='从浏览器添加 M3U8', capture=capture)
            else:
                dialog = DownloadDialogMP4(self.frame, SysSetting.GetWorkPath(), self.frame.tasks,
                                            title='从浏览器添加 MP4', capture=capture)
            if dialog.ShowModal() == wx.OK:
                self.frame.OnRefresh(None)
        finally:
            if dialog is not None:
                dialog.Destroy()
            self.frame._capture_dialog_open = False

