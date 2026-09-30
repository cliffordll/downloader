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


def _small_app_icon(size):
    """按实际像素绘制小图标，避免细线缩放后模糊；保留场记板和下载箭头。"""
    bitmap = wx.Bitmap(size, size)
    dc = wx.MemoryDC(bitmap)
    transparent = wx.Colour(255, 0, 255)
    dc.SetBackground(wx.Brush(transparent))
    dc.Clear()
    dc.SetPen(wx.TRANSPARENT_PEN)
    dc.SetBrush(wx.Brush(wx.Colour(0, 160, 224)))
    unit = size / 16

    def point(x, y):
        return wx.Point(round(x * unit), round(y * unit))

    dc.DrawRoundedRectangle(round(unit), round(unit),
                            size - 2 * round(unit), size - 2 * round(unit), round(unit))
    dc.SetBrush(wx.WHITE_BRUSH)
    # 小尺寸仅保留三条宽斜纹，取消原图中难以辨认的细边线。
    for x in (3, 7, 11):
        dc.DrawPolygon([point(x, 1), point(x + 2, 1),
                        point(x + 4, 4), point(x + 2, 4)])
    dc.DrawPolygon([point(7, 6), point(9, 6), point(9, 9),
                    point(12, 9), point(8, 13), point(4, 9), point(7, 9)])
    dc.SelectObject(wx.NullBitmap)
    bitmap.SetMask(wx.Mask(bitmap, transparent))
    return wx.Icon(bitmap)


def app_icons():
    """为标题栏、任务栏和任务切换提供不同尺寸的应用 Logo。"""
    image = wx.Image(str(ICON_ROOT / 'source' / 'app' / 'logo.png'), wx.BITMAP_TYPE_PNG)
    if not image.IsOk():
        raise ValueError('Cannot load application logo')
    # 收紧原图四周的透明留白，让 Logo 在固定大小的任务栏图标中放大约 9%。
    inset = round(min(image.GetWidth(), image.GetHeight()) * 0.04)
    image = image.GetSubImage(wx.Rect(inset, inset,
                                    image.GetWidth() - 2 * inset,
                                    image.GetHeight() - 2 * inset))
    bundle = wx.IconBundle()
    for size in (16, 20, 24, 28, 32, 40, 48, 64, 128, 256):
        if size <= 32:
            bundle.AddIcon(_small_app_icon(size))
        else:
            scaled = image.Scale(size, size, wx.IMAGE_QUALITY_HIGH)
            bundle.AddIcon(wx.Icon(wx.Bitmap(scaled)))
    return bundle


def toolbar_icon(name):
    filename = ICON_FILES[name]
    return wx.BitmapBundle.FromBitmaps([
        wx.Bitmap(icon_image(filename, size)) for size in ICON_SIZES
    ])


