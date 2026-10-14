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


DEFAULT_PORT = 17839


def validate_port(port):
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('端口必须是 1～65535 的整数')
    return port


def save_token(path, token, *, port=DEFAULT_PORT):
    validate_port(port)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='browser-pairing-', suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
            json.dump({'token': token, 'port': port}, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_or_create_pairing(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        token = secrets.token_urlsafe(32)
        save_token(path, token)
        return token, DEFAULT_PORT
    except (ValueError, UnicodeError) as error:
        raise ValueError('配对配置损坏，请检查 browser-pairing.json') from error
    token = data.get('token') if isinstance(data, dict) else None
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        raise ValueError('配对配置中的配对码无效，请检查 browser-pairing.json')
    return token, validate_port(data.get('port', DEFAULT_PORT))
