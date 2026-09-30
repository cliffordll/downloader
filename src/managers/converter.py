from threading import Thread, Lock
import os
import subprocess
import wx

from src.managers.sys_setting import SysSetting


class Converter:
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
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                       errors='replace', **options)
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
                wx.CallAfter(cls._Deliver, success, outputFile, callback, item)
            else:
                with cls._lock:
                    cls._outputs.discard(os.path.abspath(outputFile))

    @classmethod
    def _Deliver(cls, success, outputFile, callback, item):
        try:
            owner = getattr(callback, '__self__', None)
            if not (isinstance(owner, wx.Window) and not owner):
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
