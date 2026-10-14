"""持久保存浏览器配对码；失败时保留原文件和原配对状态。"""
import json
import os
from pathlib import Path
import re
import secrets
import tempfile

from src.core.app_paths import data_dir


def pairing_path():
    return data_dir() / 'browser-pairing.json'


def save_token(path, token):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='browser-pairing-', suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
            json.dump({'token': token}, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_or_create_token(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        token = secrets.token_urlsafe(32)
        save_token(path, token)
        return token
    except (ValueError, UnicodeError) as error:
        raise ValueError('配对配置损坏，请检查 browser-pairing.json') from error
    token = data.get('token') if isinstance(data, dict) else None
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        raise ValueError('配对配置中的配对码无效，请检查 browser-pairing.json')
    return token
