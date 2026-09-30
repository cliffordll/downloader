from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import os
from pathlib import Path
from queue import Queue, Empty
import threading
from typing import TypedDict

import requests
import wx

from src.core.sys_setting import SysSetting
from src.media.downloader import Downloader


class DownloadSnapshot(TypedDict):
    """供界面读取的状态副本；集合里存的是标准化文件路径，不是界面行号。"""
    pending: set[str]
    requesting: set[str]
    failed: set[str]
    paused: bool
    paused_files: set[str]
    errors: dict[str, str]


class _TaskPaused(Exception):
    """让尚未发出请求的分片回到队列，释放线程给其他任务。"""


class M3U8Downloader:
    """管理所有任务的分片队列、并发请求和完成通知。

    界面调用 DownloadTSFile 入队 → 调度线程分配线程池 → _DownLoadFile 下载保存
    → wx.CallAfter 把 _Deliver 安排到界面线程 → 回调更新任务行。
    本类方法均为项目自行调用的方法，不是 wx 自动调用的重写方法。
    暂停保留队列并阻止后续请求，已发出的请求允许完成，不中断到一半的文件。
    """
    isStop = True  # 调度线程是否已退出，不等于用户是否点击了暂停。
    threadQueue = Queue()  # 待调度元组：(网址, 文件路径, 回调, 界面节点, 文件键)。
    threadLock = threading.Lock()  # 保护队列调度相关状态与下方文件集合。
    _pending = set()  # 已接收且尚未完成通知的文件，用于防重复提交和判断忙碌。
    _requesting = set()  # 已获准发出请求、尚未结束的文件，用于区分暂停中/已暂停。
    _failed = set()  # 本次运行中失败的文件，重新入队时清除对应记录。
    _errors = {}  # 文件键 → 最后错误；完成回调将错误写入任务库。
    _paused_files = set()  # 单任务暂停通过暂停该任务的待完成文件实现。
    _master = None  # 后台调度线程；有任务时按需启动。
    _shutdown = threading.Event()  # 程序退出信号；wait(timeout) 可在退出时提前结束等待。
    _user_paused = threading.Event()  # 全部暂停开关，与单任务暂停集合分开维护。
    @classmethod
    def IsBusy(cls):
        """队列、处理中或等待界面回调的文件仍存在时返回 True；暂停不等于空闲。"""
        with cls.threadLock:
            return bool(cls._pending)

    @staticmethod
    def FileKey(filename):
        """统一为绝对路径，并按系统规则处理大小写，供去重和跨线程状态匹配使用。"""
        return os.path.normcase(os.path.abspath(filename))

    @classmethod
    def Snapshot(cls) -> DownloadSnapshot:
        """加锁复制状态，避免界面遍历集合时后台线程同时修改；调用方不能借此修改内部集合。"""
        with cls.threadLock:
            return {
                'pending': set(cls._pending),
                'requesting': set(cls._requesting),
                'failed': set(cls._failed),
                'paused': cls.IsPaused(),
                'paused_files': set(cls._paused_files),
                'errors': dict(cls._errors),
            }

    @classmethod
    def IsPaused(cls):
        """只查询全部暂停开关；单任务是否暂停还要检查 paused_files。"""
        return cls._user_paused.is_set()

    @classmethod
    def Pause(cls):
        """暂停全部任务的后续请求；已发出的请求仍允许结束。"""
        with cls.threadLock:
            cls._user_paused.set()

    @classmethod
    def Resume(cls):
        """全部继续：同时解除全局暂停和每个任务的单独暂停标记。"""
        with cls.threadLock:
            cls._user_paused.clear()
            cls._paused_files.clear()

    @classmethod
    def PauseFiles(cls, keys):
        """暂停指定文件键中仍待完成的部分，不影响其他任务。"""
        with cls.threadLock:
            cls._paused_files.update(set(keys) & cls._pending)

    @classmethod
    def ResumeFiles(cls, keys):
        """只恢复指定文件；若之前全部暂停，先把其他待完成文件转为单独暂停。"""
        with cls.threadLock:
            # 全部暂停后也可以只恢复当前任务，其余排队任务继续保持暂停。
            if cls.IsPaused():
                cls._paused_files.update(cls._pending)
                cls._user_paused.clear()
            cls._paused_files.difference_update(keys)

    @classmethod
    def _WaitForRequest(cls, respect_pause=False, request_key=None):
        """每次请求（含重试）发出前，等待暂停和全局限流条件放行。

        respect_pause 为 True 时遵守用户暂停；播放列表读取默认不受用户暂停影响。
        返回 True 表示获得请求时机，False 表示程序正在退出。
        单任务暂停则抛出 _TaskPaused，让调度器重新入队，释放工作线程。
        """
        while not cls._shutdown.is_set():
            if respect_pause and cls.IsPaused():
                if request_key is not None:
                    raise _TaskPaused()  # 释放传输额度，单独继续的 MP4 不被暂停 TS 占位。
                cls._shutdown.wait(0.1)
                continue
            with cls.threadLock:
                if respect_pause and request_key in cls._paused_files:
                    raise _TaskPaused()
                if respect_pause and cls.IsPaused():
                    if request_key is not None:
                        raise _TaskPaused()
                    continue
                delay = Downloader.RequestDelay()
                if delay <= 0:
                    if request_key is not None:
                        cls._requesting.add(request_key)
                    return True
            cls._shutdown.wait(min(delay, 0.2))
        return False

    @classmethod
    def DownloadContent(cls, baseUrl, respect_pause=False, request_key=None, headers=None):
        """同步读取 URL，返回 (True, 内容字节) 或 (False, 错误文字)。

        调用方决定在哪个线程执行；此方法本身不新建线程。
        最大重试次数不包含首次请求；网络错误、5xx、429 可重试，
        403、其他小于 500 的非成功状态，以及 SSL/重定向异常直接返回失败。
        """
        retries = SysSetting.GetAll()['max_retries']
        error = '下载已停止'
        for attempt in range(retries + 1):
            if not cls._WaitForRequest(respect_pause, request_key):
                return False, '下载已停止'
            backoff = min(2 ** attempt, 60)
            try:
                options = {'headers': dict(headers)} if headers else {}
                response = requests.get(baseUrl, timeout=SysSetting.GetTimeout(), **options)
                try:
                    status = response.status_code
                    if status == 200:
                        return True, response.content
                    if status == 403:
                        return False, 'HTTP 403：服务器拒绝访问，请检查权限或链接有效期；未自动重试。'
                    error = f'HTTP {status}'
                    if status == 429:
                        # 限流等待影响后续所有请求，而不只是当前失败的分片。
                        delay = Downloader.RetryAfter(response.headers.get('Retry-After'), backoff)
                        Downloader.DeferRequests(delay)
                    elif status < 500:
                        return False, error
                finally:
                    # 无论成功、失败还是提前 return，都释放响应资源。
                    response.close()
            except (requests.exceptions.SSLError, requests.exceptions.TooManyRedirects) as exc:
                return False, str(exc)
            except requests.RequestException as exc:
                error = str(exc)
            finally:
                # 请求已结束；即使还要退避重试，也不算正在进行的网络请求。
                if request_key is not None:
                    with cls.threadLock:
                        cls._requesting.discard(request_key)
            if attempt < retries and cls._shutdown.wait(backoff):
                return False, '下载已停止'
        return False, error

    @classmethod
    def _DownLoadFile(cls, baseUrl, fileName, headers=None):
        """TS 与 MP4 共用并发额度；实际文件写入由 _SaveFile 完成。"""
        def check():
            if cls._shutdown.is_set():
                raise _TaskPaused()
            with cls.threadLock:
                if cls.IsPaused() or cls.FileKey(fileName) in cls._paused_files:
                    raise _TaskPaused()
        Downloader.AcquireTransfer(check)
        try:
            return cls._SaveFile(baseUrl, fileName, headers)
        finally:
            Downloader.ReleaseTransfer()

    @classmethod
    def _SaveFile(cls, baseUrl, fileName, headers=None):
        """线程池执行的单文件任务：跳过已有文件，下载完整内容后再写盘。

        先写同目录 .part 临时文件，再用 os.replace 替换目标，避免未写完的文件
        被扫描成已下载。返回 (成功与否, 文件路径)，由调度器统一通知界面。
        """
        partial = str(fileName) + '.part'
        try:
            if Path(fileName).is_file():
                return True, fileName
            success, content = cls.DownloadContent(baseUrl, respect_pause=True,
                                                   request_key=cls.FileKey(fileName),
                                                   **({'headers': headers} if headers else {}))
            if not success:
                print(f'下载失败：{fileName}：{content}')
                with cls.threadLock:
                    cls._errors[cls.FileKey(fileName)] = str(content)
                return False, fileName
            Path(fileName).parent.mkdir(parents=True, exist_ok=True)
            with open(partial, 'wb') as output:
                output.write(content)
            os.replace(partial, fileName)
            return True, fileName
        except OSError as exc:
            print(f'保存失败：{fileName}：{exc}')
            with cls.threadLock:
                cls._errors[cls.FileKey(fileName)] = str(exc)
            return False, fileName
        finally:
            if os.path.isfile(partial):
                try:
                    os.unlink(partial)
                except OSError:
                    pass

    @classmethod
    def DownloadTSFile(cls, absUri, absFile, callback, item, headers=None):
        """界面提交一个分片：只负责去重入队，不在调用线程中等待网络请求。

        返回 True 表示接受入队，不代表下载成功；重复任务或程序退出时返回 False。
        item 是回调上下文，新界面传入 (任务 UUID, 分片序号)，不能使用界面行号。
        """
        key = cls.FileKey(absFile)
        with cls.threadLock:
            if cls._shutdown.is_set() or key in cls._pending:
                return False
            cls._pending.add(key)
            cls._failed.discard(key)
            cls._errors.pop(key, None)
            # 请求头随分片快照入队；不同任务不共享 Session 或可变字典。
            task = (absUri, absFile, callback, item, key)
            cls.threadQueue.put(task + (dict(headers),) if headers else task)
            if cls.isStop:
                cls.isStop = False
                cls._master = threading.Thread(target=cls._MasterThreadRun, name='download-scheduler')
                cls._master.start()
            return True

    @classmethod
    def _Deliver(cls, task, success):
        """由 wx.CallAfter 在主线程调用，安全执行界面回调并清理任务状态。

        先清理队列状态再通知，使回调保存的状态不包含刚完成的请求。
        已销毁的窗口不再接收回调，启动恢复会核对已经原子写入的文件。
        """
        _, filename, callback, item, key = task[:5]
        with cls.threadLock:
            cls._pending.discard(key)
            cls._paused_files.discard(key)
            if not success:
                cls._failed.add(key)
                cls._errors.setdefault(key, '分片下载失败。')
            else:
                cls._failed.discard(key)
                cls._errors.pop(key, None)
        owner = getattr(callback, '__self__', None)
        if not cls._shutdown.is_set() and not (isinstance(owner, wx.Window) and not owner):
            callback(success, filename, item)

    @classmethod
    def _MasterThreadRun(cls):
        """后台调度循环：按当前并发配置补足工作线程，收集结果后安排界面通知。"""
        # Future → 原始任务元组；只由调度线程维护，工作线程不直接修改。
        active = {}
        # 线程池上限固定为设置允许的最大值；实际并发由调度器按最新配置限制。
        # 降低并发时不会中断已有任务，等活动数量下降后再补充。
        with ThreadPoolExecutor(max_workers=16) as pool:
            while True:
                with cls.threadLock:
                    if not cls._shutdown.is_set() and not cls.IsPaused():
                        # 每轮最多扫描一次队列，跳过暂停任务，避免挡住其他任务或忙循环。
                        remaining = cls.threadQueue.qsize()
                        while len(active) < SysSetting.GetMaxWorkers() and remaining:
                            remaining -= 1
                            try:
                                task = cls.threadQueue.get_nowait()
                            except Empty:
                                break
                            if task[4] in cls._paused_files:
                                cls.threadQueue.put(task)
                                continue
                            options = {'headers': task[5]} if len(task) > 5 else {}
                            active[pool.submit(cls._DownLoadFile, task[0], task[1], **options)] = task
                    if not active and (cls.threadQueue.empty() or cls._shutdown.is_set()):
                        cls.isStop = True
                        break
                if active:
                    # 任意一个任务完成就处理并补位，不必等整批最慢的分片结束。
                    done, _ = wait(active, timeout=0.1, return_when=FIRST_COMPLETED)
                    for future in done:
                        task = active.pop(future)
                        try:
                            success, _ = future.result()
                        except _TaskPaused:
                            # 已分配线程、尚未发请求时遇到暂停：重新入队，不记为失败。
                            with cls.threadLock:
                                if cls._shutdown.is_set():
                                    cls._pending.discard(task[4])
                                else:
                                    cls.threadQueue.put(task)
                            continue
                        except Exception as exc:
                            print(f'下载任务异常：{exc}')
                            with cls.threadLock:
                                cls._errors[task[4]] = str(exc)
                            success = False
                        if cls._shutdown.is_set():
                            with cls.threadLock:
                                cls._pending.discard(task[4])
                        else:
                            # 工作线程不能直接更新 wx 控件，将通知交回主线程执行。
                            wx.CallAfter(cls._Deliver, task, success)
                else:
                    cls._shutdown.wait(0.1)

    @classmethod
    def Shutdown(cls):
        """程序退出时停止调度并清空待运行队列，不再发送界面回调。

        不强行杀死正在执行的 requests 请求，它们会完成或按配置超时退出。
        此方法只发出停止信号，不在调用线程中等待所有工作线程结束。
        """
        cls._shutdown.set()
        with cls.threadLock:
            cls._paused_files.clear()
            while not cls.threadQueue.empty():
                task = cls.threadQueue.get_nowait()
                cls._pending.discard(task[4])
