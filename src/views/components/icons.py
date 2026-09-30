"""PNG 图标加载和多分辨率适配。"""
from pathlib import Path
import wx

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
    if image.GetSize() != wx.Size(size, size):
        image = image.Scale(size, size, wx.IMAGE_QUALITY_HIGH)
    return image


def toolbar_icon(name):
    filename = ICON_FILES[name]
    return wx.BitmapBundle.FromBitmaps([
        wx.Bitmap(icon_image(filename, size)) for size in ICON_SIZES
    ])


