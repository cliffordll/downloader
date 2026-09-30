import wx
import sqlite3
from src.views.main_frame import MainFrame
from src.core.downloader import Downloader
from src.storage.task_repository import TaskDataError
 
if __name__ == "__main__":
    # https://m3u8player.org/

    # print(wx.version())
    app = wx.App()
    try:
        sample = MainFrame(None, "AVDownloader")
    except (OSError, ValueError, sqlite3.Error, TaskDataError) as error:
        # 任务库读取失败必须明确提示，不能显示空列表或退回扫描旧目录。
        wx.MessageBox(f"无法加载任务：{error}", "启动失败", wx.OK | wx.ICON_ERROR)
        raise SystemExit(1)
    sample.Show()
    app.MainLoop()
    Downloader.Shutdown()
