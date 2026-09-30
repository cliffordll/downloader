from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.media.m3u8.ffmpeg_converter import FFmpegConverter


class ConcatPlaylistTests(unittest.TestCase):
    def test_manifest_preserves_order_and_absolute_task_paths(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, output = root / 'download.m3u8', root / 'playlist.txt'
            source.write_text('#EXTM3U\n#EXTINF:2,\nsegments/2.ts\n#EXTINF:2,\nsegments/1.ts\n',
                              encoding='utf-8')
            self.assertTrue(FFmpegConverter.ConcatPlaylist(str(source), str(root), str(output)))
            self.assertEqual(output.read_text(encoding='utf-8').splitlines(),
                [f"file '{root / 'segments' / name}'" for name in ('2.ts', '1.ts')])

    def test_invalid_playlist_does_not_replace_manifest(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / 'download.m3u8', root / 'playlist.txt'
            output.write_text('existing', encoding='utf-8')
            for content in ('', 'not a playlist', '#EXTM3U\n'):
                source.write_text(content, encoding='utf-8')
                self.assertFalse(FFmpegConverter.ConcatPlaylist(str(source), str(root), str(output)))
                self.assertEqual(output.read_text(encoding='utf-8'), 'existing')
