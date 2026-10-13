"""Real loopback requests; no browser or user data required."""
import unittest
import requests

from src.core.browser_bridge import BrowserBridge, validate_capture


class BrowserBridgeTests(unittest.TestCase):
    def setUp(self):
        self.bridge = BrowserBridge(port=0)
        self.addCleanup(self.bridge.close)
        self.url = f'http://127.0.0.1:{self.bridge.port}/capture'
        self.capture = {'type': 'm3u8', 'url': 'https://cdn.example/index.m3u8?token=a%2Fb',
                        'referer': 'https://example.com/watch'}
        self.headers = {'X-AVDownloader-Token': self.bridge.token,
                        'Origin': 'chrome-extension://' + 'a' * 32}

    def post(self, data=None, headers=None):
        return requests.post(self.url, json=data if data is not None else self.capture,
                             headers=self.headers if headers is None else headers, timeout=3)

    def test_valid_capture_is_queued_without_starting_download(self):
        response = self.post()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.bridge.inbox.get_nowait(), self.capture)

    def test_bad_token_origin_and_host_are_rejected(self):
        for override in ({'X-AVDownloader-Token': 'wrong'}, {'X-AVDownloader-Token': '\xe9'},
                         {'Origin': 'https://example.com'}, {'Host': 'attacker.example'}):
            with self.subTest(override=override):
                self.assertEqual(self.post(headers={**self.headers, **override}).status_code, 403)
        self.assertTrue(self.bridge.inbox.empty())

    def test_invalid_urls_types_and_headers_are_rejected(self):
        for override in ({'type': 'ts'}, {'url': 'file:///etc/passwd'}, {'url': ''},
                         {'url': 'https://user:pass@example.com/a'}, {'url': 'https://example.com:bad/a'},
                         {'referer': 'https://example.com/\r\nX: bad'}, {'url': 123}):
            self.assertEqual(self.post({**self.capture, **override}).status_code, 400)
        self.assertEqual(requests.post(self.url, data='bad', headers=self.headers, timeout=3).status_code, 415)
        self.assertTrue(self.bridge.inbox.empty())

    def test_queue_is_bounded_and_recovers_after_consumption(self):
        for _ in range(10):
            self.assertEqual(self.post().status_code, 202)
        self.assertEqual(self.post().status_code, 429)
        self.bridge.inbox.get_nowait()
        self.assertEqual(self.post().status_code, 202)

    def test_signed_url_is_unchanged_and_cookie_is_not_imported(self):
        result = validate_capture({**self.capture, 'Cookie': 'secret', 'headers': {'Cookie': 'secret'}})
        self.assertEqual(result, self.capture)

    def test_restart_rotates_pairing_token(self):
        other = BrowserBridge(port=0)
        try:
            self.assertNotEqual(self.bridge.token, other.token)
        finally:
            other.close()
