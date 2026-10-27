"""Verify the icon resources actually shipped and loaded by downloader."""
from pathlib import Path
import unittest

import wx

from src.views.components.icons import (
    ICON_FILES, ICON_ROOT, ICON_SIZES, LOGO_ROOT, app_icons, app_logo, icon_image, toolbar_icon,
)


class IconResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.GetApp() or wx.App(False)

    def test_shared_resource_pack_contains_only_runtime_assets(self):
        expected = {
            Path(directory) / filename
            for directory in ('source', *map(str, ICON_SIZES))
            for filename in ICON_FILES.values()
        }
        actual = {path.relative_to(ICON_ROOT) for path in ICON_ROOT.rglob('*')
                  if path.is_file() and path.suffix.lower() in ('.png', '.svg')
                  and not path.is_relative_to(LOGO_ROOT)}
        self.assertEqual(actual, expected)

    def test_toolbar_resources_load_at_all_supported_sizes(self):
        for name, filename in ICON_FILES.items():
            for size in ICON_SIZES:
                with self.subTest(name=name, size=size):
                    self.assertTrue((ICON_ROOT / str(size) / filename).is_file())
                    image = icon_image(filename, size)
                    self.assertTrue(image.IsOk())
                    self.assertTrue(image.HasAlpha())
                    self.assertEqual(image.GetSize(), wx.Size(size, size))
            self.assertTrue(toolbar_icon(name).IsOk(), name)

    def test_password_icons_support_dip_sizes_from_source(self):
        for name in ('eye', 'eye-off'):
            for size in (16, 20, 32, 40):
                with self.subTest(name=name, size=size):
                    self.assertTrue((ICON_ROOT / 'source' / ICON_FILES[name]).is_file())
                    image = icon_image(ICON_FILES[name], size)
                    self.assertTrue(image.IsOk())
                    self.assertEqual(image.GetSize(), wx.Size(size, size))

    def test_application_logo_remains_available(self):
        self.assertEqual(LOGO_ROOT, ICON_ROOT / 'logo')
        self.assertTrue((LOGO_ROOT / 'logo.svg').is_file())
        bundle = app_icons()
        for size in (16, 32, 64, 128):
            self.assertTrue(bundle.GetIcon(wx.Size(size, size)).IsOk())
        self.assertTrue(app_logo().IsOk())
