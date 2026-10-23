import os
from pathlib import Path
import tempfile
import unittest

import requests

from src.core.services.browser_bridge import BrowserBridge
from src.core.http_service import LocalHTTPService
from src.core.services.playback_http import PlaybackHTTPServer
from src.media.encrypted_file import encrypt_file
from src.schemas.task import M3U8Details, M3U8Task, SourceType, TaskSegment


class SharedHTTPServiceTests(unittest.TestCase):
    def test_encrypted_m3u8_playlist_rewrites_segments_under_same_token(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            root = Path(folder)
            key, salt = os.urandom(32), os.urandom(16)
            clear_playlist = ('#EXTM3U\n#EXTINF:4,\nsegments/000001.ts\n'
                              '#EXTINF:4,\nsegments/000002.ts\n'
                              '#EXT-X-ENDLIST\n').encode('utf-8')
            clear_segment = b'encrypted HLS segment payload'
            work = root / '课程A'
            (work / 'segments').mkdir(parents=True)
            playlist_plain = root / 'playlist.m3u8'
            segment_plain = root / 'segment.ts'
            playlist_plain.write_bytes(clear_playlist)
            segment_plain.write_bytes(clear_segment)
            encrypt_file(playlist_plain, work / 'download.m3u8.enc', key, salt, 1000)
            encrypt_file(segment_plain, work / 'segments' / '000001.ts.enc', key, salt, 1000)
            task = M3U8Task(
                name='课程A', save_dir=root, source_type=SourceType.M3U8,
                source_url='https://example.com/index.m3u8',
                details=M3U8Details(
                    playlist_path='download.m3u8.enc',
                    segments=[
                        TaskSegment(sequence=0, segment_path='segments/000001.ts.enc'),
                        TaskSegment(sequence=1, segment_path='segments/000002.ts.enc'),
                    ]))

            host = LocalHTTPService()
            playback = PlaybackHTTPServer(http_service=host)
            try:
                _token, url = playback.create_m3u8_session(task, key)
                response = requests.get(url, timeout=3)
                self.assertEqual(response.status_code, 200)
                self.assertIn('#EXTM3U', response.text)
                self.assertIn('#EXTINF:4,', response.text)
                self.assertNotIn('000002.ts', response.text)
                self.assertEqual(response.text.count('/play/'), 1)
                segment_url = response.text.splitlines()[2]
                self.assertIn('/play/', segment_url)
                self.assertIn('/课程A/', requests.utils.unquote(segment_url))
                segment_response = requests.get(segment_url, timeout=3)
                self.assertEqual(segment_response.status_code, 200)
                self.assertEqual(segment_response.content, clear_segment)

                forbidden = segment_url.replace('000001.ts', 'other.ts')
                self.assertEqual(requests.get(forbidden, timeout=3).status_code, 404)
            finally:
                playback.close()
                host.close()

    def test_browser_capture_and_encrypted_playback_share_listener(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            root = Path(folder)
            config = root / 'pairing.json'
            source, encrypted = root / 'source.mp4', root / 'video.mp4.enc'
            plain = b'encrypted playback through shared listener'
            key, salt = os.urandom(32), os.urandom(16)
            source.write_bytes(plain)
            encrypt_file(source, encrypted, key, salt, 1000)

            host = LocalHTTPService()
            bridge = BrowserBridge(token_path=config, start=False, http_service=host)
            playback = PlaybackHTTPServer(http_service=host)
            try:
                bridge.start()
                _, url = playback.create_session(encrypted, key)
                self.assertEqual(bridge.port, int(url.split(':')[2].split('/')[0]))

                try:
                    response = requests.post(
                        f'http://127.0.0.1:{bridge.port}/capture',
                        json={'type': 'mp4', 'url': 'https://example.com/video.mp4'},
                        headers={'X-AVDownloader-Token': bridge.token,
                                 'Origin': 'chrome-extension://' + 'a' * 32}, timeout=3)
                except requests.ConnectionError as error:
                    if getattr(error.__cause__, 'winerror', None) == 10013 or '10013' in str(error):
                        self.skipTest('sandbox policy blocks loopback client connections')
                    raise
                self.assertEqual(response.status_code, 202)
                self.assertEqual(bridge.inbox.get_nowait()['url'], 'https://example.com/video.mp4')

                try:
                    response = requests.get(url, timeout=3)
                except requests.ConnectionError as error:
                    if getattr(error.__cause__, 'winerror', None) == 10013 or '10013' in str(error):
                        self.skipTest('sandbox policy blocks loopback client connections')
                    raise
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, plain)

                bridge.close()
                self.assertTrue(host.get_status()['running'])
                self.assertEqual(requests.get(url, timeout=3).content, plain)
            finally:
                bridge.close()
                playback.close()
                host.close()
            self.assertFalse(host.get_status()['running'])


if __name__ == '__main__':
    unittest.main()
