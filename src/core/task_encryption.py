"""Password management and file operations for encrypted tasks."""

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import shutil
import sys

from src.core.task_files import output_path, playlist_path, segment_path
from src.media.encrypted_file import (
    decrypt_file, encrypt_file, encrypt_partial_file, is_encrypted_mp4, read_metadata,
    prepare_header_rewrap, verify_encrypted_file, write_encrypted_header,
)
from src.schemas.task import M3U8Task, MP4Task

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

    def set_password(self, password, *, current_password=None, encrypted_files=()):
        if not isinstance(password, str) or not password:
            raise ValueError('密码不能为空。')
        existing = self._read()
        old_context = None
        if existing is not None:
            old_context = self.unlock(current_password) if current_password is not None else None
            if old_context is None:
                raise ValueError('当前密码不正确。')

        # Keep the KDF salt stable for an existing password vault: MP4 file
        # headers contain it so the derived wrapping key is reproducible.
        salt = (base64.b64decode(existing['salt'], validate=True)
                if existing is not None else os.urandom(16))
        derived = self._derive(password, salt)
        verifier = hmac.digest(derived, self.VERIFIER_CONTEXT, 'sha256')
        data = {
            'format_version': self.FORMAT_VERSION,
            'iterations': self.ITERATIONS,
            'salt': base64.b64encode(salt).decode('ascii'),
            'verifier': base64.b64encode(verifier).decode('ascii'),
        }
        protected_password = _protect_local_secret(password.encode('utf-8'))
        if protected_password is not None:
            data['local_password'] = base64.b64encode(protected_password).decode('ascii')
        header_updates = []
        if old_context is not None and password != current_password:
            seen = set()
            for path in encrypted_files:
                path = Path(path)
                key = os.path.normcase(str(path.resolve()))
                if key in seen:
                    continue
                seen.add(key)
                original, updated = prepare_header_rewrap(
                    path, old_context['wrapping_key'], derived)
                header_updates.append((path, original, updated))
        try:
            for path, _original, updated in header_updates:
                write_encrypted_header(path, updated)
            self._write(data)
        except Exception as error:
            rollback_errors = []
            for path, original, _updated in reversed(header_updates):
                try:
                    write_encrypted_header(path, original)
                except OSError as rollback_error:
                    rollback_errors.append(f'{path}: {rollback_error}')
            if rollback_errors:
                raise OSError('密码修改失败，部分文件头回滚失败：' + '；'.join(rollback_errors)) from error
            raise
        if sys.platform == 'darwin':
            try:
                _keychain_store_password(password.encode('utf-8'))
                return True
            except OSError:
                # Password setup succeeded; manual unlock remains available if
                # the login keychain is locked or this app cannot access it.
                return False
        return protected_password is not None

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

    def unlock(self, password):
        """Return the in-memory wrapping context after checking the shared password."""
        record = self._read()
        if record is None or not isinstance(password, str):
            return None
        try:
            salt = base64.b64decode(record['salt'], validate=True)
            expected = base64.b64decode(record['verifier'], validate=True)
            iterations = record['iterations']
            derived = self._derive(password, salt, iterations)
        except (KeyError, TypeError, ValueError):
            return None
        actual = hmac.digest(derived, self.VERIFIER_CONTEXT, 'sha256')
        if not hmac.compare_digest(actual, expected):
            return None
        return {'wrapping_key': derived, 'salt': salt, 'iterations': iterations}

    def unlock_local(self):
        """Use the current OS user's protected local credential, if available."""
        if sys.platform == 'darwin':
            try:
                password = _keychain_read_password()
                return self.unlock(password.decode('utf-8')) if password is not None else None
            except (OSError, UnicodeDecodeError, ValueError):
                return None
        record = self._read()
        if record is None or not record.get('local_password'):
            return None
        try:
            protected = base64.b64decode(record['local_password'], validate=True)
            password = _unprotect_local_secret(protected).decode('utf-8')
        except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError):
            return None
        return self.unlock(password)

    def remember_password(self, password):
        """Protect a verified password for convenient use by this OS account."""
        if not self.verify(password):
            raise ValueError('当前密码不正确。')
        if sys.platform == 'darwin':
            _keychain_store_password(password.encode('utf-8'))
            return True
        protected = _protect_local_secret(password.encode('utf-8'))
        if protected is None:
            return False
        record = self._read()
        record['local_password'] = base64.b64encode(protected).decode('ascii')
        self._write(record)
        return True

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(data, dict)
                    or data.get('format_version') != self.FORMAT_VERSION
                    or data.get('iterations') != self.ITERATIONS
                    or not isinstance(data.get('salt'), str)
                    or not isinstance(data.get('verifier'), str)):
                raise ValueError('加密密码配置格式无效。')
            if ('local_password' in data and not isinstance(data['local_password'], str)):
                raise ValueError('本地加密密码格式无效。')
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

def _protect_local_secret(secret):
    """Protect a local secret in the Windows account profile when available."""
    if sys.platform != 'win32':
        return None
    return _dpapi(secret, protect=True)


def _unprotect_local_secret(secret):
    if sys.platform != 'win32':
        raise OSError('当前系统不支持本地凭据自动解锁。')
    return _dpapi(secret, protect=False)


def _keychain_store_password(password):
    """Store the shared task password as a generic item in the macOS login keychain."""
    if sys.platform != 'darwin':
        raise OSError('macOS 钥匙串仅在 macOS 上可用。')
    import ctypes

    security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    core_foundation = ctypes.CDLL(
        '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    owned = []

    def constant(library, name):
        return ctypes.c_void_p.in_dll(library, name).value

    def cf_string(value):
        encoded = value.encode('utf-8')
        buffer = ctypes.create_string_buffer(encoded)
        result = core_foundation.CFStringCreateWithCString(
            None, buffer, 0x08000100)  # kCFStringEncodingUTF8
        if not result:
            raise OSError('无法创建 macOS 钥匙串查询。')
        owned.append(result)
        return result

    def dictionary(items):
        keys = (ctypes.c_void_p * len(items))(*[key for key, _value in items])
        values = (ctypes.c_void_p * len(items))(*[value for _key, value in items])
        result = core_foundation.CFDictionaryCreate(None, keys, values, len(items), None, None)
        if not result:
            raise OSError('无法创建 macOS 钥匙串查询。')
        owned.append(result)
        return result

    core_foundation.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                                         ctypes.c_uint32]
    core_foundation.CFStringCreateWithCString.restype = ctypes.c_void_p
    core_foundation.CFDictionaryCreate.argtypes = [ctypes.c_void_p,
                                                   ctypes.POINTER(ctypes.c_void_p),
                                                   ctypes.POINTER(ctypes.c_void_p),
                                                   ctypes.c_long, ctypes.c_void_p,
                                                   ctypes.c_void_p]
    core_foundation.CFDictionaryCreate.restype = ctypes.c_void_p
    core_foundation.CFDataCreate.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
    core_foundation.CFDataCreate.restype = ctypes.c_void_p
    core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
    core_foundation.CFRelease.restype = None
    security.SecItemAdd.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    security.SecItemAdd.restype = ctypes.c_int32
    security.SecItemUpdate.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    security.SecItemUpdate.restype = ctypes.c_int32

    try:
        service = cf_string('com.avdownloader.encrypted-task-password.v1')
        account = cf_string('shared-task-password')
        class_key = constant(security, 'kSecClass')
        service_key = constant(security, 'kSecAttrService')
        account_key = constant(security, 'kSecAttrAccount')
        base = dictionary([
            (class_key, constant(security, 'kSecClassGenericPassword')),
            (service_key, service),
            (account_key, account),
        ])
        data_buffer = ctypes.create_string_buffer(password)
        secret = core_foundation.CFDataCreate(None, data_buffer, len(password))
        if not secret:
            raise OSError('无法准备钥匙串凭据。')
        owned.append(secret)
        query = dictionary([
            (class_key, constant(security, 'kSecClassGenericPassword')),
            (service_key, service),
            (account_key, account),
            (constant(security, 'kSecAttrAccessible'),
             constant(security, 'kSecAttrAccessibleWhenUnlockedThisDeviceOnly')),
            (constant(security, 'kSecValueData'), secret),
        ])
        status = security.SecItemAdd(query, None)
        if status == -25299:  # errSecDuplicateItem: update the existing password.
            attributes = dictionary([(constant(security, 'kSecValueData'), secret)])
            status = security.SecItemUpdate(base, attributes)
        if status != 0:
            raise OSError(f'保存 macOS 钥匙串凭据失败（错误码 {status}）。')
    finally:
        for reference in reversed(owned):
            core_foundation.CFRelease(reference)


def _keychain_read_password():
    """Read the shared task password from the current user's login keychain."""
    if sys.platform != 'darwin':
        raise OSError('macOS 钥匙串仅在 macOS 上可用。')
    import ctypes

    security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    core_foundation = ctypes.CDLL(
        '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    owned = []

    def constant(library, name):
        return ctypes.c_void_p.in_dll(library, name).value

    core_foundation.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                                         ctypes.c_uint32]
    core_foundation.CFStringCreateWithCString.restype = ctypes.c_void_p
    core_foundation.CFDictionaryCreate.argtypes = [ctypes.c_void_p,
                                                   ctypes.POINTER(ctypes.c_void_p),
                                                   ctypes.POINTER(ctypes.c_void_p),
                                                   ctypes.c_long, ctypes.c_void_p,
                                                   ctypes.c_void_p]
    core_foundation.CFDictionaryCreate.restype = ctypes.c_void_p
    core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
    core_foundation.CFRelease.restype = None
    core_foundation.CFDataGetLength.argtypes = [ctypes.c_void_p]
    core_foundation.CFDataGetLength.restype = ctypes.c_long
    core_foundation.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
    core_foundation.CFDataGetBytePtr.restype = ctypes.c_void_p
    security.SecItemCopyMatching.argtypes = [ctypes.c_void_p,
                                             ctypes.POINTER(ctypes.c_void_p)]
    security.SecItemCopyMatching.restype = ctypes.c_int32

    def cf_string(value):
        buffer = ctypes.create_string_buffer(value.encode('utf-8'))
        result = core_foundation.CFStringCreateWithCString(None, buffer, 0x08000100)
        if not result:
            raise OSError('无法创建 macOS 钥匙串查询。')
        owned.append(result)
        return result

    def dictionary(items):
        keys = (ctypes.c_void_p * len(items))(*[key for key, _value in items])
        values = (ctypes.c_void_p * len(items))(*[value for _key, value in items])
        result = core_foundation.CFDictionaryCreate(None, keys, values, len(items), None, None)
        if not result:
            raise OSError('无法创建 macOS 钥匙串查询。')
        owned.append(result)
        return result

    result = ctypes.c_void_p()
    try:
        query = dictionary([
            (constant(security, 'kSecClass'), constant(security, 'kSecClassGenericPassword')),
            (constant(security, 'kSecAttrService'),
             cf_string('com.avdownloader.encrypted-task-password.v1')),
            (constant(security, 'kSecAttrAccount'), cf_string('shared-task-password')),
            (constant(security, 'kSecReturnData'), constant(core_foundation, 'kCFBooleanTrue')),
            (constant(security, 'kSecMatchLimit'), constant(security, 'kSecMatchLimitOne')),
        ])
        status = security.SecItemCopyMatching(query, ctypes.byref(result))
        if status == -25300:  # errSecItemNotFound
            return None
        if status != 0 or not result.value:
            raise OSError(f'读取 macOS 钥匙串凭据失败（错误码 {status}）。')
        length = core_foundation.CFDataGetLength(result)
        pointer = core_foundation.CFDataGetBytePtr(result)
        return ctypes.string_at(pointer, length)
    finally:
        if result.value:
            core_foundation.CFRelease(result)
        for reference in reversed(owned):
            core_foundation.CFRelease(reference)


def _dpapi(value, *, protect):
    """Call CryptProtectData/CryptUnprotectData without a pywin32 dependency."""
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.c_void_p)]

    source_buffer = ctypes.create_string_buffer(value)
    source = DataBlob(len(value), ctypes.cast(source_buffer, ctypes.c_void_p))
    result = DataBlob()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if protect:
        function = crypt32.CryptProtectData
        function.argtypes = [ctypes.POINTER(DataBlob), wintypes.LPCWSTR,
                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                             wintypes.DWORD, ctypes.POINTER(DataBlob)]
        ok = function(ctypes.byref(source), 'AVDownloader task password', None,
                      None, None, 0x1, ctypes.byref(result))
        description = None
    else:
        function = crypt32.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(DataBlob), ctypes.POINTER(wintypes.LPWSTR),
                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                             wintypes.DWORD, ctypes.POINTER(DataBlob)]
        description = wintypes.LPWSTR()
        ok = function(ctypes.byref(source), ctypes.byref(description), None,
                      None, None, 0x1, ctypes.byref(result))
    if not ok:
        error = ctypes.WinError(ctypes.get_last_error())
        raise OSError(f'本地凭据保护失败：{error}')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(result.data)
        if description:
            kernel32.LocalFree(ctypes.cast(description, ctypes.c_void_p))


def encrypted_artifact_name(name: str, encrypted: bool, *, partial: bool = False) -> str:
    """Return a task artifact name with its encrypted suffix added or removed."""
    lower = name.lower()
    if encrypted:
        if partial and lower.endswith('.part'):
            return name[:-5] + '.enc.part'
        return name if lower.endswith('.enc') else name + '.enc'
    if partial and lower.endswith('.enc.part'):
        return name[:-9] + '.part'
    return name[:-4] if lower.endswith('.enc') else name


def plan_task_artifacts(task, encrypted: bool):
    """Return (source, destination, complete) entries for task-owned media."""
    planned = []
    if isinstance(task, MP4Task):
        for output in task.outputs:
            source = output_path(task, output)
            destination_name = encrypted_artifact_name(source.name, encrypted)
            planned.append((source, source.with_name(destination_name), True))
        source = task.save_dir / task.details.temporary_path
        planned.append((source, source.with_name(encrypted_artifact_name(
            source.name, encrypted, partial=True)), False))
    elif isinstance(task, M3U8Task):
        for segment in task.details.segments:
            source = segment_path(task, segment)
            planned.append((source, source.with_name(encrypted_artifact_name(source.name, encrypted)), True))
        for output in task.outputs:
            source = output_path(task, output)
            planned.append((source, source.with_name(encrypted_artifact_name(source.name, encrypted)), True))
        if task.details.playlist_path:
            source = playlist_path(task)
            planned.append((source, source.with_name(encrypted_artifact_name(source.name, encrypted)), True))
    else:
        raise ValueError('此任务类型不支持文件加密。')
    return planned


def prepare_task_artifacts(media_paths, encrypted, context):
    """Convert and verify all present artifacts while retaining every source."""
    prepared = []
    created = []
    try:
        for source, destination, complete in media_paths:
            if not source.exists():
                continue
            if source.is_symlink() or not source.is_file():
                raise ValueError(f'无法安全处理任务文件：{source}')
            working = (destination if destination != source else
                       source.with_name(source.name + '.tmp'))
            if working.exists() or working.is_symlink():
                raise FileExistsError(f'临时或目标文件已存在，未修改原文件：{working}')
            created.append(working)

            already_encrypted = is_encrypted_mp4(source)
            if already_encrypted:
                verify_encrypted_file(source, context['wrapping_key'])
            if encrypted and not already_encrypted:
                convert = encrypt_file if complete else encrypt_partial_file
                convert(source, working, context['wrapping_key'],
                        context['salt'], context['iterations'])
            elif not encrypted and already_encrypted:
                decrypt_file(source, working, context['wrapping_key'],
                             allow_partial=not complete)
            else:
                shutil.copyfile(source, working)

            if encrypted:
                if not is_encrypted_mp4(working):
                    raise OSError(f'加密文件校验失败：{working.name}')
                header = read_metadata(working)
                if bool(header.get('complete')) != complete:
                    raise OSError(f'加密文件状态校验失败：{working.name}')
            elif is_encrypted_mp4(working):
                raise OSError(f'解密文件校验失败：{working.name}')
            prepared.append((source, destination, working, complete))
        return prepared
    except Exception:
        for path in created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def publish_task_artifacts(prepared):
    """Publish converted artifacts and remove the original source files."""
    errors = []
    for source, destination, working, _complete in prepared:
        try:
            if working != destination:
                os.replace(working, destination)
            if source != destination:
                source.unlink(missing_ok=True)
        except OSError as error:
            errors.append(error)
    return errors


