"""应用数据目录与下载目录分离，不依赖程序启动位置。"""

from pathlib import Path


def data_dir() -> Path:
    """只返回路径；由设置/数据库的写入操作创建目录，不迁移旧版数据。"""
    return Path.home() / '.avdownloader'


def database_path() -> Path:
    return data_dir() / 'downloads.db'
