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
    "expand": "tools/expand.png",
    "collapse": "tools/collapse.png",
    "refresh": "tools/refresh.png",
    "pause": "tools/pause.png",
    "start": "tools/start.png",
    "settings": "tools/settings.png",
    "help": "tools/help.png",
    "about": "tools/about.png",
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
    # 任务栏使用完整三斜杠版本；标题栏小图通过 ICON_SMALL 独立设置。
    image = wx.svg.SVGimage.CreateFromFile(str(ICON_ROOT / 'source/app/logo.svg'))
    bundle = wx.IconBundle()
    for size in (16, 20, 24, 28, 32, 40, 48, 64, 128, 256):
        bundle.AddIcon(wx.Icon(_app_icon_bitmap(image, size)))
    return bundle


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
    """Windows 单独设置 ICON_SMALL，不改变 ICON_BIG 的任务栏 Logo。"""
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
    vector = wx.svg.SVGimage.CreateFromFile(str(ICON_ROOT / 'source/app/logo-small.svg'))
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
