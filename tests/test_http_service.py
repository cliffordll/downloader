import os
from pathlib import Path
import tempfile
import unittest

import requests

from src.core.services.browser_bridge import BrowserBridge
from src.core.http_service import LocalHTTPService
from src.core.services.playback_http import PlaybackHTTPServer
from src.media.mp4.encrypted_file import encrypt_file


class SharedHTTPServiceTests(unittest.TestCase):
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
