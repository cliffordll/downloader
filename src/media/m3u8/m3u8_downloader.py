from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import os
from pathlib import Path
from queue import Queue, SimpleQueue, Empty
import threading
from typing import Any, TypedDict

import requests

from src.core.sys_setting import SysSetting
from src.media.downloader import Downloader
from src.schemas.task import TaskStatus


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

    界面调用 StartTask 入队 → 线程池下载 → 调度线程保存分片结果和任务状态
    → changes() 提供变化的 UUID，界面只读取数据库快照。
    本类方法均为项目自行调用的方法，不是 wx 自动调用的重写方法。
    暂停保留队列并阻止后续请求，已发出的请求允许完成，不中断到一半的文件。
    """
    isStop = True  # 调度线程是否已退出，不等于用户是否点击了暂停。
    threadQueue = Queue()  # 待调度元组：(网址, 文件路径, 后台回调, 任务身份, 文件键)。
    threadLock = threading.Lock()  # 保护队列调度相关状态与下方文件集合。
    _pending = set()  # 已接收且尚未保存结果的文件，用于防重复提交和判断忙碌。
    _requesting = set()  # 已获准发出请求、尚未结束的文件，用于区分暂停中/已暂停。
    _failed = set()  # 本次运行中失败的文件，重新入队时清除对应记录。
    _errors = {}  # 文件键 → 最后错误；调度线程将错误写入任务库。
    _paused_files = set()  # 单任务暂停通过暂停该任务的待完成文件实现。
    _master = None  # 后台调度线程；有任务时按需启动。
    _shutdown = threading.Event()  # 程序退出信号；wait(timeout) 可在退出时提前结束等待。
    _user_paused = threading.Event()  # 全部暂停开关，与单任务暂停集合分开维护。
    _state_lock = threading.RLock()  # 串行化批量入队和后台落库，避免一批分片尚未入队就被判定完成。
    _jobs = {}  # UUID → (TaskService, 本轮文件键, 已保存状态)，不保留任何窗口或控件。
    _changes = SimpleQueue()
    errors = SimpleQueue()
    _unsaved = {}  # 落库失败的结果保留，用户继续后先重试保存，不重新下载已完成的文件。
    _storage_error = ''

    @classmethod
    def changes(cls):
        """与 MP4 下载器一样，只传递变化的任务 ID；通知前结果已经落库。"""
        ids = set()
        while True:
            try:
                ids.add(cls._changes.get_nowait())
            except Empty:
                return ids

    @classmethod
    def StartTask(cls, service, task_id, sequences):
        """登记一批分片并入队；开始操作可由界面调用，下载结果由后台保存。"""
        with cls._state_lock:
            if cls._shutdown.is_set():
                return 0
            sequences = list(sequences)
            record = service.begin_download(task_id, sequences)
            if record is None:
                raise ValueError('任务已删除。')
            selected = set(sequences)
            previous = cls._jobs.get(task_id)
            keys = set(previous[1]) if previous else set()
            cls._jobs[task_id] = [service, keys, record.status]
            count = 0
            missing = []
            for segment in record.details.segments:
                if segment.sequence not in selected:
                    continue
                path = str(record.save_dir / segment.relative_path)
                key = cls.FileKey(path)
                keys.add(key)
                if not segment.source_url:
                    missing.append((path, segment.sequence, key))
                    continue
                options = {'headers': record.details.request_headers} if record.details.request_headers else {}
                if cls.DownloadTSFile(segment.source_url, path, cls._SaveResult,
                                      (task_id, segment.sequence), **options):
                    count += 1
            for path, sequence, key in missing:
                with cls.threadLock:
                    cls._errors[key] = '分片缺少下载地址。'
                cls._Deliver(('', path, cls._SaveResult, (task_id, sequence), key), False)
            if count == 0 and not (keys & cls.Snapshot()['pending']):
                service.interrupt(task_id)
                cls._jobs.pop(task_id, None)
            cls._changes.put(task_id)
            return count

    @classmethod
    def _StorageFailure(cls, error):
        cls.Pause()
        message = f'保存 M3U8 下载状态失败，已暂停后续下载：{error}'
        if message != cls._storage_error:
            cls.errors.put(message)
            cls._storage_error = message

    @classmethod
    def _RuntimeStatus(cls, keys, snapshot):
        pending = keys & snapshot['pending']
        if not pending:
            return None
        requesting = bool(pending & snapshot['requesting'])
        paused = snapshot['paused'] or pending <= snapshot['paused_files']
        return ((TaskStatus.PAUSING if requesting else TaskStatus.PAUSED) if paused
                else (TaskStatus.DOWNLOADING if requesting else TaskStatus.QUEUED))

    @classmethod
    def _SyncRuntime(cls):
        """调度线程更新运行状态；未变化不写库，暂停、退出均不依赖 GUI 定时器。"""
        with cls._state_lock:
            if cls._storage_error and cls.IsPaused() and not cls._shutdown.is_set():
                return
            snapshot = cls.Snapshot()
            for task_id, job in list(cls._jobs.items()):
                service, keys, saved_status = job
                status = cls._RuntimeStatus(keys, snapshot)
                try:
                    if cls._shutdown.is_set():
                        if saved_status in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.PAUSING):
                            record = service.interrupt(task_id)
                            if record is not None:
                                job[2] = record.status
                            cls._changes.put(task_id)
                        if not (keys & snapshot['pending']):
                            cls._jobs.pop(task_id, None)
                            cls._changes.put(task_id)
                    elif status is None:
                        cls._jobs.pop(task_id, None)
                        cls._changes.put(task_id)
                    elif status != saved_status and saved_status not in (
                            TaskStatus.MERGING, TaskStatus.COMPLETED, TaskStatus.WAITING_MERGE):
                        record = service.runtime_status(task_id, status, preserve_terminal=True)
                        if record is not None:
                            job[2] = record.status
                            cls._storage_error = ''
                            cls._changes.put(task_id)
                except Exception as error:
                    cls._StorageFailure(error)

    @classmethod
    def _SaveResult(cls, success, filename, context):
        """后台回调以 UUID + 分片序号落库；窗口关闭或列表重排不会影响保存。"""
        task_id, sequence = context
        job = cls._jobs.get(task_id)
        if job is None:
            return
        service, keys, _ = job
        snapshot = cls.Snapshot()
        key = cls.FileKey(filename)
        snapshot['pending'].discard(key)
        status = cls._RuntimeStatus(keys, snapshot)
        if status is None and (snapshot['paused'] or key in snapshot['paused_files']):
            status = TaskStatus.PAUSED
        if cls._shutdown.is_set():
            status = TaskStatus.INTERRUPTED
        record = service.finish_segment(task_id, sequence, filename, success,
                                        snapshot['errors'].get(key), runtime_status=status)
        if record is not None:
            job[2] = record.status
        cls._storage_error = ''
        cls._changes.put(task_id)

    @classmethod
    def IsBusy(cls):
        """队列、处理中或等待保存结果的文件仍存在时返回 True；暂停不等于空闲。"""
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
    def DownloadContent(cls, baseUrl, respect_pause=False, request_key=None, headers=None, *, consume=None):
        """同步请求 URL，返回 (True, 消费结果) 或 (False, 错误文字)。

        调用方决定在哪个线程执行；此方法本身不新建线程。
        默认消费结果为内容字节；分片使用 consume 逐块写盘，结果为已写入字节数。
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
                options: dict[str, Any] = {'headers': dict(headers)} if headers else {}
                if consume is not None:
                    options['stream'] = True
                response = requests.get(baseUrl, timeout=SysSetting.GetTimeout(), **options)
                try:
                    status = response.status_code
                    if status == 200:
                        return True, consume(response) if consume is not None else response.content
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
        """线程池逐块写入分片；每次重试截断临时文件，从头重新下载。

        先写同目录 .part 临时文件，再用 os.replace 替换目标，避免未写完的文件
        被当成已下载。返回 (成功与否, 文件路径)，由调度器保存结果。
        """
        partial = str(fileName) + '.part'
        try:
            if Path(fileName).is_file():
                return True, fileName
            Path(fileName).parent.mkdir(parents=True, exist_ok=True)

            def save(response):
                size = 0
                with open(partial, 'wb') as output:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if cls._shutdown.is_set():
                            raise _TaskPaused()
                        if chunk:
                            output.write(chunk)
                            size += len(chunk)
                    # requests 对压缩响应会解码，只有未压缩响应才能按 Content-Length 核对。
                    expected = response.headers.get('Content-Length')
                    if expected and not response.headers.get('Content-Encoding') and size != int(expected):
                        raise requests.ConnectionError('分片长度与服务器声明不符。')
                    if not size:
                        raise requests.ConnectionError('服务器返回了空分片。')
                    output.flush()
                    os.fsync(output.fileno())
                return size

            success, content = cls.DownloadContent(baseUrl, respect_pause=True,
                                                   request_key=cls.FileKey(fileName), consume=save,
                                                   **({'headers': headers} if headers else {}))
            if not success:
                with cls.threadLock:
                    cls._errors[cls.FileKey(fileName)] = str(content)
                return False, fileName
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
        """底层单分片入队方法；任务入口为 StartTask，不在调用线程中等待网络请求。

        返回 True 表示接受入队，不代表下载成功；重复任务或程序退出时返回 False。
        item 是 (任务 UUID, 分片序号)；callback 在后台执行，不能访问 wx 控件。
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
        """调度线程先保存结果，再释放 pending；落库失败保留结果等待用户继续。"""
        with cls._state_lock:
            try:
                cls._DeliverSaved(task, success)
                cls._unsaved.pop(task[4], None)
            except Exception as error:
                cls._unsaved[task[4]] = (task, success)
                cls._StorageFailure(error)

    @classmethod
    def _DeliverSaved(cls, task, success):
        _, filename, callback, item, key = task[:5]
        callback(success, filename, item)
        with cls.threadLock:
            cls._pending.discard(key)
            cls._paused_files.discard(key)
            if not success:
                cls._failed.add(key)
                cls._errors.setdefault(key, '分片下载失败。')
            else:
                cls._failed.discard(key)
                cls._errors.pop(key, None)

    @classmethod
    def _MasterThreadRun(cls):
        """后台调度循环：按当前并发配置补足工作线程，收集结果后保存数据库，并发出变化通知。"""
        # Future → 原始任务元组；只由调度线程维护，工作线程不直接修改。
        active = {}
        # 线程池上限固定为设置允许的最大值；实际并发由调度器按最新配置限制。
        # 降低并发时不会中断已有任务，等活动数量下降后再补充。
        with ThreadPoolExecutor(max_workers=16) as pool:
            while True:
                # 已完成但尚未保存的结果必须先处理；暂停时不反复冲击出错的数据库。
                if not cls.IsPaused() or cls._shutdown.is_set():
                    for task, success in list(cls._unsaved.values()):
                        cls._Deliver(task, success)
                cls._SyncRuntime()
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
                    if not active and ((cls.threadQueue.empty() and not cls._unsaved) or cls._shutdown.is_set()):
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
                        # 保存和通知均在后台执行，不再通过 wx.CallAfter 写库。
                        cls._Deliver(task, success)
                else:
                    cls._shutdown.wait(0.1)

    @classmethod
    def Shutdown(cls):
        """程序退出时停止调度并清空待运行队列；后台负责保存中断状态。

        不强行杀死正在执行的 requests 请求，它们会完成或按配置超时退出。
        此方法只发出停止信号，不在调用线程中等待所有工作线程结束。
        """
        cls._shutdown.set()
        with cls.threadLock:
            cls._paused_files.clear()
            while not cls.threadQueue.empty():
                task = cls.threadQueue.get_nowait()
                cls._pending.discard(task[4])
