import os
import socket
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import unquote, urlsplit

from src.media.mp4.encrypted_file import (
    CHUNK_SIZE, EncryptedMP4Reader, EncryptedMP4Writer, context_from_password,
    decrypt_file, encrypt_file, encrypt_partial_file, is_encrypted_mp4,
)
from src.core.services.playback_http import PlaybackHTTPServer


class EncryptedMP4Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key = os.urandom(32)
        self.salt = os.urandom(16)

    def tearDown(self):
        self.temp.cleanup()

    def test_encrypts_file_and_supports_random_range_reads(self):
        plain = os.urandom(CHUNK_SIZE * 2 + 123)
        source, encrypted = self.root / 'source.mp4', self.root / 'video.mp4'
        source.write_bytes(plain)

        encrypt_file(source, encrypted, self.key, self.salt, 1000)

        self.assertTrue(is_encrypted_mp4(encrypted))
        self.assertNotIn(plain[:256], encrypted.read_bytes())
        reader = EncryptedMP4Reader(encrypted, self.key)
        self.assertEqual(reader.size, len(plain))
        self.assertEqual(reader.read_range(0, len(plain)), plain)
        self.assertEqual(reader.read_range(CHUNK_SIZE - 10, CHUNK_SIZE + 20),
                         plain[CHUNK_SIZE - 10:CHUNK_SIZE + 20])
        self.assertEqual(reader.read_range(len(plain), len(plain)), b'')

    def test_wrong_key_and_tampering_are_rejected(self):
        source, encrypted = self.root / 'source.mp4', self.root / 'video.mp4'
        source.write_bytes(os.urandom(128))
        encrypt_file(source, encrypted, self.key, self.salt, 1000)
        with self.assertRaisesRegex(ValueError, '密码不正确'):
            EncryptedMP4Reader(encrypted, os.urandom(32))

        with encrypted.open('r+b') as handle:
            handle.seek(4096 + 3)
            value = handle.read(1)
            handle.seek(4096 + 3)
            handle.write(bytes([value[0] ^ 1]))
        reader = EncryptedMP4Reader(encrypted, self.key)
        with self.assertRaisesRegex(ValueError, '校验失败'):
            reader.read_range(0, reader.size)

    def test_file_embedded_salt_allows_password_only_recovery(self):
        source, encrypted = self.root / 'source.mp4', self.root / 'video.mp4'
        source.write_bytes(b'video bytes')
        password = 'local file password'
        salt = os.urandom(16)
        import hashlib
        wrapping_key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 1000, dklen=32)
        encrypt_file(source, encrypted, wrapping_key, salt, 1000)
        context = context_from_password(encrypted, password)
        reader = EncryptedMP4Reader(encrypted, context['wrapping_key'])
        self.assertEqual(reader.read_range(0, reader.size), b'video bytes')
        with self.assertRaisesRegex(ValueError, '密码不正确'):
            EncryptedMP4Reader(encrypted, context_from_password(encrypted, 'wrong')['wrapping_key'])

    def test_decrypts_completed_and_partial_files(self):
        plain = os.urandom(CHUNK_SIZE + 91)
        source, encrypted, restored = (self.root / 'source.mp4', self.root / 'video.mp4',
                                       self.root / 'restored.mp4')
        source.write_bytes(plain)
        encrypt_file(source, encrypted, self.key, self.salt, 1000)
        decrypt_file(encrypted, restored, self.key, allow_partial=False)
        self.assertEqual(restored.read_bytes(), plain)

        partial_source = self.root / 'video.mp4.part'
        partial_encrypted = self.root / 'video.mp4.part.enc'
        encrypt_partial_file(source, partial_encrypted, self.key, self.salt, 1000)
        decrypt_file(partial_encrypted, partial_source, self.key, allow_partial=True)
        self.assertEqual(partial_source.read_bytes(), plain[:CHUNK_SIZE])

    def test_writer_resume_discards_uncommitted_tail_and_finalizes(self):
        path = self.root / 'partial.mp4'
        first = os.urandom(CHUNK_SIZE + 77)
        writer = EncryptedMP4Writer(path, self.key, self.salt, 1000)
        writer.append(first)
        self.assertEqual(writer.plaintext_bytes, CHUNK_SIZE)
        writer.close()

        writer = EncryptedMP4Writer(path, self.key, self.salt, 1000, resume=True)
        rest = os.urandom(91)
        writer.append(rest)
        writer.finalize(CHUNK_SIZE + len(rest))
        reader = EncryptedMP4Reader(path, self.key)
        self.assertEqual(reader.read_range(0, reader.size), first[:CHUNK_SIZE] + rest)

    def test_loopback_playback_server_serves_range_requests(self):
        plain = os.urandom(CHUNK_SIZE + 37)
        source, encrypted = self.root / 'source.mp4', self.root / 'video.mp4'
        source.write_bytes(plain)
        encrypt_file(source, encrypted, self.key, self.salt, 1000)
        server = PlaybackHTTPServer()
        _, url = server.create_session(encrypted, self.key, 'my video.mp4.enc')
        self.assertEqual(unquote(urlsplit(url).path.rsplit('/', 1)[-1]), 'my video.mp4')
        try:
            request = Request(url, headers={'Range': 'bytes=20-49'})
            with urlopen(request) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.headers['Content-Range'], f'bytes 20-49/{len(plain)}')
                self.assertEqual(response.read(), plain[20:50])
            with urlopen(url) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), plain)
        finally:
            server.close()

    def test_multiple_loopback_playback_servers_are_independent(self):
        plain = b'independent playback sessions'
        source, encrypted = self.root / 'source.mp4', self.root / 'video.mp4'
        source.write_bytes(plain)
        encrypt_file(source, encrypted, self.key, self.salt, 1000)
        server = PlaybackHTTPServer()
        first_token, first_url = server.create_session(encrypted, self.key)
        second_token, second_url = server.create_session(encrypted, self.key)
        try:
            self.assertEqual(first_url.split(':')[2].split('/')[0],
                             second_url.split(':')[2].split('/')[0])
            with urlopen(first_url) as response:
                self.assertEqual(response.read(), plain)
            with urlopen(second_url) as response:
                self.assertEqual(response.read(), plain)
            server.close_session(first_token)
            with self.assertRaises(Exception):
                urlopen(first_url, timeout=1)
            with urlopen(second_url) as response:
                self.assertEqual(response.read(), plain)
        finally:
            server.close_session(first_token)
            server.close_session(second_token)
            server.close()

    def test_playback_service_status_reports_listener_and_sessions(self):
        plain = b'status check'
        source, encrypted = self.root / 'source.mp4', self.root / 'video.mp4'
        source.write_bytes(plain)
        encrypt_file(source, encrypted, self.key, self.salt, 1000)
        server = PlaybackHTTPServer()
        try:
            self.assertFalse(server.get_status()['running'])
            token, url = server.create_session(encrypted, self.key)
            status = server.get_status()
            self.assertTrue(status['running'])
            self.assertEqual(status['address'], '127.0.0.1')
            self.assertEqual(status['port'], int(url.split(':')[2].split('/')[0]))
            self.assertEqual(status['sessions'], 1)
            server.close_session(token)
            self.assertEqual(server.get_status()['sessions'], 0)
        finally:
            server.close()
        self.assertFalse(server.get_status()['running'])

    def test_playback_service_reallocates_unavailable_port(self):
        plain = b'port reassignment'
        source, encrypted = self.root / 'source.mp4', self.root / 'video.mp4'
        source.write_bytes(plain)
        encrypt_file(source, encrypted, self.key, self.salt, 1000)
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            preferred_port = occupied.getsockname()[1]
            assigned = []
            server = PlaybackHTTPServer(port=preferred_port, on_port_assigned=assigned.append)
            try:
                _, url = server.create_session(encrypted, self.key)
                actual_port = int(url.split(':')[2].split('/')[0])
                self.assertNotEqual(actual_port, preferred_port)
                self.assertEqual(assigned, [actual_port])
            finally:
                server.close()

if __name__ == '__main__':
    unittest.main()
