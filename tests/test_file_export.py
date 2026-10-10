from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.core.file_export import save_file_as


class FileExportTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'video.mp4'
        self.source.write_bytes(b'complete video')
        directory = self.root / 'other'
        directory.mkdir()
        self.destination = directory / 'renamed.mp4'

    def test_copies_to_other_directory_and_preserves_source(self):
        save_file_as(self.source, self.destination)
        self.assertEqual(self.destination.read_bytes(), self.source.read_bytes())
        self.assertEqual(list(self.destination.parent.iterdir()), [self.destination])

    def test_replaces_existing_destination(self):
        self.destination.write_bytes(b'old video')
        save_file_as(self.source, self.destination)
        self.assertEqual(self.destination.read_bytes(), b'complete video')
        self.assertTrue(self.source.is_file())

    def test_failed_copy_preserves_existing_destination_and_cleans_temporary(self):
        self.destination.write_bytes(b'old video')
        def fail(source, target):
            Path(target).write_bytes(b'partial')
            raise OSError('disk full')
        with patch('src.core.file_export.shutil.copy2', side_effect=fail):
            with self.assertRaises(OSError):
                save_file_as(self.source, self.destination)
        self.assertEqual(self.destination.read_bytes(), b'old video')
        self.assertEqual(list(self.destination.parent.iterdir()), [self.destination])

    def test_rejects_same_file_and_missing_source(self):
        with self.assertRaises(ValueError):
            save_file_as(self.source, self.source)
        with self.assertRaises(FileNotFoundError):
            save_file_as(self.root / 'missing.mp4', self.destination)
        self.assertFalse(self.destination.exists())
