"""Secure storage and verification for the shared encrypted-task password."""

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile

from src.core.app_paths import data_dir


class TaskEncryption:
    """Stores a salted password verifier outside the task database and settings."""

    ITERATIONS = 600_000
    FORMAT_VERSION = 1
    VERIFIER_CONTEXT = b'avdownloader:encrypted-task-password:v1'

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else data_dir() / 'encryption.json'

    def is_configured(self):
        return self._read() is not None

    def set_password(self, password, *, current_password=None):
        if not isinstance(password, str) or not password:
            raise ValueError('密码不能为空。')
        existing = self._read()
        if existing is not None:
            if current_password is None or not self.verify(current_password):
                raise ValueError('当前密码不正确。')

        salt = os.urandom(16)
        derived = self._derive(password, salt)
        verifier = hmac.digest(derived, self.VERIFIER_CONTEXT, 'sha256')
        data = {
            'format_version': self.FORMAT_VERSION,
            'iterations': self.ITERATIONS,
            'salt': base64.b64encode(salt).decode('ascii'),
            'verifier': base64.b64encode(verifier).decode('ascii'),
        }
        self._write(data)

    def verify(self, password):
        record = self._read()
        if record is None or not isinstance(password, str):
            return False
        try:
            salt = base64.b64decode(record['salt'], validate=True)
            expected = base64.b64decode(record['verifier'], validate=True)
            derived = self._derive(password, salt, record['iterations'])
        except (KeyError, TypeError, ValueError):
            return False
        actual = hmac.digest(derived, self.VERIFIER_CONTEXT, 'sha256')
        return hmac.compare_digest(actual, expected)

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(data, dict)
                    or data.get('format_version') != self.FORMAT_VERSION
                    or data.get('iterations') != self.ITERATIONS
                    or not isinstance(data.get('salt'), str)
                    or not isinstance(data.get('verifier'), str)):
                raise ValueError('加密密码配置格式无效。')
            return data
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f'读取加密密码配置失败：{error}') from error

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='encryption-', suffix='.tmp', delete=False) as target:
                temp_name = target.name
                json.dump(data, target, ensure_ascii=True, separators=(',', ':'))
                target.flush()
                os.fsync(target.fileno())
            os.replace(temp_name, self.path)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def _derive(password, salt, iterations=ITERATIONS):
        return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt,
                                   iterations, dklen=32)


