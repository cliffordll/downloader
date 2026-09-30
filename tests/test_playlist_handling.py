"""验证当前 M3U8 解析器，不依赖旧版 seed 或目录扫描实现。"""
import os
import unittest

from src.media.m3u8.m3u8_parser import M3U8Parser


def playlist(*uris):
    return '#EXTM3U\n' + ''.join(f'#EXTINF:4,\n{uri}\n' for uri in uris) + '#EXT-X-ENDLIST\n'


class PlaylistHandlingTests(unittest.TestCase):
    def test_absolute_urls_do_not_require_base_uri(self):
        url = 'https://cdn.example.com/a.ts'
        segments = M3U8Parser(playlist(url)).parse_media()
        self.assertEqual([(s.name, s.absUri) for s in segments], [('a.ts', url)])

    def test_local_relative_segments_have_empty_download_address(self):
        segments = M3U8Parser(playlist('a.ts', 'chunks/b.ts')).parse_media()
        self.assertEqual([s.absUri for s in segments], ['', ''])
        self.assertEqual([s.name for s in segments], ['a.ts', os.path.join('chunks', 'b.ts')])

    def test_reference_url_resolves_all_segments_in_order(self):
        segments = M3U8Parser(playlist('a.ts', 'chunks/b.ts', '/c.ts'),
                              m3u8_uri='https://example.com/media/index.m3u8').parse_media()
        self.assertEqual([s.absUri for s in segments], [
            'https://example.com/media/a.ts', 'https://example.com/media/chunks/b.ts',
            'https://example.com/c.ts'])

    def test_absolute_segment_keeps_its_own_host_and_query(self):
        url = 'https://cdn.example.com/a.ts?token=123'
        segments = M3U8Parser(playlist(url),
                              m3u8_uri='https://example.com/media/index.m3u8').parse_media()
        self.assertEqual(segments[0].absUri, url)

    def test_parser_does_not_drop_repeated_segments(self):
        segments = M3U8Parser(playlist('a.ts', 'b.ts', 'a.ts')).parse_media()
        self.assertEqual([s.name for s in segments], ['a.ts', 'b.ts', 'a.ts'])
