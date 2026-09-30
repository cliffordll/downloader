"""为 FFmpeg 生成合并清单，不扫描目录或发现任务。"""
from pathlib import Path

from src.core.parsers.m3u8_parser import M3U8Parser


def create_concat_playlist(absSeed: str, playDir: str, playlist: str) -> bool:
    """读取任务派生的本地 M3U8，按播放顺序写出分片绝对路径。"""
    try:
        content = Path(absSeed).read_text(encoding='utf-8-sig')
        if not content.strip() or content.strip().splitlines()[0] != '#EXTM3U':
            return False
        segments = M3U8Parser(content=content, base_path='', m3u8_uri='').parse_media()
        if not segments:
            return False
        lines = []
        for segment in segments:
            path = str((Path(playDir) / segment.name).resolve())
            # concat 清单使用单引号，路径中的单引号需要在引用之外转义。
            escaped = path.replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
        Path(playlist).write_text('\n'.join(lines), encoding='utf-8')
        return True
    except (OSError, ValueError):
        return False
