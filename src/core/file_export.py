"""复制已完成文件，成功后再替换目标，避免失败时留下不完整文件。"""
import os
from pathlib import Path
import shutil
import tempfile


def save_file_as(source, destination):
    source, destination = Path(source), Path(destination)
    if not source.is_file():
        raise FileNotFoundError('原文件已不存在，请刷新列表。')
    if source.resolve() == destination.resolve() or (
            destination.exists() and source.samefile(destination)):
        raise ValueError('另存位置与原文件相同，请选择其他位置。')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.avdownloader-',
                                         suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
