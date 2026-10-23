"""Shared encrypted-task password persistence and verification tests."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from src.core.task_encryption import TaskEncryption


class TaskEncryptionTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[1] / '.test-temp'
        temp_root.mkdir(exist_ok=True)
        self.temp = TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'encryption.json'
        self.password = TaskEncryption(self.path)

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

    def test_password_can_be_reused_from_current_windows_account(self):
        self.password.set_password('shared password')
        if self.password.unlock_local() is None:
            self.skipTest('This platform has no supported local secret store.')
        context = self.password.unlock_local()
        self.assertEqual(context, self.password.unlock('shared password'))

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

    def test_empty_password_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '不能为空'):
            self.password.set_password('')


if __name__ == '__main__':
    unittest.main()
