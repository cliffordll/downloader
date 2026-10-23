"""Tests for the encrypted files used by the task encrypt/decrypt actions."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.core.task_encryption import prepare_task_artifacts
from src.media.encrypted_file import (
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

    def test_failure_converting_second_artifact_keeps_sources_and_removes_first_output(self):
        key, salt = os.urandom(32), os.urandom(16)
        context = {'wrapping_key': key, 'salt': salt, 'iterations': 1000}
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            video, partial = root / 'video.mp4', root / 'video.mp4.part'
            encrypted, encrypted_partial = root / 'video.mp4.enc', root / 'video.mp4.enc.part'
            video.write_bytes(b'complete video')
            partial.write_bytes(b'partial video')

            with patch('src.core.task_encryption.encrypt_partial_file',
                       side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    prepare_task_artifacts(
                        [(video, encrypted, True), (partial, encrypted_partial, False)],
                        True, context)

            self.assertEqual(video.read_bytes(), b'complete video')
            self.assertEqual(partial.read_bytes(), b'partial video')
            self.assertFalse(encrypted.exists())
            self.assertFalse(encrypted_partial.exists())

    def test_prepares_all_outputs_while_preserving_sources_until_commit(self):
        key, salt = os.urandom(32), os.urandom(16)
        context = {'wrapping_key': key, 'salt': salt, 'iterations': 1000}
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            video, partial = root / 'video.mp4', root / 'video.mp4.part'
            encrypted, encrypted_partial = root / 'video.mp4.enc', root / 'video.mp4.enc.part'
            plain_video, plain_partial = b'complete video', os.urandom(CHUNK_SIZE)
            video.write_bytes(plain_video)
            partial.write_bytes(plain_partial)

            prepared = prepare_task_artifacts(
                [(video, encrypted, True), (partial, encrypted_partial, False)], True, context)

            self.assertEqual(len(prepared), 2)
            self.assertEqual(video.read_bytes(), plain_video)
            self.assertEqual(partial.read_bytes(), plain_partial)
            self.assertTrue(is_encrypted_mp4(encrypted))
            self.assertTrue(is_encrypted_mp4(encrypted_partial))

            restored, restored_partial = root / 'restored.mp4', root / 'restored.mp4.part'
            restored_files = prepare_task_artifacts(
                [(encrypted, restored, True), (encrypted_partial, restored_partial, False)],
                False, context)
            self.assertEqual(len(restored_files), 2)
            self.assertEqual(restored.read_bytes(), plain_video)
            self.assertEqual(restored_partial.read_bytes(), plain_partial)
            self.assertTrue(encrypted.exists())
            self.assertTrue(encrypted_partial.exists())


if __name__ == '__main__':
    unittest.main()
