"""下载器共用的调度入口与请求节奏；不涉及界面或文件写入。"""
from collections import OrderedDict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import threading
import time

from src.core.sys_setting import SysSetting


@dataclass
class _Request:
    future: Future
    function: object
    args: tuple
    kwargs: dict
    ready: object
    cancelled: object
    limit: int | None


class Downloader:
    """统一管理任务排队、每任务并发和请求限流，供 M3U8/TS 与 MP4 共用。"""
    _condition = threading.Condition()
    _queues = OrderedDict()  # 任务入队顺序；每项是自己的请求队列。
    _running = {}  # 任务 → 正在执行的请求数。
    _admitted = set()  # 已占用下载名额的任务。
    _paused = set()  # 已让出名额的暂停任务，恢复后移到队尾。
    _thread = None
    _rate_lock = threading.Lock()  # 请求间隔与服务器限流独立加锁，不阻塞队列操作。
    _next_request = 0.0
    _paused_until = 0.0  # HTTP 429 的限流截止时间，不是用户暂停。

    @classmethod
    def Submit(cls, task_id, function, *args, ready=lambda: True,
               cancelled=lambda: False, limit=None, **kwargs):
        """按任务身份提交下载请求，返回 Future；等待名额时不占工作线程。"""
        future = Future()
        request = _Request(future, function, args, kwargs, ready, cancelled, limit)
        with cls._condition:
            cls._queues.setdefault(task_id, deque()).append(request)
            if cls._thread is None:
                cls._thread = threading.Thread(target=cls._Run, name='task-scheduler', daemon=True)
                cls._thread.start()
            cls._condition.notify_all()
        return future

    @classmethod
    def _Finished(cls, task_id, request, result):
        with cls._condition:
            cls._running[task_id] -= 1
            cls._condition.notify_all()
        # 完成通知可能落库或重新入队，不能持有调度锁调用。
        try:
            value = result.result()
        except BaseException as error:
            request.future.set_exception(error)
        else:
            request.future.set_result(value)

    @classmethod
    def _Run(cls):
        # 设置上限为 16 个任务 × 每任务 16 个分片；线程按实际请求懒创建。
        with ThreadPoolExecutor(max_workers=256, thread_name_prefix='media-download') as pool:
            while True:
                dispatch, cancelled = [], []
                with cls._condition:
                    settings = SysSetting.GetAll()
                    for task_id, queue in list(cls._queues.items()):
                        remaining = deque()
                        for request in queue:
                            if request.future.cancelled() or request.cancelled():
                                cancelled.append(request.future)
                            else:
                                remaining.append(request)
                        cls._queues[task_id] = remaining
                        if not cls._running.get(task_id, 0):
                            if not remaining:
                                del cls._queues[task_id]
                                cls._running.pop(task_id, None)
                                cls._admitted.discard(task_id)
                                cls._paused.discard(task_id)
                            elif not any(r.ready() for r in remaining):
                                # 暂停任务等在途请求结束后让出名额，恢复时排在队尾。
                                cls._admitted.discard(task_id)
                                cls._paused.add(task_id)
                            elif task_id in cls._paused:
                                cls._paused.remove(task_id)
                                cls._queues.move_to_end(task_id)
                    for task_id, queue in cls._queues.items():
                        if not any(r.ready() for r in queue):
                            continue
                        if task_id not in cls._admitted:
                            if len(cls._admitted) >= settings['max_tasks']:
                                continue
                            cls._admitted.add(task_id)
                        # 降低额度不取消已有请求，等活动数量降到新上限再补充。
                        for _ in range(len(queue)):
                            request = queue.popleft()
                            limit = request.limit or settings['max_workers']
                            if cls._running.get(task_id, 0) < limit and request.ready():
                                if request.future.set_running_or_notify_cancel():
                                    cls._running[task_id] = cls._running.get(task_id, 0) + 1
                                    dispatch.append((task_id, request))
                            else:
                                queue.append(request)
                    idle = not cls._queues
                    if idle:
                        cls._thread = None
                for future in cancelled:
                    future.cancel()
                    future.set_running_or_notify_cancel()  # 唤醒 wait()/as_completed() 的等待者。
                if idle:
                    return
                for task_id, request in dispatch:
                    try:
                        result = pool.submit(request.function, *request.args, **request.kwargs)
                    except BaseException as error:
                        result = Future()
                        result.set_exception(error)
                    result.add_done_callback(lambda result, task_id=task_id, request=request:
                                             cls._Finished(task_id, request, result))
                with cls._condition:
                    cls._condition.wait(timeout=0.05)


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

