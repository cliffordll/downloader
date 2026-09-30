"""M3U8 本地媒体处理：生成合并清单、检测分片时长及后台转换 MP4。"""
from pathlib import Path
from threading import Thread, Lock
import json
import math
import os
import subprocess

from src.core.sys_setting import SysSetting
from src.media.m3u8.m3u8_parser import M3U8Parser


class FFmpegConverter:
    """统一封装清单生成、时长检测和后台合并。

    无状态处理使用静态方法；后台合并通过类级锁和输出集合防止重复启动。
    """
    @staticmethod
    def ConcatPlaylist(absSeed: str, playDir: str, playlist: str) -> bool:
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


    @staticmethod
    def ProbeDuration(path):
        """用 ffprobe 读取实际时长；由后台队列调用，不访问界面。"""
        executable = SysSetting.GetFFprobe()
        if not executable:
            raise ValueError('找不到 ffprobe，请放入 scripts 或配置的 FFmpeg 所在目录。')
        # 显式传入整数参数，避免 **dict 让类型检查器无法匹配 subprocess 的重载。
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        result = subprocess.run([executable, '-v', 'error', '-show_entries', 'format=duration',
                                 '-of', 'json', str(path)], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=15, creationflags=creationflags)
        if result.returncode:
            raise ValueError(result.stderr.strip() or 'ffprobe 检测失败')
        try:
            duration = float(json.loads(result.stdout)['format']['duration'])
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError('ffprobe 未返回有效时长') from error
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('ffprobe 返回的时长无效')
        return duration

    _lock = Lock()
    _outputs = set()

    @classmethod
    def IsBusy(cls):
        with cls._lock:
            return bool(cls._outputs)

    @classmethod
    def IsConverting(cls, filename):
        with cls._lock:
            return os.path.abspath(filename) in cls._outputs

    @classmethod
    def _ConvertTSFile(cls, playlist, outputFile='output.mp4', callback=None, item=None):
        success = False
        # 正式文件只在 FFmpeg 成功退出后出现，重启时不会把半成品判为已完成。
        temporary = str(outputFile) + '.part.mp4'
        try:
            if os.path.exists(outputFile):
                raise FileExistsError('输出文件已存在。')
            cmd = [SysSetting.GetFFmpeg(), '-nostdin', '-y', '-f', 'concat', '-safe', '0',
                   '-i', playlist, '-c', 'copy', '-progress', 'pipe:1', temporary]
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                       errors='replace', creationflags=creationflags)
            # Popen.stdout 的类型允许 None；这里已指定 PIPE，明确这一前提后再读取。
            assert process.stdout is not None, '设置 stdout=PIPE 后应有输出流'
            for line in process.stdout:
                if line.startswith('out_time_ms='):
                    print(line.strip())
            if process.wait() == 0 and os.path.isfile(temporary):
                if os.path.exists(outputFile):
                    raise FileExistsError('输出文件已存在。')
                os.replace(temporary, outputFile)
                success = True
        except (OSError, subprocess.SubprocessError) as error:
            print(f'转换失败：{error}')
        finally:
            if os.path.isfile(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            if callback:
                # 后台回调只保存业务结果；界面通过变化队列读取，不参与落库。
                cls._Deliver(success, outputFile, callback, item)
            else:
                with cls._lock:
                    cls._outputs.discard(os.path.abspath(outputFile))

    @classmethod
    def _Deliver(cls, success, outputFile, callback, item):
        try:
            callback(success, outputFile, item)
        finally:
            with cls._lock:
                cls._outputs.discard(os.path.abspath(outputFile))

    @classmethod
    def ConvertTSFile(cls, playlist, outputFile, callback=None, item=None):
        key = os.path.abspath(outputFile)
        with cls._lock:
            if key in cls._outputs:
                return
            cls._outputs.add(key)
        try:
            Thread(target=cls._ConvertTSFile, args=(playlist, outputFile, callback, item),
                   name='mp4-converter').start()
        except Exception:
            with cls._lock:
                cls._outputs.discard(key)
            raise
