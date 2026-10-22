"""Tests for the encrypted files used by the task encrypt/decrypt actions."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.media.mp4.encrypted_file import (
    CHUNK_SIZE, decrypt_file, encrypt_file, encrypt_partial_file, is_encrypted_mp4,
)


class EncryptedTaskFileTests(unittest.TestCase):
    def test_encrypt_and_decrypt_completed_mp4(self):
        plain = os.urandom(CHUNK_SIZE + 127)
        key, salt = os.urandom(32), os.urandom(16)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, encrypted, restored = (root / 'video.mp4', root / 'video.mp4.enc',
                                           root / 'restored.mp4')
            source.write_bytes(plain)

            encrypt_file(source, encrypted, key, salt, 1000)
            self.assertTrue(is_encrypted_mp4(encrypted))
            self.assertNotIn(plain[:256], encrypted.read_bytes())

            decrypt_file(encrypted, restored, key, allow_partial=False)
            self.assertEqual(restored.read_bytes(), plain)

    def test_encrypt_and_decrypt_partial_mp4(self):
        plain = os.urandom(CHUNK_SIZE + 127)
        key, salt = os.urandom(32), os.urandom(16)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, encrypted, restored = (root / 'video.mp4.part',
                                           root / 'video.mp4.enc.part',
                                           root / 'restored.mp4.part')
            source.write_bytes(plain)

            encrypt_partial_file(source, encrypted, key, salt, 1000)
            decrypt_file(encrypted, restored, key, allow_partial=True)
            self.assertTrue(is_encrypted_mp4(encrypted))
            self.assertEqual(restored.read_bytes(), plain[:CHUNK_SIZE])

    def test_wrong_key_is_rejected(self):
        key, salt = os.urandom(32), os.urandom(16)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, encrypted, restored = (root / 'video.mp4', root / 'video.mp4.enc',
                                           root / 'restored.mp4')
            source.write_bytes(b'video payload')
            encrypt_file(source, encrypted, key, salt, 1000)

            with self.assertRaisesRegex(ValueError, '密码不正确'):
                decrypt_file(encrypted, restored, os.urandom(32), allow_partial=False)


if __name__ == '__main__':
    unittest.main()
