"""Chunked authenticated MP4 container with password-wrapped random file keys."""

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import struct
import tempfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MAGIC = b'AVDMP4E1'
HEADER_SIZE = 4096
CHUNK_SIZE = 1024 * 1024
TAG_SIZE = 16
VERSION = 1
MAX_CHUNKS = 1 << 32


def is_encrypted_mp4(path):
    try:
        with Path(path).open('rb') as source:
            return source.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def _header_bytes(header, file_key):
    authenticated = {key: value for key, value in header.items() if key != 'header_mac'}
    encoded_payload = json.dumps(authenticated, separators=(',', ':'), sort_keys=True).encode('ascii')
    header['header_mac'] = base64.b64encode(hmac.digest(file_key, encoded_payload, 'sha256')).decode('ascii')
    encoded = json.dumps(header, separators=(',', ':'), sort_keys=True).encode('ascii')
    if len(encoded) > HEADER_SIZE - 12:
        raise ValueError('MP4 加密头超出格式限制。')
    return MAGIC + struct.pack('>I', len(encoded)) + encoded + bytes(HEADER_SIZE - 12 - len(encoded))


def _read_header(source):
    prefix = source.read(12)
    if len(prefix) != 12 or prefix[:8] != MAGIC:
        raise ValueError('这不是 AVDownloader 加密 MP4 文件。')
    size = struct.unpack('>I', prefix[8:])[0]
    if not 1 <= size <= HEADER_SIZE - 12:
        raise ValueError('MP4 加密头损坏。')
    try:
        header = json.loads(source.read(size))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('MP4 加密头损坏。') from error
    if not isinstance(header, dict) or header.get('version') != VERSION:
        raise ValueError('不支持的 MP4 加密格式版本。')
    return header


def _wrap_aad(salt, iterations):
    return MAGIC + struct.pack('>I', VERSION) + salt + struct.pack('>I', iterations)


def _chunk_aad(index, length):
    return MAGIC + struct.pack('>QI', index, length)


def _b64(value):
    return base64.b64encode(value).decode('ascii')


def _unb64(value, expected, name):
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError(f'MP4 加密头中的 {name} 无效。') from error
    if len(result) != expected:
        raise ValueError(f'MP4 加密头中的 {name} 长度无效。')
    return result


def _new_header(wrapping_key, salt, iterations, *, plaintext_size=None):
    file_key = os.urandom(32)
    wrap_nonce = os.urandom(12)
    nonce_prefix = os.urandom(8)
    wrapped = AESGCM(wrapping_key).encrypt(
        wrap_nonce, file_key, _wrap_aad(salt, iterations))
    return {
        'version': VERSION,
        'salt': _b64(salt),
        'iterations': iterations,
        'wrap_nonce': _b64(wrap_nonce),
        'wrapped_key': _b64(wrapped),
        'nonce_prefix': _b64(nonce_prefix),
        'chunk_size': CHUNK_SIZE,
        'written_size': 0,
        'plaintext_size': plaintext_size,
        'complete': False,
    }, file_key


def _unwrap(header, wrapping_key):
    try:
        salt = _unb64(header['salt'], 16, 'salt')
        iterations = int(header['iterations'])
        nonce = _unb64(header['wrap_nonce'], 12, 'wrap_nonce')
        wrapped = _unb64(header['wrapped_key'], 48, 'wrapped_key')
        key = AESGCM(wrapping_key).decrypt(nonce, wrapped, _wrap_aad(salt, iterations))
    except InvalidTag as error:
        raise ValueError('密码不正确，或 MP4 文件头已损坏。') from error
    if len(key) != 32 or iterations < 1:
        raise ValueError('MP4 加密头中的密钥参数无效。')
    return key


def _verify_header(header, file_key):
    expected = _unb64(header.get('header_mac'), 32, 'header_mac')
    authenticated = {key: value for key, value in header.items() if key != 'header_mac'}
    payload = json.dumps(authenticated, separators=(',', ':'), sort_keys=True).encode('ascii')
    actual = hmac.digest(file_key, payload, 'sha256')
    if not hmac.compare_digest(actual, expected):
        raise ValueError('MP4 加密文件头认证失败。')


def read_metadata(path):
    with Path(path).open('rb') as source:
        header = _read_header(source)
    return header


def verify_encrypted_file(path, wrapping_key):
    """Authenticate an encrypted container header without decrypting its payload."""
    header = read_metadata(path)
    file_key = _unwrap(header, wrapping_key)
    _verify_header(header, file_key)
    return header


def prepare_header_rewrap(path, old_wrapping_key, new_wrapping_key):
    """Prepare a password-key change for a file without touching its media data."""
    with Path(path).open('rb') as source:
        original = source.read(HEADER_SIZE)
        if len(original) != HEADER_SIZE:
            raise ValueError('加密文件头不完整。')
        source.seek(0)
        header = _read_header(source)
    file_key = _unwrap(header, old_wrapping_key)
    _verify_header(header, file_key)
    salt = _unb64(header['salt'], 16, 'salt')
    iterations = int(header['iterations'])
    nonce = os.urandom(12)
    header['wrap_nonce'] = _b64(nonce)
    header['wrapped_key'] = _b64(AESGCM(new_wrapping_key).encrypt(
        nonce, file_key, _wrap_aad(salt, iterations)))
    return original, _header_bytes(header, file_key)


def write_encrypted_header(path, header_bytes):
    """Replace only the fixed-size container header and flush it to disk."""
    if len(header_bytes) != HEADER_SIZE:
        raise ValueError('加密文件头长度无效。')
    with Path(path).open('r+b') as target:
        target.seek(0)
        target.write(header_bytes)
        target.flush()
        os.fsync(target.fileno())


def context_from_password(path, password):
    """Derive a file's wrapping key from only its embedded KDF salt and password."""
    if not isinstance(password, str) or not password:
        raise ValueError('密码不能为空。')
    header = read_metadata(path)
    salt = _unb64(header.get('salt'), 16, 'salt')
    iterations = int(header.get('iterations', 0))
    if not 1 <= iterations <= 2_000_000:
        raise ValueError('MP4 文件的密钥派生参数无效。')
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt,
                              iterations, dklen=32)
    return {'wrapping_key': key, 'salt': salt, 'iterations': iterations}


class EncryptedMP4Writer:
    """Append complete authenticated chunks; incomplete trailing input stays in RAM."""

    def __init__(self, path, wrapping_key, salt, iterations, *, plaintext_size=None, resume=False):
        self.path = Path(path)
        self.buffer = bytearray()
        self._closed = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if resume and self.path.exists() and self.path.stat().st_size:
            with self.path.open('r+b') as handle:
                self.header = _read_header(handle)
                if self.header.get('complete'):
                    raise ValueError('加密 MP4 已完成，不能继续追加。')
                if (_unb64(self.header['salt'], 16, 'salt') != salt
                        or int(self.header['iterations']) != iterations):
                    raise ValueError('当前加密密码与此 MP4 临时文件不匹配。')
                self.file_key = _unwrap(self.header, wrapping_key)
                _verify_header(self.header, self.file_key)
                if self.header.get('chunk_size') != CHUNK_SIZE:
                    raise ValueError('不支持的 MP4 分块大小。')
                self.persisted = int(self.header.get('written_size', 0))
                if self.persisted < 0 or self.persisted % CHUNK_SIZE:
                    raise ValueError('MP4 临时文件的加密分块状态无效。')
                expected_size = HEADER_SIZE + self.persisted + (self.persisted // CHUNK_SIZE) * TAG_SIZE
                if handle.seek(0, os.SEEK_END) < expected_size:
                    raise ValueError('MP4 临时文件尾部数据不完整。')
                handle.truncate(expected_size)
                if plaintext_size is not None:
                    self.header['plaintext_size'] = plaintext_size
                    self._save_header(handle)
        else:
            self.header, self.file_key = _new_header(
                wrapping_key, salt, iterations, plaintext_size=plaintext_size)
            self.persisted = 0
            with self.path.open('wb') as handle:
                handle.write(_header_bytes(self.header, self.file_key))
                handle.flush()
                os.fsync(handle.fileno())

    @property
    def plaintext_bytes(self):
        return self.persisted

    def _save_header(self, handle):
        handle.seek(0)
        handle.write(_header_bytes(self.header, self.file_key))
        handle.flush()
        os.fsync(handle.fileno())

    def append(self, data):
        if self._closed:
            raise ValueError('加密 MP4 写入器已关闭。')
        self.buffer.extend(data)
        while len(self.buffer) >= CHUNK_SIZE:
            self._write_chunk(bytes(self.buffer[:CHUNK_SIZE]), CHUNK_SIZE)
            del self.buffer[:CHUNK_SIZE]

    def _write_chunk(self, data, index_length):
        index = self.persisted // CHUNK_SIZE
        if index >= MAX_CHUNKS:
            raise ValueError('MP4 文件超过加密格式支持的最大长度。')
        prefix = _unb64(self.header['nonce_prefix'], 8, 'nonce_prefix')
        nonce = prefix + struct.pack('>I', index)
        encrypted = AESGCM(self.file_key).encrypt(nonce, data, _chunk_aad(index, index_length))
        with self.path.open('r+b') as target:
            target.seek(0, os.SEEK_END)
            target.write(encrypted)
            target.flush()
            os.fsync(target.fileno())
            self.persisted += len(data)
            self.header['written_size'] = self.persisted
            self._save_header(target)

    def finalize(self, plaintext_size=None):
        if self._closed:
            return
        if self.buffer:
            size = len(self.buffer)
            self._write_chunk(bytes(self.buffer), size)
            self.buffer.clear()
        actual = self.persisted
        if plaintext_size is not None and actual != plaintext_size:
            raise ValueError('MP4 加密数据长度与服务器声明长度不一致。')
        self.header['plaintext_size'] = actual
        self.header['written_size'] = actual
        self.header['complete'] = True
        with self.path.open('r+b') as target:
            self._save_header(target)
        self._closed = True

    def close(self):
        self.buffer.clear()
        self._closed = True


class EncryptedMP4Reader:
    def __init__(self, path, wrapping_key):
        self.path = Path(path)
        with self.path.open('rb') as source:
            self.header = _read_header(source)
        if not self.header.get('complete') or self.header.get('plaintext_size') is None:
            raise ValueError('MP4 文件尚未完成加密，不能播放。')
        if self.header.get('chunk_size') != CHUNK_SIZE:
            raise ValueError('不支持的 MP4 分块大小。')
        self.size = int(self.header['plaintext_size'])
        if self.size < 0:
            raise ValueError('MP4 加密头中的文件长度无效。')
        self.file_key = _unwrap(self.header, wrapping_key)
        _verify_header(self.header, self.file_key)
        self.nonce_prefix = _unb64(self.header['nonce_prefix'], 8, 'nonce_prefix')

    def read_range(self, start, end):
        """Read plaintext bytes in the half-open interval [start, end)."""
        if not 0 <= start <= end <= self.size:
            raise ValueError('MP4 播放请求的字节范围无效。')
        if start == end:
            return b''
        first = start // CHUNK_SIZE
        last = (end - 1) // CHUNK_SIZE
        result = bytearray()
        with self.path.open('rb') as source:
            for index in range(first, last + 1):
                plain_start = index * CHUNK_SIZE
                plain_length = min(CHUNK_SIZE, self.size - plain_start)
                disk_offset = HEADER_SIZE + index * (CHUNK_SIZE + TAG_SIZE)
                source.seek(disk_offset)
                ciphertext = source.read(plain_length + TAG_SIZE)
                if len(ciphertext) != plain_length + TAG_SIZE:
                    raise ValueError('MP4 加密分块不完整。')
                nonce = self.nonce_prefix + struct.pack('>I', index)
                try:
                    chunk = AESGCM(self.file_key).decrypt(
                        nonce, ciphertext, _chunk_aad(index, plain_length))
                except InvalidTag as error:
                    raise ValueError(f'MP4 第 {index + 1} 个加密分块校验失败。') from error
                left = max(start - plain_start, 0)
                right = min(end - plain_start, plain_length)
                result.extend(chunk[left:right])
        return bytes(result)


def encrypt_bytes(data, wrapping_key, salt, iterations):
    """Encrypt a small in-memory payload (such as an HLS AES key) in container format."""
    with tempfile.TemporaryDirectory(prefix='avd-encrypted-bytes-') as directory:
        path = Path(directory) / 'payload.enc'
        writer = EncryptedMP4Writer(path, wrapping_key, salt, iterations,
                                    plaintext_size=len(data))
        writer.append(data)
        writer.finalize(len(data))
        return path.read_bytes()


def decrypt_bytes(data, wrapping_key):
    """Decrypt a small encrypted payload without persisting its plaintext."""
    with tempfile.TemporaryDirectory(prefix='avd-encrypted-bytes-') as directory:
        path = Path(directory) / 'payload.enc'
        path.write_bytes(data)
        reader = EncryptedMP4Reader(path, wrapping_key)
        return reader.read_range(0, reader.size)


def encrypt_file(source, destination, wrapping_key, salt, iterations):
    source, destination = Path(source), Path(destination)
    size = source.stat().st_size
    writer = EncryptedMP4Writer(destination, wrapping_key, salt, iterations, plaintext_size=size)
    try:
        with source.open('rb') as handle:
            while True:
                chunk = handle.read(CHUNK_SIZE)
                if not chunk:
                    break
                writer.append(chunk)
        writer.finalize(size)
    except Exception:
        writer.close()
        raise


def encrypt_partial_file(source, destination, wrapping_key, salt, iterations):
    """Convert a plaintext .part file to an incomplete container for encrypted resume."""
    source, destination = Path(source), Path(destination)
    writer = EncryptedMP4Writer(destination, wrapping_key, salt, iterations)
    try:
        with source.open('rb') as handle:
            while True:
                chunk = handle.read(64 * 1024)
                if not chunk:
                    break
                writer.append(chunk)
        writer.close()
    except Exception:
        writer.close()
        raise


def decrypt_file(source, destination, wrapping_key, *, allow_partial=True):
    """Decrypt a completed container or its authenticated, resumable prefix."""
    source, destination = Path(source), Path(destination)
    with source.open('rb') as handle:
        header = _read_header(handle)
    file_key = _unwrap(header, wrapping_key)
    _verify_header(header, file_key)
    if header.get('complete'):
        size = int(header.get('plaintext_size'))
    elif allow_partial:
        size = int(header.get('written_size', 0))
        if size % CHUNK_SIZE:
            raise ValueError('MP4 临时文件包含未完成的分块，无法安全解密。')
    else:
        raise ValueError('MP4 文件尚未完成加密。')
    prefix = _unb64(header.get('nonce_prefix'), 8, 'nonce_prefix')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as encrypted, destination.open('wb') as plain:
        count = (size + CHUNK_SIZE - 1) // CHUNK_SIZE
        for index in range(count):
            length = min(CHUNK_SIZE, size - index * CHUNK_SIZE)
            encrypted.seek(HEADER_SIZE + index * (CHUNK_SIZE + TAG_SIZE))
            ciphertext = encrypted.read(length + TAG_SIZE)
            if len(ciphertext) != length + TAG_SIZE:
                raise ValueError('MP4 加密分块不完整。')
            try:
                data = AESGCM(file_key).decrypt(prefix + struct.pack('>I', index),
                                                ciphertext, _chunk_aad(index, length))
            except InvalidTag as error:
                raise ValueError(f'MP4 第 {index + 1} 个加密分块校验失败。') from error
            plain.write(data)
        plain.flush()
        os.fsync(plain.fileno())
