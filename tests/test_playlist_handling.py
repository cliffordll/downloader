import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.managers.file_manager import FileManager
from src.managers.m3m8_parser import M3U8Parser
from src.managers.sys_setting import SysSetting
from src.managers.downloader import Downloader
from src.managers.converter import Converter
from src.views.main_frame import MainFrame
from src.views.tab_index import TabIndex


def playlist(*uris):
    return '#EXTM3U\n' + ''.join(f'#EXTINF:4,\n{uri}\n' for uri in uris) + '#EXT-X-ENDLIST\n'


class PlaylistHandlingTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix='downloader-playlist-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        work_path = patch.object(SysSetting, 'GetWorkPath', return_value=str(self.root) + os.sep)
        work_path.start()
        self.addCleanup(work_path.stop)

    def write(self, name, content):
        path = self.root / name
        path.write_text(content, encoding='utf-8')
        return path

    def seed(self, content, source='https://example.com/media/index.m3u8', name='download.seed'):
        return self.write(name, f'\n{source}\n{content}')

    def test_absolute_urls_do_not_require_base_uri(self):
        url = 'https://cdn.example.com/a.ts'
        content = playlist(url)
        for path in (self.seed(content, source=''), self.write('remote.m3u8', content)):
            with self.subTest(path=path.name):
                segments = FileManager.GetSegments(str(path))
                self.assertEqual([(s.name, s.absUri) for s in segments], [('a.ts', url)])
        tree = FileManager.GetFileInfos()
        self.assertEqual(tree.items[0].childs[0].absUri, url)

    def test_local_relative_segments_have_safe_empty_download_address(self):
        content = playlist('a.ts')
        self.seed(content, source='')
        segment = M3U8Parser(content).parse_media()[0]
        self.assertEqual(segment.absUri, '')
        tree = FileManager.GetFileInfos()
        self.assertEqual(tree.items[0].childs[0].absUri, '')
        _, item = FileManager.GetFileItem(str(self.root / 'a.ts'), None)
        self.assertEqual(item.absUri, '')

    def test_m3u8_preserves_every_segment_and_recovers_paired_seed_urls(self):
        content = playlist('a.ts', 'b.ts')
        self.seed(content)
        path = self.write('download.m3u8', content)
        segments = FileManager.GetSegments(str(path))
        self.assertEqual([s.name for s in segments], ['a.ts', 'b.ts'])
        self.assertEqual([s.absUri for s in segments], [
            'https://example.com/media/a.ts', 'https://example.com/media/b.ts',
        ])
        tree = FileManager.GetFileInfos()
        self.assertEqual(tree.items[0].parent.fileName, 'download.m3u8')
        self.assertEqual([s.absUri for s in tree.items[0].childs], [s.absUri for s in segments])

    def test_unrelated_seed_cannot_supply_download_urls(self):
        self.seed(playlist('a.ts'))
        path = self.write('other.m3u8', playlist('a.ts'))
        self.assertEqual(FileManager.GetSegments(str(path))[0].absUri, '')

    def test_empty_invalid_or_unreadable_m3u8_does_not_replace_seed(self):
        self.seed(playlist('a.ts', 'b.ts'))
        for invalid in ('', 'not a playlist', '#EXTM3U\n', '#EXTM3U\n#EXTINF:invalid,\na.ts\n'):
            self.write('download.m3u8', invalid)
            for names in (['download.seed', 'download.m3u8'], ['download.m3u8', 'download.seed']):
                with self.subTest(invalid=invalid, names=names), patch(
                    'src.managers.file_manager.os.walk', return_value=[(str(self.root), [], names)]
                ):
                    tree = FileManager.GetFileInfos()
                    self.assertEqual(tree.items[0].parent.fileName, 'download.seed')
                    self.assertEqual(len(tree.items[0].childs), 2)
        with patch.object(FileManager, '_ParseM3U8File', side_effect=OSError('unreadable')):
            self.assertEqual(FileManager.GetFileInfos().items[0].parent.fileName, 'download.seed')

    def test_invalid_m3u8_alone_does_not_create_completed_task(self):
        self.write('download.m3u8', '')
        self.assertEqual(FileManager.GetFileInfos().items, [])

    def test_downloaders_use_m3u8_reader(self):
        content = playlist('a.ts', 'b.ts')
        self.seed(content)
        path = self.write('download.m3u8', content)
        receiver = SimpleNamespace(_DownloadCall=lambda *args: None)
        for view in (MainFrame, TabIndex):
            with self.subTest(view=view.__name__), patch.object(Downloader, 'DownloadTSFile') as download:
                view._DownloadFiles(receiver, str(path), [(0, None), (1, None)])
                self.assertEqual([call.args[0] for call in download.call_args_list], [
                    'https://example.com/media/a.ts', 'https://example.com/media/b.ts',
                ])
                self.assertEqual([Path(call.args[1]).name for call in download.call_args_list], ['a.ts', 'b.ts'])

    def test_missing_source_is_reported_without_starting_download(self):
        path = self.write('local.m3u8', playlist('a.ts'))
        receiver = SimpleNamespace(_DownloadCall=lambda *args: None)
        for view in (MainFrame, TabIndex):
            with self.subTest(view=view.__name__), patch('wx.MessageBox') as message, patch.object(
                Downloader, 'DownloadTSFile'
            ) as download:
                view._DownloadFiles(receiver, str(path), [(0, None)])
                download.assert_not_called()
                self.assertIn('缺少下载地址', message.call_args.args[0])

    def test_changed_playlist_does_not_cause_index_error(self):
        path = self.write('local.m3u8', playlist('a.ts'))
        receiver = SimpleNamespace(_DownloadCall=lambda *args: None)
        for view in (MainFrame, TabIndex):
            with self.subTest(view=view.__name__), patch('wx.MessageBox') as message, patch.object(
                Downloader, 'DownloadTSFile'
            ) as download:
                view._DownloadFiles(receiver, str(path), [(1, None)])
                download.assert_not_called()
                message.assert_called_once()

    def test_seed_and_m3u8_generate_identical_merge_lists(self):
        content = playlist('a.ts', 'b.ts')
        seed = self.seed(content)
        local = self.write('download.m3u8', content)
        target = self.root / 'playlist.txt'
        expected = '\n'.join(f"file '{self.root / name}'" for name in ('a.ts', 'b.ts'))
        for path in (seed, local):
            with self.subTest(path=path.name):
                self.assertTrue(FileManager.CreatePlaylist(str(path), str(self.root), str(target)))
                self.assertEqual(target.read_text(), expected)

    def test_invalid_playlist_does_not_launch_converter(self):
        path = self.write('download.m3u8', '')
        receiver = SimpleNamespace(_CreateMP4Call=lambda *args: None)
        with patch('wx.MessageBox'), patch.object(Converter, 'ConvertTSFile') as convert:
            MainFrame._CreateMP4File(receiver, str(path), None)
            TabIndex._CreatePlaylist(receiver, str(path))
            convert.assert_not_called()
        self.assertFalse((self.root / 'playlist.txt').exists())

    def test_completion_preserves_existing_m3u8_content(self):
        content = playlist('a.ts', 'b.ts')
        path = self.write('download.m3u8', content)
        original = path.read_bytes()
        self.assertTrue(FileManager.CreateM3U8File(str(self.root), str(path)))
        self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
