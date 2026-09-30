"""协调任务执行；只接收任务 UUID 和分片序号，不依赖窗口、列表模型或 wx。"""
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, SimpleQueue
import sqlite3
from threading import Event, RLock

from src.core.sys_setting import SysSetting
from src.core.task_service import TaskService
from src.media.m3u8.ffmpeg_converter import FFmpegConverter
from src.media.m3u8.m3u8_downloader import M3U8Downloader
from src.media.mp4.mp4_downloader import MP4Downloader
from src.schemas.task import FileStatus, M3U8Task, MP4Task, TaskStatus
from src.storage.task_repository import TaskDataError, TaskConflictError


class TaskRunner:
    """一个应用使用一个协调对象，共享入口创建的 TaskService。

    命令重新读取业务记录，避免筛选、排序或界面快照过期影响目标任务。
    下载仍由各自引擎执行；检测和合并回调在后台保存，通过队列通知界面。
    """
    def __init__(self, tasks: TaskService):
        self.tasks = tasks
        self.mp4 = MP4Downloader(tasks.repository)
        self.closed = Event()
        self.errors = SimpleQueue()
        self.duration_results = SimpleQueue()
        self._changes = SimpleQueue()
        self._lock = RLock()
        self._duration_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='duration-probe')
        self._duration_jobs = set()
        self._duration_blocked = set()
        self._merging = set()

    def _task(self, task_id):
        task = self.tasks.repository.get(task_id)
        if task is None:
            raise ValueError('任务不存在，请刷新后重试。')
        return task

    @staticmethod
    def _keys(task):
        return {M3U8Downloader.FileKey(task.save_dir / segment.relative_path)
                for segment in task.details.segments} if isinstance(task, M3U8Task) else set()

    def start(self, task_id, *, sequences=None, retry=False):
        """按任务类型选择引擎；重试仅选择失败且缺失的分片，单片下载按稳定序号选择。"""
        if self.closed.is_set():
            return 0
        task = self._task(task_id)
        if isinstance(task, MP4Task):
            return self.mp4.start(task_id)
        if not isinstance(task, M3U8Task):
            raise ValueError('此任务类型尚不支持下载。')
        with self._lock:
            merging = task_id in self._merging
        if merging or FFmpegConverter.IsConverting(str(task.save_dir / 'output.mp4')):
            raise ValueError('任务正在合并，请稍后操作。')
        # 已完成分片的时长检测可与剩余分片下载并行；服务会校验文件身份再保存时长。
        selected = None if sequences is None else set(sequences)
        if selected is not None and not selected <= {s.sequence for s in task.details.segments}:
            raise ValueError('分片序号无效，请刷新后重试。')
        failed = M3U8Downloader.Snapshot()['failed']
        wanted = [s.sequence for s in task.details.segments
                  if (selected is None or s.sequence in selected)
                  and not (task.save_dir / s.relative_path).is_file()
                  and (not retry or s.status == FileStatus.FAILED
                       or M3U8Downloader.FileKey(task.save_dir / s.relative_path) in failed)]
        if not wanted:
            return 0
        try:
            return M3U8Downloader.StartTask(self.tasks, task_id, wanted)
        except Exception:
            M3U8Downloader.Pause()
            raise

    def activate(self, task_id, *, retry=False):
        """行内开始按钮：运行中暂停，已暂停队列继续；无队列才创建新下载请求。"""
        task = self._task(task_id)
        if isinstance(task, MP4Task):
            if self.mp4.busy(task_id):
                self.mp4.pause(task_id)
                return False
            return self.start(task_id, retry=retry)
        if isinstance(task, M3U8Task) and not retry:
            snapshot = M3U8Downloader.Snapshot()
            pending = self._keys(task) & snapshot['pending']
            if pending:
                if snapshot['paused'] or pending <= snapshot['paused_files']:
                    M3U8Downloader.ResumeFiles(pending)
                else:
                    M3U8Downloader.PauseFiles(pending)
                return False
        return self.start(task_id, retry=retry)

    def pause_all(self):
        """操作全部业务任务，包括界面筛选隐藏的任务；单个任务失败不阻断其他任务。"""
        snapshot = M3U8Downloader.Snapshot()
        if not snapshot['paused'] and snapshot['pending'] - snapshot['paused_files']:
            M3U8Downloader.Pause()
        for task in self.tasks.repository.list_tasks():
            if isinstance(task, MP4Task):
                try:
                    self.mp4.pause(task.id)
                except Exception as error:
                    self.errors.put(f'暂停 {task.name} 失败：{error}')

    def resume_all(self):
        """恢复现有队列；重启后的暂停/中断任务重新入队，不启动新建或失败任务。"""
        M3U8Downloader.Resume()
        pending = M3U8Downloader.Snapshot()['pending']
        started = set()
        for task in self.tasks.repository.list_tasks():
            if task.status not in (TaskStatus.PAUSED, TaskStatus.INTERRUPTED):
                continue
            if isinstance(task, M3U8Task) and self._keys(task) & pending:
                continue  # 保留原先只下载指定分片的范围。
            if not isinstance(task, (M3U8Task, MP4Task)):
                continue
            try:
                if self.start(task.id):
                    started.add(task.id)
            except Exception as error:
                self.errors.put(f'继续 {task.name} 失败：{error}')
        return started

    def busy_processing(self, task_id):
        with self._lock:
            return task_id in self._duration_jobs or task_id in self._merging

    def busy(self, task_id):
        if self.busy_processing(task_id) or self.mp4.busy(task_id):
            return True
        task = self._task(task_id)
        if isinstance(task, M3U8Task):
            return bool(self._keys(task) & M3U8Downloader.Snapshot()['pending']) or FFmpegConverter.IsConverting(
                str(task.save_dir / 'output.mp4'))
        return task.status in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.PAUSING,
                               TaskStatus.RECORDING, TaskStatus.STOPPING)

    def delete(self, task_id):
        """确认弹窗由界面负责；执行删除前再次核对忙碌状态，只删除记录。"""
        if self.busy(task_id):
            raise ValueError('任务仍在下载、检测或合并中，暂时不能删除。')
        self.tasks.repository.delete(task_id)

    def changes(self):
        """合并两种下载器和媒体处理的通知；统一交给界面读取最新记录。"""
        ids = self.mp4.changes() | M3U8Downloader.changes()
        while True:
            try:
                ids.add(self._changes.get_nowait())
            except Empty:
                break
        for engine in (self.mp4, M3U8Downloader):
            while True:
                try:
                    self.errors.put(engine.errors.get_nowait())
                except Empty:
                    break
        return ids

    def queue_duration(self, task_id):
        """每项任务同时只运行一次检测；数据库故障后等待显式刷新再尝试。"""
        with self._lock:
            if (self.closed.is_set() or task_id in self._duration_jobs
                    or task_id in self._duration_blocked):
                return
            self._duration_jobs.add(task_id)
            try:
                future = self._duration_pool.submit(self.tasks.detect_durations, task_id, self.closed)
            except Exception:
                self._duration_jobs.discard(task_id)
                raise
            future.add_done_callback(lambda result: self._duration_finished(task_id, result))

    def _duration_finished(self, task_id, future):
        with self._lock:
            self._duration_jobs.discard(task_id)
            if future.cancelled() or self.closed.is_set():
                return
            error = None
            try:
                future.result()  # TaskService 已将每片检测结果保存到数据库。
            except Exception as failure:
                error = str(failure)
                self._duration_blocked.add(task_id)
            self.duration_results.put((task_id, error))

    def block_duration(self, task_id):
        with self._lock:
            self._duration_blocked.add(task_id)

    def reset_duration_failures(self):
        with self._lock:
            self._duration_blocked.clear()

    def write_playlist(self, task_id):
        return self.tasks.write_playlist(task_id)

    def merge(self, task_id):
        """准备清单并启动合并；文件发布、结果落库不依赖界面回调。"""
        with self._lock:
            if self.closed.is_set():
                return
            task = self._task(task_id)
            if not isinstance(task, M3U8Task):
                raise ValueError('只有 M3U8 分片任务可以合并。')
            if self.busy(task_id):
                raise ValueError('任务仍在下载、检测或合并中，请稍后操作。')
            output = task.save_dir / 'output.mp4'
            if output.exists():
                raise ValueError('视频文件已经存在。')
            seed = self.write_playlist(task_id)
            playlist = task.save_dir / 'playlist.txt'
            if not FFmpegConverter.ConcatPlaylist(str(seed), str(task.save_dir), str(playlist)):
                raise ValueError('播放列表无效，无法生成合并清单。')
            try:
                record = self.tasks.begin_merge(task_id)
            except (OSError, sqlite3.Error, TaskDataError, TaskConflictError):
                M3U8Downloader.Pause()
                raise
            if record is None:
                raise ValueError('任务已删除。')
            self._merging.add(task_id)
            self._changes.put(task_id)
            try:
                FFmpegConverter.ConvertTSFile(str(playlist), str(output), self._merge_finished, task_id)
            except Exception as error:
                # 与后台失败共用一次通知，避免调用方弹窗和错误队列重复提示。
                self._merge_finished(False, str(output), task_id, str(error))

    def _merge_finished(self, success, filename, task_id, error=None):
        with self._lock:
            try:
                self.tasks.finish_merge(task_id, filename, success)
                if not success:
                    self.errors.put(f'视频文件合并失败：{error}' if error else
                                    '视频文件合并失败，错误状态已保存，可重新转 MP4。')
            except Exception as error:
                M3U8Downloader.Pause()
                self.errors.put(f'合并结果保存失败：{error}')
            finally:
                self._merging.discard(task_id)
                self._changes.put(task_id)

    def complete(self, task_id):
        """分片齐备后的处理顺序：检测实际时长、重建播放列表、按设置自动合并。"""
        task = self._task(task_id)
        if not isinstance(task, M3U8Task) or task.status != TaskStatus.WAITING_MERGE:
            return  # 忽略已进入合并或合并失败后的迟到完成事件，不能自动重试合并。
        if task.details.detect_duration and any(s.duration_status == 'pending' for s in task.details.segments):
            self.queue_duration(task_id)
            return
        self.write_playlist(task_id)
        if SysSetting.GetAll()['auto_merge'] and not any(o.status == FileStatus.COMPLETED for o in task.outputs):
            self.merge(task_id)

    def shutdown(self, merging_ids=()):
        """停止下载和检测，保存合并中断状态；已经运行的转换仍可在后台保存最终结果。"""
        self.closed.set()
        self.mp4.shutdown()
        M3U8Downloader.Shutdown()
        self._duration_pool.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            for task_id in self._merging | set(merging_ids):
                try:
                    self.tasks.interrupt(task_id)
                except Exception as error:
                    self.errors.put(f'保存退出状态失败：{error}')
