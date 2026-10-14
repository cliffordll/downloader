"""Real loopback requests; no browser or user data required."""
import unittest
import requests
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
import socket
import json
from src.core.browser_pairing import load_or_create_pairing

from src.core.browser_bridge import BrowserBridge, validate_capture


class BrowserBridgeTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.token_path = Path(temporary.name) / 'browser-pairing.json'
        self.bridge = BrowserBridge(port=0, token_path=self.token_path)
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

    def test_restart_keeps_pairing_token(self):
        other = BrowserBridge(port=0, token_path=self.token_path)
        try:
            self.assertEqual(self.bridge.token, other.token)
        finally:
            other.close()

    def test_reset_persists_new_code_rejects_old_code_and_clears_inbox(self):
        self.assertEqual(self.post().status_code, 202)
        old_token = self.bridge.token
        new_token = self.bridge.reset_token()
        self.assertNotEqual(old_token, new_token)
        self.assertTrue(self.bridge.inbox.empty())
        self.assertEqual(self.post().status_code, 403)
        self.headers['X-AVDownloader-Token'] = new_token
        self.assertEqual(self.post().status_code, 202)
        other = BrowserBridge(port=0, token_path=self.token_path)
        try:
            self.assertEqual(other.token, new_token)
        finally:
            other.close()

    def test_failed_reset_preserves_old_code_and_configuration(self):
        original = self.token_path.read_bytes()
        token = self.bridge.token
        with patch('src.core.browser_pairing.os.replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                self.bridge.reset_token()
        self.assertEqual(self.token_path.read_bytes(), original)
        self.assertEqual(self.bridge.token, token)
        self.assertEqual(self.post().status_code, 202)

    def test_corrupt_config_is_not_silently_replaced(self):
        self.token_path.write_text('{bad', encoding='utf-8')
        with self.assertRaises(ValueError):
            BrowserBridge(port=0, token_path=self.token_path)
        self.assertEqual(self.token_path.read_text(encoding='utf-8'), '{bad')

    def free_port(self):
        with socket.socket() as candidate:
            candidate.bind(('127.0.0.1', 0))
            return candidate.getsockname()[1]

    def test_port_switch_preserves_token_and_queue_and_survives_restart(self):
        self.assertEqual(self.post().status_code, 202)
        token = self.bridge.token
        old_url = self.url
        port = self.free_port()
        self.assertEqual(self.bridge.set_port(port), port)
        self.assertEqual(self.bridge.token, token)
        self.assertEqual(self.bridge.inbox.qsize(), 1)
        self.url = f'http://127.0.0.1:{port}/capture'
        self.assertEqual(self.post().status_code, 202)
        with self.assertRaises(requests.ConnectionError):
            requests.post(old_url, json=self.capture, headers=self.headers, timeout=1)
        self.bridge.close()
        restarted = BrowserBridge(token_path=self.token_path)
        try:
            self.assertEqual(restarted.port, port)
            self.assertEqual(restarted.token, token)
            self.assertEqual(self.post().status_code, 202)
        finally:
            restarted.close()

    def test_occupied_port_keeps_original_service_and_config(self):
        original = self.token_path.read_bytes()
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            with self.assertRaises(OSError):
                self.bridge.set_port(occupied.getsockname()[1])
        self.assertEqual(self.token_path.read_bytes(), original)
        self.assertEqual(self.post().status_code, 202)

    def test_failed_port_save_rolls_back_to_original_service(self):
        original = self.token_path.read_bytes()
        with patch('src.core.browser_pairing.os.replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                self.bridge.set_port(self.free_port())
        self.assertEqual(self.token_path.read_bytes(), original)
        self.assertEqual(self.post().status_code, 202)

    def test_invalid_ports_and_legacy_config(self):
        for port in (0, -1, 65536, True, '17840', 1.5):
            with self.assertRaises(ValueError):
                self.bridge.set_port(port)
        self.token_path.write_text(json.dumps({'token': self.bridge.token}))
        self.assertEqual(load_or_create_pairing(self.token_path), (self.bridge.token, 17839))
