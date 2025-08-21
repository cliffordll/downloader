import wx
from src.views.main_window import MainWindow
from src.views.main_frame import MainFrame
from src.managers.downloader import Downloader
 
if __name__ == "__main__":
    # https://m3u8player.org/

    # print(wx.version())
    app = wx.App()
    # sample = MainWindow(None, "视频下载")
    sample = MainFrame(None, "视频下载")
    sample.Show()
    app.MainLoop()
    Downloader.isStop = True