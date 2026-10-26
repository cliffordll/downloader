"""PNG 图标加载和多分辨率适配。"""
from pathlib import Path
import sys
import wx
import wx.adv
import wx.svg

ICON_ROOT = Path(__file__).resolve().parents[3] / "icons"
ICON_FILES = {
    "open": "tools/open.png",
    "playlist": "files/m3u8.png",
    "segment": "files/ts.png",
    "mp4": "files/mp4.png",
    "url": "files/url.png",
    "browser": "tools/extension.png",
    "batch": "tools/batch.png",
    "expand": "tools/expand.png",
    "collapse": "tools/collapse.png",
    "refresh": "tools/refresh.png",
    "pause": "tools/pause.png",
    "start": "tools/start.png",
    "task-start": "tools/task-start.png",
    "task-download": "tools/task-download.png",
    "download-all": "tools/download-all.png",
    "upload-all": "tools/upload-all.png",
    "task-upload": "tools/task-upload.png",
    "task-resume": "tools/task-resume.png",
    "task-pause": "tools/task-pause.png",
    "task-stop": "tools/task-stop.png",
    "settings": "tools/settings.png",
    "player": "tools/player.png",
    "lock": "tools/lock.png",
    "key": "tools/key.png",
    "unlock": "tools/unlock.png",
    "eye": "tools/eye.png",
    "eye-off": "tools/eye-off.png",
    "help": "tools/help.png",
    "about": "tools/about.png",
    "edit": "tools/edit.png",
    "check": "tools/check.png",
    "warning": "tools/warning.png",
    "external": "tools/external.png",
    "sort": "tools/sort.png",
    "upload": "tools/upload.png",
    "close": "tools/close.png",
    "copy": "tools/copy.png",
    "delete": "tools/delete.png",
    "download": "tools/download.png",
    "file": "tools/file.png",
    "filter": "tools/filter.png",
    "home": "tools/home.png",
    "link": "tools/link.png",
    "list": "tools/list.png",
    "load": "tools/load.png",
    "merge": "tools/merge.png",
    "more": "tools/more.png",
    "new": "tools/new.png",
    "retry": "tools/retry.png",
    "save": "tools/save.png",
    "search": "tools/search.png",
    "stop": "tools/stop.png",
}
ICON_SIZES = (24, 30, 36, 48)


def icon_image(filename, size):
    """Load an exact-size PNG, or scale the shared source for other sizes."""
    path = ICON_ROOT / str(size) / filename
    if not path.is_file():
        path = ICON_ROOT / "source" / filename
    image = wx.Image(str(path), wx.BITMAP_TYPE_PNG)
    if not image.IsOk():
        raise ValueError(f"Cannot load icon: {path}")
    if image.GetHeight() != size:
        width = max(1, round(image.GetWidth() * size / image.GetHeight()))
        image = image.Scale(width, size, wx.IMAGE_QUALITY_HIGH)
    return image


def _app_icon_bitmap(vector, size):
    """直接按目标尺寸绘制，保留自然抗锯齿和透明镂空。"""
    return vector.ConvertToBitmap(scale=size / vector.width, width=size, height=size)


def app_icons():
    """为标题栏、任务栏和任务切换提供不同尺寸的应用 Logo。"""
    # 标题栏和任务栏共用完整版本；Windows 标题栏另按实际 DPI 设置尺寸。
    image = wx.svg.SVGimage.CreateFromFile(str(ICON_ROOT / 'source/app/logo.svg'))
    bundle = wx.IconBundle()
    for size in (16, 20, 24, 28, 32, 40, 48, 64, 128, 256):
        bundle.AddIcon(wx.Icon(_app_icon_bitmap(image, size)))
    return bundle


def app_logo(size=64):
    """对话框正文使用的应用 Logo，提供高分辨率版本。"""
    vector = wx.svg.SVGimage.CreateFromFile(str(ICON_ROOT / 'source/app/logo.svg'))
    return wx.BitmapBundle.FromBitmaps([_app_icon_bitmap(vector, size),
                                      _app_icon_bitmap(vector, size * 2)])


def create_dock_icon():
    """源码运行时替换 Mac 的 Python Dock 图标；调用方持有对象直到退出。"""
    if sys.platform != 'darwin':
        return None
    vector = wx.svg.SVGimage.CreateFromFile(str(ICON_ROOT / 'source/app/logo.svg'))
    # Dock 画布保留透明边距，避免几乎铺满画布的 Logo 比邻近应用显得更大。
    size = 512
    content_size = round(size * 0.82)
    margin = (size - content_size) / 2
    bitmap = vector.ConvertToBitmap(tx=margin, ty=margin, scale=content_size / vector.width,
                                    width=size, height=size)
    icon = wx.Icon(bitmap)
    dock = wx.adv.TaskBarIcon(iconType=wx.adv.TBI_DOCK)
    if not dock.SetIcon(icon):
        dock.Destroy()
        raise OSError('无法设置 macOS Dock 图标')
    return dock


def set_titlebar_icon(window):
    """Windows 标题栏使用完整 Logo，按窗口 DPI 生成系统要求的小图标尺寸。"""
    if sys.platform != 'win32':
        return
    import ctypes
    user32 = ctypes.windll.user32
    get_dpi = user32.GetDpiForWindow
    get_dpi.argtypes = [ctypes.c_void_p]
    get_dpi.restype = ctypes.c_uint
    metric = user32.GetSystemMetricsForDpi
    metric.argtypes = [ctypes.c_int, ctypes.c_uint]
    metric.restype = ctypes.c_int
    size = metric(49, get_dpi(window.GetHandle()))  # SM_CXSMICON
    vector = wx.svg.SVGimage.CreateFromFile(str(ICON_ROOT / 'source/app/logo.svg'))
    icon = wx.Icon(_app_icon_bitmap(vector, size))
    send = user32.SendMessageW
    send.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    send.restype = ctypes.c_ssize_t
    send(window.GetHandle(), 0x0080, 0, icon.GetHandle())  # WM_SETICON / ICON_SMALL
    window._titlebar_icon = icon  # 窗口使用期间保留原生图标句柄。


def toolbar_icon(name):
    filename = ICON_FILES[name]
    return wx.BitmapBundle.FromBitmaps([
        wx.Bitmap(icon_image(filename, size)) for size in ICON_SIZES
    ])
