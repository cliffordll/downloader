"""协调任务执行；只接收任务 UUID 和分片序号，不依赖窗口、列表模型或 wx。"""
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, SimpleQueue
import sqlite3
from threading import Event, RLock

from src.core.sys_setting import SysSetting
from src.core.task_service import TaskService
from src.core.task_files import trash_task_files
from src.core.task_paths import output_for, output_path, segment_path
from src.core.task_rules import can_rename
from src.media.m3u8.ffmpeg_converter import FFmpegConverter
from src.media.m3u8.m3u8_downloader import M3U8Downloader
from src.media.mp4.mp4_downloader import MP4Downloader
from src.schemas.task import FileStatus, SegmentStatus, M3U8Task, MP4Task, TaskStatus
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
        self._encryption_context_provider = None

    def set_encryption_context_provider(self, provider):
        """Supply the unlocked password-derived key only to explicitly encrypted MP4 jobs."""
        self._encryption_context_provider = provider

    def _task(self, task_id):
        task = self.tasks.repository.get(task_id)
        if task is None:
            raise ValueError('任务不存在，请刷新后重试。')
        return task

    @staticmethod
    def _keys(task):
        return {M3U8Downloader.FileKey(segment_path(task, segment))
                for segment in task.details.media_segments} if isinstance(task, M3U8Task) else set()

    def start(self, task_id, *, sequences=None, retry=False):
        with self._lock:
            return self._start_locked(task_id, sequences=sequences, retry=retry)

    def _start_locked(self, task_id, *, sequences=None, retry=False):
        """按任务类型选择引擎；重试仅选择失败且缺失的分片，单片下载按稳定序号选择。"""
        if self.closed.is_set():
            return 0
        task = self._task(task_id)
        if isinstance(task, MP4Task):
            if self.mp4.busy(task_id):
                return False
            task = self.check_files(task_id)
            if task.status == TaskStatus.COMPLETED:
                return False
            if not task.encrypted:
                return self.mp4.start(task_id)
            context = self._encryption_context_provider() if self._encryption_context_provider else None
            return self.mp4.start(task_id, encryption_context=context)
        if not isinstance(task, M3U8Task):
            raise ValueError('此任务类型尚不支持下载。')
        with self._lock:
            merging = task_id in self._merging
        merged = output_for(task, 'merged')
        if merged is None:
            raise ValueError('M3U8 任务缺少合并产物记录。')
        output = output_path(task, merged)
        if merging or FFmpegConverter.IsConverting(str(output)):
            raise ValueError('任务正在合并，请稍后操作。')
        # 已完成分片的时长检测可与剩余分片下载并行；服务会校验文件身份再保存时长。
        selected = None if sequences is None else set(sequences)
        if selected is not None and not selected <= {s.sequence for s in task.details.media_segments}:
            raise ValueError('分片序号无效，请刷新后重试。')
        task = self.check_files(task_id)
        failed = M3U8Downloader.Snapshot()['failed']
        wanted = [s.sequence for s in task.details.media_segments
                  if (selected is None or s.sequence in selected)
                  and s.status != SegmentStatus.COMPLETED
                  and (not retry or s.status == SegmentStatus.FAILED
                       or M3U8Downloader.FileKey(segment_path(task, s)) in failed)]
        if not wanted:
            if task.status == TaskStatus.WAITING_MERGE:
                self.complete(task_id)
            return 0
        try:
            count = M3U8Downloader.StartTask(self.tasks, task_id, wanted)
            if count:
                # 用户主动开始/重试只恢复本次选择，其他全局暂停的任务保持暂停。
                M3U8Downloader.ResumeFiles({M3U8Downloader.FileKey(segment_path(task, s))
                                           for s in task.details.media_segments if s.sequence in wanted})
            return count
        except Exception:
            M3U8Downloader.Pause()
            raise

    def update_segment_info(self, task_id, sequence, source_url, duration=None):
        """修改前排除下载/媒体处理，保存后重建本地清单。"""
        with self._lock:
            task = self._task(task_id)
            if not isinstance(task, M3U8Task):
                raise ValueError('只有 TS 分片可以修改分片信息。')
            segment = next((s for s in task.details.media_segments if s.sequence == sequence), None)
            if segment is None:
                raise ValueError('分片不存在，请刷新后重试。')
            if self.busy(task_id) or self._keys(task) & M3U8Downloader.Snapshot()['requesting']:
                raise ValueError('任务仍在下载、检测或合并中，请结束后再修改。')
            record = self.tasks.update_segment_info(task_id, sequence, source_url, duration)
            try:
                self.write_playlist(task_id)
            except OSError as error:
                raise OSError(f'分片信息已保存，但本地 M3U8 更新失败：{error}') from error
            finally:
                self._changes.put(task_id)
            return record

    def check_files(self, task_id):
        try:
            task = self.tasks.check_files(task_id)
            if task is None:
                raise ValueError('任务不存在，请刷新后重试。')
            return task
        except (OSError, sqlite3.Error, TaskDataError, TaskConflictError):
            M3U8Downloader.Pause()
            raise
        finally:
            self._changes.put(task_id)

    def _check_before_resume(self, task):
        completed = {segment.sequence for segment in task.details.media_segments
                     if segment.status == SegmentStatus.COMPLETED}
        checked = self.check_files(task.id)
        missing = [segment.sequence for segment in checked.details.media_segments
                   if segment.sequence in completed and segment.status == SegmentStatus.MISSING]
        if missing:
            # 暂停期间丢失的已完成分片也要补下载，其余未选中的分片不加入队列。
            self.start(task.id, sequences=missing)

    def activate(self, task_id, *, retry=False):
        """行内开始按钮：运行中暂停，已暂停队列继续；无队列才创建新下载请求。"""
        with self._lock:
            return self._activate_locked(task_id, retry=retry)

    def _activate_locked(self, task_id, *, retry=False):
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
                    self._check_before_resume(task)
                    self.tasks.runtime_status(task_id, TaskStatus.QUEUED)
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
        """恢复现有队列；暂停、中断和部分完成的任务重新入队，不启动新建或失败任务。"""
        snapshot = M3U8Downloader.Snapshot()
        records = self.tasks.repository.list_tasks()
        pending_tasks = [task for task in records
                         if isinstance(task, M3U8Task) and self._keys(task) & snapshot['pending']]
        for task in pending_tasks:
            if task.status in (TaskStatus.PAUSED, TaskStatus.INTERRUPTED, TaskStatus.PARTIAL):
                self.tasks.runtime_status(task.id, TaskStatus.QUEUED)
        for task in pending_tasks:
            self._check_before_resume(task)
        M3U8Downloader.Resume()
        pending = M3U8Downloader.Snapshot()['pending']
        started = set()
        for task in self.tasks.repository.list_tasks():
            if task.status not in (TaskStatus.PAUSED, TaskStatus.INTERRUPTED, TaskStatus.PARTIAL):
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
            pending = bool(self._keys(task) & M3U8Downloader.Snapshot()['pending'])
            merged = output_for(task, 'merged')
            return pending or bool(merged and FFmpegConverter.IsConverting(str(output_path(task, merged))))
        return task.status in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.PAUSING,
                               TaskStatus.RECORDING, TaskStatus.STOPPING)

    def rename(self, task_id, name):
        """Serialize rename against task starts and reject work represented in the task record."""
        with self._lock:
            task = self._task(task_id)
            if not can_rename(task):
                raise ValueError('任务正在下载、检测或合并，暂时不能重命名。')
            return self.tasks.rename_task(task_id, name)

    def set_task_encrypted(self, task_id, encrypted, *, artifacts_prepared=False):
        """Serialize task locking against starts, detection and merge operations."""
        with self._lock:
            task = self._task(task_id)
            if not can_rename(task):
                raise ValueError('任务正在下载、检测或合并，暂时不能加密或解密。')
            return self.tasks.set_task_encrypted(
                task_id, encrypted, artifacts_prepared=artifacts_prepared)

    def delete(self, task_id, *, delete_files=False, scope='task'):
        """统一处理任务、工作文件和 MP4 删除；单删文件保留记录并核对状态。"""
        if scope not in ('task', 'work', 'output'):
            raise ValueError('无效的删除范围。')
        with self._lock:
            task = self._task(task_id)
            merged = output_for(task, 'merged') if isinstance(task, M3U8Task) else None
            output = output_path(task, merged) if merged else None
            merging = task_id in self._merging or task.status == TaskStatus.MERGING or bool(
                output and FFmpegConverter.IsConverting(str(output)))
            if merging or (scope != 'output' and self.busy_processing(task_id)):
                raise ValueError('任务正在检测或合并中，暂时不能删除。')
            if isinstance(task, M3U8Task) and scope != 'output':
                snapshot = M3U8Downloader.Snapshot()
                keys = self._keys(task)
                pending = keys & snapshot['pending']
                if keys & snapshot['requesting'] or (pending and not snapshot['paused']
                                                    and not pending <= snapshot['paused_files']):
                    raise ValueError('任务仍在下载，暂时不能删除。请先暂停并等待在途请求结束。')
                M3U8Downloader.RemovePausedTask(task_id, keys)
                self.tasks.interrupt(task_id)
            elif not isinstance(task, M3U8Task) and self.busy(task_id):
                raise ValueError('任务仍在下载，暂时不能删除。')
            if delete_files or scope != 'task':
                others = [other for other in self.tasks.repository.list_tasks() if other.id != task_id]
                try:
                    trash_task_files(task, others, scope='all' if scope == 'task' else scope)
                finally:
                    if scope != 'task':
                        self.check_files(task_id)
            if scope == 'task':
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

    def detect_durations(self, task_id):
        """手动重检已完成分片，不修改自动检测选项，也不启动合并。"""
        with self._lock:
            task = self._task(task_id)
            if not isinstance(task, M3U8Task):
                raise ValueError('只有 M3U8 和 TS 任务可以检测分片时长。')
            if self.busy(task_id):
                raise ValueError('任务仍在下载、检测或合并中，请结束后再检测。')
            if not any(s.status == SegmentStatus.COMPLETED for s in task.details.media_segments):
                raise ValueError('没有已下载的分片可供检测。')
            if not SysSetting.GetFFprobe():
                raise ValueError('找不到 ffprobe，请先配置 FFmpeg 所在目录。')
            self._duration_blocked.discard(task_id)
            self.queue_duration(task_id, force=True)

    def queue_duration(self, task_id, *, force=False):
        """每项任务同时只运行一次检测；数据库故障后等待显式刷新再尝试。"""
        with self._lock:
            if (self.closed.is_set() or task_id in self._duration_jobs
                    or task_id in self._duration_blocked):
                return
            record = self.tasks.set_duration_detection(task_id, True)
            if record is None:
                raise ValueError('任务不存在，请刷新后重试。')
            self._duration_jobs.add(task_id)
            try:
                future = self._duration_pool.submit(self.tasks.detect_durations, task_id, self.closed, force=force)
            except Exception:
                self._duration_jobs.discard(task_id)
                self.tasks.set_duration_detection(task_id, False)
                raise
            future.add_done_callback(lambda result: self._duration_finished(task_id, result))

    def _duration_finished(self, task_id, future):
        with self._lock:
            self._duration_jobs.discard(task_id)
            error = None
            try:
                if not future.cancelled():
                    future.result()  # TaskService 已将每片检测结果保存到数据库。
            except Exception as failure:
                error = str(failure)
                self._duration_blocked.add(task_id)
            try:
                self.tasks.set_duration_detection(task_id, False)
            except Exception as failure:
                error = error or str(failure)
                self._duration_blocked.add(task_id)
            if not self.closed.is_set():
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
            merged = output_for(task, 'merged')
            if merged is None:
                raise ValueError('M3U8 任务缺少合并产物记录。')
            output = output_path(task, merged)
            task = self.check_files(task_id)
            if output.exists():
                raise ValueError('视频文件已经存在。')
            seed = self.write_playlist(task_id)
            playlist = seed.parent / 'playlist.txt'
            if not FFmpegConverter.ConcatPlaylist(str(seed), str(seed.parent), str(playlist)):
                raise ValueError('播放列表无效，无法生成合并清单。')
            try:
                record = self.tasks.begin_merge(task_id)
            except ValueError:
                self._changes.put(task_id)
                raise
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
        if task.details.detect_duration and any(s.duration_status == 'pending' for s in task.details.media_segments):
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
