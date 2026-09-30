import wx
import sqlite3
import sys
from src.views.main_frame import MainFrame
from src.media.m3u8.m3u8_downloader import M3U8Downloader
from src.storage.task_repository import TaskDataError
 
if __name__ == "__main__":
    # https://m3u8player.org/

    # print(wx.version())
    # 在创建窗口前设置独立的任务栏身份，避免源码运行时归入 Python 图标。
    if sys.platform == 'win32':
        import ctypes
        set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        result = set_app_id('AVDownloader.Desktop')
        if result < 0:
            raise OSError(f'设置应用任务栏标识失败：HRESULT {result & 0xffffffff:#x}')
    app = wx.App()
    app.SetAppName('AVDownloader')
    try:
        sample = MainFrame(None, "AVDownloader")
    except (OSError, ValueError, sqlite3.Error, TaskDataError) as error:
        # 任务库读取失败必须明确提示，不能显示空列表或退回扫描旧目录。
        wx.MessageBox(f"无法加载任务：{error}", "启动失败", wx.OK | wx.ICON_ERROR)
        raise SystemExit(1)
    sample.Show()
    app.MainLoop()
    M3U8Downloader.Shutdown()
