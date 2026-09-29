from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import os
from pathlib import Path
from queue import Queue, Empty
import threading
import time

import requests
import wx

from src.managers.sys_setting import SysSetting


class Downloader:
    isStop = True
    threadQueue = Queue()
    threadLock = threading.Lock()
    _pending = set()
    _master = None
    _shutdown = threading.Event()
    _rate_lock = threading.Lock()
    _next_request = 0.0
    _paused_until = 0.0

    @classmethod
    def IsBusy(cls):
        with cls.threadLock:
            return bool(cls._pending)

    @classmethod
    def _WaitForRequest(cls):
        while not cls._shutdown.is_set():
            with cls._rate_lock:
                now = time.monotonic()
                delay = max(cls._next_request, cls._paused_until) - now
                if delay <= 0:
                    cls._next_request = now + SysSetting.GetAll()['request_interval']
                    return True
            cls._shutdown.wait(min(delay, 0.2))
        return False

    @classmethod
    def _RetryAfter(cls, value, fallback):
        try:
            return max(0, float(value))
        except (TypeError, ValueError):
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                return max(0, (date - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return fallback

    @classmethod
    def DownloadContent(cls, baseUrl):
        retries = SysSetting.GetAll()['max_retries']
        error = '下载已停止'
        for attempt in range(retries + 1):
            if not cls._WaitForRequest():
                return False, '下载已停止'
            backoff = min(2 ** attempt, 60)
            try:
                response = requests.get(baseUrl, timeout=SysSetting.GetTimeout())
                try:
                    status = response.status_code
                    if status == 200:
                        return True, response.content
                    if status == 403:
                        return False, 'HTTP 403：服务器拒绝访问，请检查权限或链接有效期；未自动重试。'
                    error = f'HTTP {status}'
                    if status == 429:
                        delay = cls._RetryAfter(response.headers.get('Retry-After'), backoff)
                        with cls._rate_lock:
                            cls._paused_until = max(cls._paused_until, time.monotonic() + delay)
                    elif status < 500:
                        return False, error
                finally:
                    response.close()
            except (requests.exceptions.SSLError, requests.exceptions.TooManyRedirects) as exc:
                return False, str(exc)
            except requests.RequestException as exc:
                error = str(exc)
            if attempt < retries and cls._shutdown.wait(backoff):
                return False, '下载已停止'
        return False, error

    @classmethod
    def _DownLoadFile(cls, baseUrl, fileName):
        partial = str(fileName) + '.part'
        try:
            if Path(fileName).is_file():
                return True, fileName
            success, content = cls.DownloadContent(baseUrl)
            if not success:
                print(f'下载失败：{fileName}：{content}')
                return False, fileName
            Path(fileName).parent.mkdir(parents=True, exist_ok=True)
            with open(partial, 'wb') as output:
                output.write(content)
            os.replace(partial, fileName)
            return True, fileName
        except OSError as exc:
            print(f'保存失败：{fileName}：{exc}')
            return False, fileName
        finally:
            if os.path.isfile(partial):
                try:
                    os.unlink(partial)
                except OSError:
                    pass

    @classmethod
    def DownloadTSFile(cls, absUri, absFile, callback, item):
        key = os.path.normcase(os.path.abspath(absFile))
        with cls.threadLock:
            if cls._shutdown.is_set() or key in cls._pending:
                return
            cls._pending.add(key)
            cls.threadQueue.put((absUri, absFile, callback, item, key))
            if cls.isStop:
                cls.isStop = False
                cls._master = threading.Thread(target=cls._MasterThreadRun, name='download-scheduler')
                cls._master.start()

    @classmethod
    def _Deliver(cls, task, success):
        _, filename, callback, item, key = task
        try:
            owner = getattr(callback, '__self__', None)
            if not cls._shutdown.is_set() and not (isinstance(owner, wx.Window) and not owner):
                callback(success, filename, item)
        finally:
            with cls.threadLock:
                cls._pending.discard(key)

    @classmethod
    def _MasterThreadRun(cls):
        active = {}
        # The dispatcher limits active jobs; the fixed ceiling allows live limit changes.
        with ThreadPoolExecutor(max_workers=16) as pool:
            while True:
                with cls.threadLock:
                    if not cls._shutdown.is_set():
                        while len(active) < SysSetting.GetMaxWorkers():
                            try:
                                task = cls.threadQueue.get_nowait()
                            except Empty:
                                break
                            active[pool.submit(cls._DownLoadFile, task[0], task[1])] = task
                    if not active and (cls.threadQueue.empty() or cls._shutdown.is_set()):
                        cls.isStop = True
                        break
                if active:
                    done, _ = wait(active, timeout=0.1, return_when=FIRST_COMPLETED)
                    for future in done:
                        task = active.pop(future)
                        try:
                            success, _ = future.result()
                        except Exception as exc:
                            print(f'下载任务异常：{exc}')
                            success = False
                        if cls._shutdown.is_set():
                            with cls.threadLock:
                                cls._pending.discard(task[4])
                        else:
                            wx.CallAfter(cls._Deliver, task, success)

    @classmethod
    def Shutdown(cls):
        cls._shutdown.set()
        with cls.threadLock:
            while not cls.threadQueue.empty():
                task = cls.threadQueue.get_nowait()
                cls._pending.discard(task[4])
