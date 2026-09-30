import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import threading

from src.config.app_paths import data_dir


class SysSetting:
    _lock = threading.RLock()
    _values = None
    _load_error = ''

    @classmethod
    def ConfigPath(cls):
        return data_dir() / 'settings.json'

    @classmethod
    def Defaults(cls):
        return {
            'download_dir': str(Path(__file__).resolve().parents[2] / 'downloads'),
            'max_workers': 3,
            'request_interval': 0.5,
            'max_retries': 2,
            'connect_timeout': 10,
            'read_timeout': 30,
            'ffmpeg_path': '',
            'auto_merge': False,
            'default_expand_tasks': False,
        }

    @classmethod
    def Validate(cls, values):
        result = cls.Defaults()
        result.update({k: v for k, v in values.items() if k in result})
        for key, label, lower, upper, integral in (
            ('max_workers', '最大并发数', 1, 16, True),
            ('request_interval', '请求启动间隔', 0, 60, False),
            ('max_retries', '最大重试次数', 0, 10, True),
            ('connect_timeout', '连接超时', 1, 300, False),
            ('read_timeout', '读取超时', 1, 600, False),
        ):
            value = result[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{label}必须是有效数字。')
            if not lower <= value <= upper or (integral and value != int(value)):
                raise ValueError(f'{label}必须在 {lower}～{upper} 之间' + ('，且为整数。' if integral else '。'))
            result[key] = int(value) if integral else float(value)
        directory = result['download_dir']
        if not isinstance(directory, str) or not directory.strip():
            raise ValueError('请选择下载目录。')
        directory = Path(directory).expanduser()
        if not directory.is_absolute() or (directory.exists() and not directory.is_dir()):
            raise ValueError('下载目录必须是绝对路径，且不能是文件。')
        result['download_dir'] = str(directory.resolve())
        ffmpeg = result['ffmpeg_path']
        if not isinstance(ffmpeg, str):
            raise ValueError('FFmpeg 路径无效。')
        ffmpeg = ffmpeg.strip()
        if ffmpeg and (not Path(ffmpeg).is_absolute() or not Path(ffmpeg).is_file()):
            raise ValueError('请选择已有的 FFmpeg 可执行文件，或留空使用自动检测。')
        result['ffmpeg_path'] = ffmpeg
        if not isinstance(result['auto_merge'], bool):
            raise ValueError('自动合并设置必须为开启或关闭。')
        if not isinstance(result['default_expand_tasks'], bool):
            raise ValueError('默认展开任务设置必须为开启或关闭。')
        return result

    @classmethod
    def GetAll(cls):
        with cls._lock:
            if cls._values is None:
                cls._load_error = ''
                try:
                    data = json.loads(cls.ConfigPath().read_text(encoding='utf-8'))
                    if not isinstance(data, dict):
                        raise ValueError('设置文件内容无效')
                    cls._values = cls.Validate(data)
                except FileNotFoundError:
                    cls._values = cls.Defaults()
                except (OSError, ValueError, TypeError) as error:
                    cls._load_error = f'读取设置失败，当前使用默认值：{error}'
                    cls._values = cls.Defaults()
            return dict(cls._values)

    @classmethod
    def Save(cls, values):
        validated = cls.Validate(values)
        with cls._lock:
            target = cls.ConfigPath()
            target.parent.mkdir(parents=True, exist_ok=True)
            Path(validated['download_dir']).mkdir(parents=True, exist_ok=True)
            name = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                                 prefix='settings-', suffix='.tmp', delete=False) as output:
                    name = output.name
                    json.dump(validated, output, ensure_ascii=False, indent=2)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(name, target)
            finally:
                if name and os.path.exists(name):
                    os.unlink(name)
            cls._values = validated
            cls._load_error = ''

    @classmethod
    def GetWorkPath(cls):
        # 新建任务使用的默认根目录；已有任务必须使用其 save_dir，不能重新拼接此路径。
        # 保留尾部分隔符，供现有添加任务表单显示。
        return os.path.join(cls.GetAll()['download_dir'], '')

    @classmethod
    def GetTimeout(cls):
        values = cls.GetAll()
        return values['connect_timeout'], values['read_timeout']

    @classmethod
    def GetMaxWorkers(cls):
        return cls.GetAll()['max_workers']

    @classmethod
    def GetFFmpeg(cls):
        configured = cls.GetAll()['ffmpeg_path']
        if configured:
            return configured
        executable = 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'
        bundled = Path(__file__).resolve().parents[2] / 'scripts' / executable
        if bundled.is_file():
            return str(bundled)
        return shutil.which('ffmpeg') or 'ffmpeg'
