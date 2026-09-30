"""MP4 直链下载：后台流式写入、校验续传、限频持久化；不访问 wx 控件。"""
from concurrent.futures import ThreadPoolExecutor
import os
from queue import SimpleQueue, Empty
import re
import threading
import time

import requests

from src.core.sys_setting import SysSetting
from src.media.downloader import Downloader
from src.schemas.task import MP4Task, TaskProgress, TaskStatus, FileStatus


class _Paused(Exception):
    pass


class MP4Downloader:
    def __init__(self, repository):
        self.repository = repository
        self._pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix='mp4-download')
        self._lock = threading.RLock()
        self._jobs = {}  # UUID → 暂停事件；直到后台关闭文件并保存最终状态才移除。
        self._closed = threading.Event()
        self._changes = SimpleQueue()
        self.errors = SimpleQueue()

    def busy(self, task_id):
        with self._lock:
            return task_id in self._jobs

    def changes(self):
        """界面定时器取走变化的任务 ID，只读取一次最新快照，不逐块刷新界面。"""
        ids = set()
        while True:
            try:
                ids.add(self._changes.get_nowait())
            except Empty:
                return ids

    def _write(self, task_id, update):
        record = self.repository.mutate(task_id, update)
        if record is None:
            raise ValueError('任务已删除，下载停止。')
        self._changes.put(task_id)
        return record

    def start(self, task_id):
        with self._lock:
            if self._closed.is_set() or task_id in self._jobs:
                return False
            def enqueue(task):
                if not isinstance(task, MP4Task):
                    raise ValueError('此下载器仅支持 MP4 直链任务。')
                if task.status == TaskStatus.COMPLETED:
                    raise ValueError('任务已完成。')
                task.status, task.last_error = TaskStatus.QUEUED, None
            self._write(task_id, enqueue)
            pause = threading.Event()
            self._jobs[task_id] = pause
            try:
                self._pool.submit(self._run, task_id, pause)
            except Exception:
                self._jobs.pop(task_id, None)
                self._write(task_id, lambda task: setattr(task, 'status', TaskStatus.INTERRUPTED))
                raise
            return True

    def pause(self, task_id):
        with self._lock:
            event = self._jobs.get(task_id)
            if event is not None:
                event.set()
                def pausing(task):
                    if task.status in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING):
                        task.status = TaskStatus.PAUSING
                self._write(task_id, pausing)

    def shutdown(self):
        self._closed.set()
        with self._lock:
            for pause in self._jobs.values():
                pause.set()
        # 工作线程会保存 INTERRUPTED；不在 GUI 线程等待网络超时。
        self._pool.shutdown(wait=False)

    def _check(self, pause):
        if pause.is_set() or self._closed.is_set():
            raise _Paused()

    def _run(self, task_id, pause):
        acquired = False
        try:
            Downloader.AcquireTransfer(lambda: self._check(pause))
            acquired = True
            retries = SysSetting.GetAll()['max_retries']
            for attempt in range(retries + 1):
                self._check(pause)
                try:
                    self._transfer(task_id, pause)
                    break
                except requests.RequestException as error:
                    # 非临时错误不重复请求；每次重试从已落盘文件长度恢复。
                    response = error.response
                    if (isinstance(error, (requests.exceptions.SSLError, requests.exceptions.TooManyRedirects))
                            or (response is not None and response.status_code < 500 and response.status_code != 429)
                            or attempt == retries):
                        raise
                    delay = min(2 ** attempt, 60)
                    if response is not None and response.status_code == 429:
                        delay = Downloader.RetryAfter(response.headers.get('Retry-After'), delay)
                        Downloader.DeferRequests(delay)
                    pause.wait(delay)
        except _Paused:
            self._finish_state(task_id, TaskStatus.INTERRUPTED if self._closed.is_set() else TaskStatus.PAUSED)
        except Exception as error:
            self._finish_state(task_id, TaskStatus.FAILED, str(error))
        finally:
            with self._lock:
                self._jobs.pop(task_id, None)
                if acquired:
                    Downloader.ReleaseTransfer()
            self._changes.put(task_id)

    def _finish_state(self, task_id, status, error=None):
        try:
            with self._lock:
                def update(task):
                    # 网络异常与暂停同时发生时，保留用户暂停意图。
                    if task.status == TaskStatus.COMPLETED:
                        return
                    task.status = (TaskStatus.INTERRUPTED if self._closed.is_set() else
                                   TaskStatus.PAUSED if self._jobs[task_id].is_set() else status)
                    task.last_error = error
                    path = task.save_dir / task.details.temporary_path
                    done = path.stat().st_size if path.is_file() else 0
                    total = task.progress.total_bytes
                    task.progress = TaskProgress(downloaded_bytes=done,
                                                 total_bytes=total if total is None or done <= total else None)
                self._write(task_id, update)
        except Exception as failure:
            self.errors.put(f'保存 MP4 下载状态失败：{failure}')

    def _transfer(self, task_id, pause):
        task = self.repository.get(task_id)
        if not isinstance(task, MP4Task):
            raise ValueError('MP4 任务不存在。')
        path = task.save_dir / task.details.temporary_path
        target = task.save_dir / task.details.target_path
        if target.exists():
            raise ValueError('目标文件已经存在，请核对文件；为避免覆盖，下载已停止。')
        path.parent.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        offset = path.stat().st_size if path.is_file() else 0
        etag = task.details.etag
        strong_etag = etag if etag and not etag.startswith('W/') else None
        validator = strong_etag or task.details.last_modified
        # 没有可验证的文件标识时从头下载，不能只凭文件长度拼接。
        offset = offset if validator else 0
        for fallback in range(2):
            self._check(pause)
            # 复用请求间隔限制；用可中断等待，暂停时不必等完限流周期。
            while True:
                self._check(pause)
                delay = Downloader.RequestDelay()
                if delay <= 0:
                    break
                pause.wait(min(delay, 0.2))
            headers = {'Accept-Encoding': 'identity'}
            if offset:
                headers.update({'Range': f'bytes={offset}-', 'If-Range': validator})
            with requests.get(task.source_url, headers=headers, stream=True,
                              timeout=SysSetting.GetTimeout()) as response:
                self._check(pause)
                if response.status_code == 416 and offset and not fallback:
                    offset = 0  # 文件长度变化或临时文件已完整：重新获取，绝不盲目认定完成。
                    continue
                response.raise_for_status()
                if response.status_code not in (200, 206):
                    raise ValueError(f'不支持的下载响应：HTTP {response.status_code}')
                if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                    raise ValueError('服务器返回了压缩内容，不能安全进行字节续传。')
                if 'text/html' in response.headers.get('Content-Type', '').lower():
                    raise ValueError('网址返回的是网页，请填写视频文件的直链。')
                total = None
                if response.status_code == 206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                    if not match:
                        raise ValueError('服务器返回无效的 Content-Range，已停止下载。')
                    start, end, total = map(int, match.groups())
                    changed = offset and ((strong_etag and response.headers.get('ETag') != strong_etag) or
                        (not strong_etag and response.headers.get('Last-Modified') != task.details.last_modified))
                    if start != offset or changed:
                        if offset and not fallback:
                            offset = 0
                            continue
                        raise ValueError('服务器返回的续传位置或文件标识不一致。')
                    if end < start or end >= total:
                        raise ValueError('服务器返回无效的续传范围。')
                    length = response.headers.get('Content-Length')
                    if length is not None and int(length) != end - start + 1:
                        raise ValueError('服务器返回的续传长度不一致。')
                else:
                    offset = 0  # Range 被忽略或 If-Range 不匹配，截断旧文件后从头写入。
                    length = response.headers.get('Content-Length')
                    total = int(length) if length is not None else None
                    if total is not None and total < 0:
                        raise ValueError('服务器返回无效的文件长度。')
                with path.open('ab' if offset else 'wb') as output:
                    def metadata(current):
                        current.details.verified_bytes = None
                        current.details.etag = response.headers.get('ETag')
                        current.details.last_modified = response.headers.get('Last-Modified')
                        current.details.supports_ranges = response.status_code == 206
                        current.status = TaskStatus.PAUSING if pause.is_set() else TaskStatus.DOWNLOADING
                        current.last_error = None
                        current.progress = TaskProgress(downloaded_bytes=offset, total_bytes=total)
                    self._write(task_id, metadata)
                    done, checkpoint = offset, time.monotonic()
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        self._check(pause)
                        if not chunk:
                            continue
                        if total is not None and done + len(chunk) > total:
                            raise ValueError('服务器实际数据超过声明的文件大小。')
                        output.write(chunk)
                        done += len(chunk)
                        if time.monotonic() - checkpoint >= 0.5:
                            output.flush()
                            self._write(task_id, lambda current: setattr(current, 'progress',
                                TaskProgress(downloaded_bytes=done, total_bytes=total)))
                            checkpoint = time.monotonic()
                    output.flush()
                    os.fsync(output.fileno())
                if total is not None and done != total:
                    raise requests.ConnectionError('连接提前结束，文件尚未完整。')
                if not done:
                    raise ValueError('服务器返回空文件。')
                with self._lock:
                    self._check(pause)
                    # 仅完整文件才发布正式名称；未完成内容始终保留为 .part。
                    self._write(task_id, lambda current: setattr(current.details, 'verified_bytes', done))
                    if target.exists():
                        raise ValueError('目标文件已存在，已保留下载临时文件，请核对后重试。')
                    os.rename(path, target)
                    def completed(current):
                        current.status, current.last_error = TaskStatus.COMPLETED, None
                        current.progress = TaskProgress(downloaded_bytes=done, total_bytes=done)
                        for item in current.outputs:
                            item.status, item.size_bytes = FileStatus.COMPLETED, done
                    self._write(task_id, completed)
                return
