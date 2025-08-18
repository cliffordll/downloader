import wx
from src.views.main_window import MainWindow
 
if __name__ == "__main__":
    # https://m3u8player.org/

    # print(wx.version())
    app = wx.App()
    sample = MainWindow(None, "视频下载")
    sample.Show()
    app.MainLoop()