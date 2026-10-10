import sys

# 在导入界面和创建 wx.App 前启用原生 DPI 缩放，尺寸由 FromDIP 换算。
if __name__ == '__main__' and sys.platform == 'win32':
    import ctypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    set_dpi_awareness = user32.SetProcessDpiAwarenessContext
    set_dpi_awareness.argtypes = [ctypes.c_void_p]
    set_dpi_awareness.restype = ctypes.c_bool
    if not set_dpi_awareness(ctypes.c_void_p(-4)):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        error = ctypes.get_last_error()
        # 宿主或 manifest 已设置模式时不重复设置。
        if error != 5:  # ERROR_ACCESS_DENIED
            raise ctypes.WinError(error)

import wx
import sqlite3
from src.views.main_frame import MainFrame
from src.media.m3u8.m3u8_downloader import M3U8Downloader
from src.storage.task_repository import TaskDataError, TaskRepository
from src.core.task_service import TaskService
from src.models.tree_model import load_tree
from src.views.components.icons import create_dock_icon
 
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
    dock_icon = None
    try:
        dock_icon = create_dock_icon()
        # 在创建窗口前初始化任务库并恢复任务；失败时沿用下方启动错误提示。
        repository = TaskRepository()
        tasks = TaskService(repository)
        initial_tree = load_tree(tasks)
        sample = MainFrame(None, "AVDownloader", tasks, initial_tree)
    except (OSError, ValueError, sqlite3.Error, TaskDataError) as error:
        if dock_icon is not None:
            dock_icon.Destroy()
        # 任务库读取失败必须明确提示，不能显示空列表或退回扫描旧目录。
        wx.MessageBox(f"无法启动应用：{error}", "启动失败", wx.OK | wx.ICON_ERROR)
        raise SystemExit(1)
    sample.Show()
    try:
        app.MainLoop()
    finally:
        M3U8Downloader.Shutdown()
        if dock_icon is not None:
            dock_icon.Destroy()
