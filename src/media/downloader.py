"""下载器共用的并发额度与请求节奏；不管理任务队列、界面或文件写入。"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import threading
import time

from src.core.sys_setting import SysSetting


class Downloader:
    """供 M3U8 与 MP4 组合使用，两个具体下载器不继承也不互相依赖。"""
    _rate_lock = threading.Lock()  # 保护请求间隔和服务器限流截止时间。
    _next_request = 0.0  # 下一次请求允许启动的 monotonic 时间。
    _paused_until = 0.0  # HTTP 429 要求停止发起请求的截止时间，不是用户暂停。
    _transfer_lock = threading.Lock()
    _transfers = 0  # TS 与 MP4 共用并发额度，混合下载也不超过设置上限。

    @classmethod
    def AcquireTransfer(cls, check):
        """等待全局传输额度；check 负责取消/暂停，等待期间不持锁。"""
        while True:
            check()
            with cls._transfer_lock:
                if cls._transfers < SysSetting.GetMaxWorkers():
                    cls._transfers += 1
                    return
            time.sleep(0.05)

    @classmethod
    def ReleaseTransfer(cls):
        with cls._transfer_lock:
            cls._transfers -= 1

    @classmethod
    def RequestDelay(cls):
        """原子申请请求启动时机；返回 0 表示已获准，否则返回还需等待的秒数。

        调用方负责可中断等待以及自身的暂停/退出判断，公共锁不跨等待持有。
        """
        with cls._rate_lock:
            now = time.monotonic()
            delay = max(cls._next_request, cls._paused_until) - now
            if delay <= 0:
                cls._next_request = now + SysSetting.GetAll()['request_interval']
                return 0.0
            return delay

    @classmethod
    def DeferRequests(cls, delay):
        """服务器限流影响两个下载器的后续请求，较短的新延迟不会提前解除限流。"""
        with cls._rate_lock:
            cls._paused_until = max(cls._paused_until, time.monotonic() + delay)

    @classmethod
    def RetryAfter(cls, value, fallback):
        """解析 Retry-After：支持等待秒数和 HTTP 日期，非法值使用退避时间。"""
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

