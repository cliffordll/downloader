"""读取本地媒体时长；由后台工作队列调用，不依赖 wx。"""
import json
import math
import os
import subprocess

from src.config.sys_setting import SysSetting


def probe_duration(path):
    executable = SysSetting.GetFFprobe()
    if not executable:
        raise ValueError('找不到 ffprobe，请放入 scripts 或配置的 FFmpeg 所在目录。')
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    result = subprocess.run([executable, '-v', 'error', '-show_entries', 'format=duration',
                             '-of', 'json', str(path)], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=15, **options)
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'ffprobe 检测失败')
    try:
        duration = float(json.loads(result.stdout)['format']['duration'])
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError('ffprobe 未返回有效时长') from error
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('ffprobe 返回的时长无效')
    return duration
