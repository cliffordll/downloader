"""Shared encrypted-task password persistence and verification tests."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys
import unittest
from unittest.mock import patch

from src.core.task_encryption import TaskEncryption
from src.media.encrypted_file import decrypt_file, encrypt_file, HEADER_SIZE


class TaskEncryptionTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[1] / '.test-temp'
        temp_root.mkdir(exist_ok=True)
        self.temp = TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'encryption.json'
        self.password = TaskEncryption(self.path)
        if sys.platform == 'darwin':
            # Never read or overwrite a real user's application Keychain item in tests.
            store = patch('src.core.task_encryption._keychain_store_password')
            read = patch('src.core.task_encryption._keychain_read_password', return_value=None)
            store.start()
            read.start()
            self.addCleanup(read.stop)
            self.addCleanup(store.stop)

    def test_password_is_persisted_as_a_salted_verifier(self):
        self.assertFalse(self.password.is_configured())
        self.password.set_password('shared password')

        saved = self.path.read_text(encoding='utf-8')
        self.assertNotIn('shared password', saved)
        self.assertTrue(TaskEncryption(self.path).is_configured())
        self.assertTrue(TaskEncryption(self.path).verify('shared password'))
        self.assertFalse(TaskEncryption(self.path).verify('wrong password'))
        context = TaskEncryption(self.path).unlock('shared password')
        self.assertIsNotNone(context)
        self.assertEqual(context['iterations'], TaskEncryption.ITERATIONS)
        self.assertIsNone(TaskEncryption(self.path).unlock('wrong password'))

    @unittest.skipUnless(sys.platform == 'win32', 'Windows DPAPI integration test')
    def test_password_can_be_reused_from_current_windows_account(self):
        self.password.set_password('shared password')
        if self.password.unlock_local() is None:
            self.skipTest('This platform has no supported local secret store.')
        context = self.password.unlock_local()
        self.assertEqual(context, self.password.unlock('shared password'))

    def test_password_can_be_reused_from_macos_keychain(self):
        with patch('src.core.task_encryption.sys.platform', 'darwin'), \
             patch('src.core.task_encryption._keychain_store_password') as store, \
             patch('src.core.task_encryption._keychain_read_password',
                   return_value=b'shared password') as read:
            self.assertTrue(self.password.set_password('shared password'))
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            self.assertNotIn('local_password', saved)
            self.assertNotIn('shared password', self.path.read_text(encoding='utf-8'))
            self.assertEqual(self.password.unlock_local(), self.password.unlock('shared password'))
        store.assert_called_once_with(b'shared password')
        read.assert_called_once_with()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows DPAPI migration test')
    def test_old_password_record_can_be_migrated_after_verification(self):
        self.password.set_password('shared password')
        data = json.loads(self.path.read_text(encoding='utf-8'))
        data.pop('local_password', None)
        self.path.write_text(json.dumps(data), encoding='utf-8')
        self.assertIsNone(self.password.unlock_local())
        if not self.password.remember_password('shared password'):
            self.skipTest('This platform has no supported local secret store.')
        self.assertEqual(self.password.unlock_local(), self.password.unlock('shared password'))

    def test_changing_password_requires_the_current_password(self):
        self.password.set_password('first password')
        with self.assertRaisesRegex(ValueError, '当前密码不正确'):
            self.password.set_password('second password', current_password='wrong')
        self.assertTrue(self.password.verify('first password'))

        self.password.set_password('second password', current_password='first password')
        self.assertFalse(self.password.verify('first password'))
        self.assertTrue(self.password.verify('second password'))

    def test_setting_same_password_preserves_file_wrapping_key(self):
        self.password.set_password('same password')
        before = self.password.unlock('same password')
        self.password.set_password('same password', current_password='same password')
        after = self.password.unlock('same password')
        self.assertEqual(before['salt'], after['salt'])
        self.assertEqual(before['wrapping_key'], after['wrapping_key'])

    def test_password_change_rewraps_only_encrypted_file_headers(self):
        self.password.set_password('first password')
        old_context = self.password.unlock('first password')
        source = Path(self.temp.name) / 'source.mp4'
        encrypted = Path(self.temp.name) / 'source.mp4.enc'
        decrypted = Path(self.temp.name) / 'decrypted.mp4'
        source.write_bytes(b'media payload' * 100)
        encrypt_file(source, encrypted, old_context['wrapping_key'],
                     old_context['salt'], old_context['iterations'])
        before = encrypted.read_bytes()

        self.password.set_password('second password', current_password='first password',
                                   encrypted_files=[encrypted])

        after = encrypted.read_bytes()
        self.assertNotEqual(before[:HEADER_SIZE], after[:HEADER_SIZE])
        self.assertEqual(before[HEADER_SIZE:], after[HEADER_SIZE:])
        new_context = self.password.unlock('second password')
        decrypt_file(encrypted, decrypted, new_context['wrapping_key'])
        self.assertEqual(decrypted.read_bytes(), source.read_bytes())

    def test_password_change_rolls_back_headers_when_password_file_write_fails(self):
        self.password.set_password('first password')
        old_context = self.password.unlock('first password')
        source = Path(self.temp.name) / 'source.mp4'
        encrypted = Path(self.temp.name) / 'source.mp4.enc'
        source.write_bytes(b'media payload')
        encrypt_file(source, encrypted, old_context['wrapping_key'],
                     old_context['salt'], old_context['iterations'])
        before = encrypted.read_bytes()
        original_write = self.password._write

        def fail_write(_data):
            raise OSError('simulated password config failure')

        self.password._write = fail_write
        try:
            with self.assertRaisesRegex(OSError, 'simulated password config failure'):
                self.password.set_password('second password', current_password='first password',
                                           encrypted_files=[encrypted])
        finally:
            self.password._write = original_write
        self.assertEqual(encrypted.read_bytes(), before)
        self.assertTrue(self.password.verify('first password'))

    def test_empty_password_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '不能为空'):
            self.password.set_password('')


if __name__ == '__main__':
    unittest.main()
