"""管理任务创建、持久化状态和恢复，不依赖窗口或列表模型。"""

from pathlib import Path
import math
import os
import tempfile
import subprocess
import stat
from src.media.m3u8.ffmpeg_converter import FFmpegConverter

from src.media.m3u8.m3u8_parser import M3U8Parser
from src.core.path_manager import PathManager
from src.storage.task_repository import TaskRepository
from src.schemas.task import M3U8Task, M3U8Details, MP4Task, MP4Details, SourceType, TaskOutput, TaskProgress, TaskSegment, TaskStatus, FileStatus


class TaskService:
    def __init__(self, repository: TaskRepository):
        # 仓库由启动入口创建；服务和界面不再隐式打开默认数据库。
        # 仓库每次操作自行管理连接，共享的是仓库对象，不是跨线程的 SQLite 连接。
        self.repository = repository
        self._recovered = False

    def _create_named(self, save_dir, filename, source_url, build):
        """文件、同名工作目录及数据库记录共同参与重名检查。"""
        directory = Path(save_dir).expanduser()
        if not directory.is_absolute():
            raise ValueError('保存目录必须是绝对路径。')
        directory = directory.resolve()
        name = PathManager.ValidateFileName(filename) if filename and filename.strip() else PathManager.GetFileNameFromURI(source_url)
        created_files, created_dirs = [], []

        def allocate(existing):
            reserved = set()
            for task in existing:
                reserved.update(PathManager.GetPathKey(task.save_dir / output.relative_path) for output in task.outputs)
                if isinstance(task, MP4Task):
                    reserved.add(PathManager.GetPathKey(task.save_dir / task.details.temporary_path))
                if isinstance(task, M3U8Task) and task.details.playlist_path:
                    work = (task.save_dir / task.details.playlist_path).parent
                    if PathManager.GetPathKey(directory) == PathManager.GetPathKey(work) or PathManager.GetPathKey(directory).startswith(PathManager.GetPathKey(work) + os.sep):
                        raise ValueError('保存目录不能位于已有任务的分片工作目录内。')
                    reserved.add(PathManager.GetPathKey(work))
            directory.mkdir(parents=True, exist_ok=True)
            occupied = reserved | {PathManager.GetPathKey(path) for path in directory.iterdir()}
            index = 0
            while True:
                stem = name[:-4] + (f' ({index})' if index else '')
                target = stem + '.mp4'
                candidates = [directory / value for value in
                              (target, stem, target + '.part', target + '.part.mp4')]
                if any(PathManager.GetPathKey(path) in occupied or path.exists() or path.is_symlink() for path in candidates):
                    index += 1
                    continue
                task = build(directory, stem, target)
                try:
                    if isinstance(task, M3U8Task):
                        work = directory / stem
                        work.mkdir(exist_ok=False)
                        created_dirs.append(work)
                        playlist = directory / task.details.playlist_path
                        created_files.append(playlist)
                        playlist.write_text(self.local_playlist(task), encoding='utf-8')
                    else:
                        partial = directory / task.details.temporary_path
                        with partial.open('xb'):
                            pass
                        created_files.append(partial)
                except FileExistsError:
                    if created_dirs:
                        raise
                    index += 1
                    continue
                return task
        try:
            return self.repository.create_allocated(allocate)
        except Exception:
            for path in reversed(created_files):
                path.unlink(missing_ok=True)
            for path in reversed(created_dirs):
                path.rmdir()
            raise

    def create_mp4(self, save_dir, source_url, *, filename=None, request_headers=None):
        """直链保存在所选目录；正式名称和 .part 名称在创建时确定。"""
        def build(directory, stem, target):
            return MP4Task(name=target, save_dir=directory, source_url=source_url.strip(),
                           details=MP4Details(target_path=target, temporary_path=target + '.part',
                                              request_headers=request_headers or {}),
                           outputs=[TaskOutput(relative_path=target, kind='download')])
        return self._create_named(save_dir, filename, source_url, build)

    def create_m3u8(self, save_dir, source_url, content, base_path='', *, filename=None,
                    source_type=SourceType.M3U8, ts_pattern=None, detect_duration=False, request_headers=None):
        """分片写入同名工作目录，最终 MP4 位于保存目录；两个入口共用命名规则。"""
        if not content.strip():
            raise ValueError('请先获取播放列表或添加 TS 分片。')
        parser = M3U8Parser(content=content, base_path=base_path, m3u8_uri=source_url)
        if not parser.segments:
            raise ValueError('列表没有可下载的分片；主播放列表请先选择具体清晰度的播放列表。')
        if any((segment.key and segment.key.method != 'NONE') or segment.byterange
               or segment.init_section for segment in parser.segments):
            raise ValueError('当前暂不支持加密、字节范围或带初始化片段的播放列表。')
        urls = parser.parse_media()
        segments = []
        for index, (parsed, resolved) in enumerate(zip(parser.segments, urls)):
            if not resolved.absUri:
                raise ValueError('分片地址不完整，请填写参考列表网址或使用完整的分片网址。')
            segments.append(TaskSegment(sequence=index, relative_path=f'segments/{index:06d}.ts',
                                        source_url=resolved.absUri, duration=parsed.duration))
        def build(directory, stem, target):
            children = [segment.model_copy(update={'relative_path': f'{stem}/{segment.relative_path}'})
                        for segment in segments]
            return M3U8Task(name=target, save_dir=directory, source_type=source_type,
                           source_url=source_url or None,
                           details=M3U8Details(playlist_path=f'{stem}/download.m3u8', ts_pattern=ts_pattern,
                                               detect_duration=detect_duration,
                                               request_headers=request_headers or {}, segments=children),
                           progress=TaskProgress(total_segments=len(children)),
                           outputs=[TaskOutput(relative_path=target, kind='merged')])
        return self._create_named(save_dir, filename, source_url, build)

    @staticmethod
    def local_playlist(task):
        maximum = max((segment.duration or 0 for segment in task.details.segments), default=0)
        lines = ['#EXTM3U', '#EXT-X-VERSION:3', f'#EXT-X-TARGETDURATION:{max(1, math.ceil(maximum))}',
                 '#EXT-X-MEDIA-SEQUENCE:0', '#EXT-X-PLAYLIST-TYPE:VOD']
        for segment in sorted(task.details.segments, key=lambda segment: segment.sequence):
            relative = os.path.relpath(task.save_dir / segment.relative_path,
                                       (task.save_dir / task.details.playlist_path).parent)
            lines.extend([f'#EXTINF:{segment.duration or 0},', Path(relative).as_posix()])
        return '\n'.join(lines + ['#EXT-X-ENDLIST', ''])




    def load_tasks(self):
        """从数据库恢复；旧记录首次加载时补充目录/文件存在状态，不扫描分片。"""
        records = self.repository.list_tasks()
        if not self._recovered:
            active = {TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.PAUSING,
                      TaskStatus.MERGING, TaskStatus.RECORDING, TaskStatus.STOPPING}
            def needs_file_state(task):
                return ((isinstance(task, M3U8Task) and 'work_dir_missing' not in task.details.model_fields_set)
                        or (isinstance(task, MP4Task) and 'file_missing' not in task.details.model_fields_set))
            def interrupt(task):
                if task.status in active:
                    task.status, task.last_error = TaskStatus.INTERRUPTED, '上次任务未正常结束，请手动继续。'
                if isinstance(task, M3U8Task) and 'work_dir_missing' not in task.details.model_fields_set:
                    task.details.work_dir_missing = bool(task.details.playlist_path and not (
                        task.save_dir / task.details.playlist_path).parent.is_dir())
                elif isinstance(task, MP4Task) and 'file_missing' not in task.details.model_fields_set:
                    task.details.file_missing = not any((task.save_dir / path).is_file() for path in (
                        task.details.target_path, task.details.temporary_path))
            records = [self.repository.mutate(task.id, interrupt)
                       if task.status in active or needs_file_state(task) else task
                       for task in records]
            self._recovered = True
        return [task for task in records if task is not None]

    def check_files(self, task_id):
        """仅在文件操作前核对当前任务，先保存差异，后续操作失败也不丢失修正。"""
        return self.repository.mutate(task_id, self._reconcile)

    @staticmethod
    def _progress(task):
        completed = [segment for segment in task.details.segments if segment.status == FileStatus.COMPLETED]
        task.progress = TaskProgress(total_segments=len(task.details.segments), completed_segments=len(completed),
                                     downloaded_bytes=sum(segment.size_bytes or 0 for segment in completed))

    @staticmethod
    def _settle(task):
        """无正在运行的请求时确定状态；暂停和中断只在任务尚未完成时保留。"""
        if any(output.status == FileStatus.COMPLETED for output in task.outputs):
            task.status, task.last_error = TaskStatus.COMPLETED, None
        elif task.status == TaskStatus.MERGING:
            return  # 文件完成通知可能晚于用户启动合并，只有合并回调能结束该状态。
        elif task.details.segments and task.progress.completed_segments == task.progress.total_segments:
            if any(output.status == FileStatus.FAILED for output in task.outputs):
                task.status = TaskStatus.FAILED
            else:
                task.status, task.last_error = TaskStatus.WAITING_MERGE, None
        elif task.status not in (TaskStatus.PAUSED, TaskStatus.PAUSING):
            failed = next((segment for segment in task.details.segments if segment.status == FileStatus.FAILED), None)
            if failed:
                task.status, task.last_error = TaskStatus.FAILED, failed.last_error
            elif task.progress.completed_segments and task.status != TaskStatus.INTERRUPTED:
                # 本次选择的分片已下完，不代表整项任务被异常中断。
                task.status, task.last_error = TaskStatus.PARTIAL, None
            else:
                task.status = TaskStatus.INTERRUPTED

    @staticmethod
    def _file_size(path):
        try:
            info = path.stat()
        except FileNotFoundError:
            return None
        return info.st_size if stat.S_ISREG(info.st_mode) else None

    @classmethod
    def _reconcile(cls, task):
        """文件操作前核对已登记路径；不遍历目录，不在启动或刷新时调用。"""
        if not isinstance(task, M3U8Task):
            target_size = cls._file_size(task.save_dir / task.details.target_path)
            if isinstance(task, MP4Task):
                verified = task.details.verified_bytes
                if verified is not None and target_size == verified:
                    task.details.file_missing = False
                    task.status, task.last_error = TaskStatus.COMPLETED, None
                    task.progress = TaskProgress(downloaded_bytes=verified, total_bytes=verified)
                    for output in task.outputs:
                        output.status, output.size_bytes = FileStatus.COMPLETED, verified
                    return
                invalid = target_size is None or (verified is not None and target_size != verified)
            else:
                invalid = target_size is None
            if task.status == TaskStatus.COMPLETED and invalid:
                task.status, task.last_error = TaskStatus.INTERRUPTED, '已完成的视频不存在或大小与记录不符。'
                for output in task.outputs:
                    output.status, output.size_bytes = FileStatus.MISSING, None
                if isinstance(task, MP4Task):
                    task.details.verified_bytes = None
                    task.progress = TaskProgress()
            if isinstance(task, MP4Task) and task.status != TaskStatus.COMPLETED:
                temporary_size = cls._file_size(task.save_dir / task.details.temporary_path)
                task.details.file_missing = target_size is None and temporary_size is None
                done = temporary_size or 0
                total = task.progress.total_bytes
                task.progress = TaskProgress(downloaded_bytes=done,
                    total_bytes=total if total is None or done <= total else None)
            return
        previous = task.status
        active = previous in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.PAUSING, TaskStatus.MERGING)
        if task.details.playlist_path:
            directory = (task.save_dir / task.details.playlist_path).parent
            if directory.is_dir():
                task.details.work_dir_missing = False
            elif not active:
                task.details.work_dir_missing = True
        missing = False
        for segment in task.details.segments:
            path = task.save_dir / segment.relative_path
            size = cls._file_size(path)
            if size and (segment.size_bytes is None or segment.size_bytes == size):
                segment.status, segment.size_bytes, segment.last_error = FileStatus.COMPLETED, size, None
            elif segment.status == FileStatus.COMPLETED or size is not None:
                segment.status, segment.last_error = FileStatus.MISSING, '分片不存在或大小与记录不符'
                segment.duration_status, segment.duration_error = 'pending', None
                missing = True
        for output in task.outputs:
            path = task.save_dir / output.relative_path
            size = cls._file_size(path)
            if size and (output.size_bytes is None or output.size_bytes == size):
                # 正式文件只在转换成功后发布，临时文件不能作为完成依据。
                output.status, output.size_bytes = FileStatus.COMPLETED, size
            elif output.status == FileStatus.COMPLETED:
                output.status, output.size_bytes = FileStatus.MISSING, None
                missing = True
        cls._progress(task)
        if active:
            return  # 恢复当前队列时，不覆盖仍在运行的任务状态。
        if missing:
            task.status, task.last_error = TaskStatus.INTERRUPTED, '部分已下载文件缺失或大小异常，请继续下载。'
        if task.status != TaskStatus.NEW or task.progress.completed_segments or any(
                output.status == FileStatus.COMPLETED for output in task.outputs):
            cls._settle(task)

    def begin_download(self, task_id, sequences):
        """先登记排队，再提交网络请求；只清除本次重试分片的错误。"""
        def change(task):
            self._require_m3u8(task)
            self._reconcile(task)
            selected = set(sequences)
            if not selected or not selected <= {segment.sequence for segment in task.details.segments}:
                raise ValueError('分片序号无效，请刷新后重试。')
            task.details.work_dir_missing = False  # 用户主动重新下载，重新展示待创建的工作目录。
            for segment in task.details.segments:
                if segment.sequence in selected and segment.status != FileStatus.COMPLETED:
                    segment.status, segment.last_error, segment.size_bytes = FileStatus.PENDING, None, None
                    segment.duration_status, segment.duration_error = 'pending', None
            task.status, task.last_error = TaskStatus.QUEUED, None
            self._progress(task)
        return self.repository.mutate(task_id, change)

    def update_segment_info(self, task_id, sequence, source_url, duration=None):
        """原子保存分片地址和时长，不改变本地路径、序号和下载状态。"""
        source_url = source_url.strip()
        if not source_url:
            raise ValueError('下载地址不能为空。')
        if duration is not None:
            try:
                duration = float(duration)
            except (TypeError, ValueError):
                raise ValueError('播放时长必须是大于 0 的秒数，支持小数。') from None
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError('播放时长必须是大于 0 的秒数，支持小数。')
        def change(task):
            self._require_m3u8(task)
            segment = next((s for s in task.details.segments if s.sequence == sequence), None)
            if segment is None:
                raise ValueError('分片不存在，请刷新后重试。')
            if task.status == TaskStatus.MERGING:
                raise ValueError('正在合并的任务不能修改分片信息。')
            if segment.status == FileStatus.COMPLETED and source_url != segment.source_url:
                raise ValueError('已完成的分片不能修改下载地址。')
            segment.source_url = source_url  # 数据模型校验完整 HTTP/HTTPS 地址。
            if duration is not None:
                segment.duration = duration
                # 手动确认的时长无需自动补测；显式“检测分片时长”仍可覆盖。
                segment.duration_status, segment.duration_error = 'detected', None
        record = self.repository.mutate(task_id, change)
        if record is None:
            raise ValueError('任务不存在，请刷新后重试。')
        return record

    def runtime_status(self, task_id, status, *, preserve_terminal=False):
        def change(task):
            if preserve_terminal and task.status in (TaskStatus.MERGING, TaskStatus.COMPLETED, TaskStatus.WAITING_MERGE):
                return  # 后台排队快照不能覆盖已开始的合并或已完成任务。
            task.status = status
        return self.repository.mutate(task_id, change)

    def finish_segment(self, task_id, sequence, filename, success, error=None, *, runtime_status=None):
        """回调身份是任务 UUID + 分片序号，并校验文件路径，绝不按旧行号定位。"""
        def change(task):
            self._require_m3u8(task)
            segment = next((segment for segment in task.details.segments if segment.sequence == sequence), None)
            if segment is None or (task.save_dir / segment.relative_path).resolve() != Path(filename).resolve():
                raise ValueError('分片回调身份或保存路径不匹配。')
            path = task.save_dir / segment.relative_path
            if success and path.is_file():
                segment.status, segment.size_bytes, segment.last_error = FileStatus.COMPLETED, path.stat().st_size, None
            else:
                segment.status, segment.size_bytes = FileStatus.FAILED, None
                segment.last_error = error or '分片下载失败或文件未保存。'
                task.last_error = segment.last_error
            self._progress(task)
            self._settle(task)
            # 后台在同一事务中保存剩余队列状态，避免单片结束让整项任务短暂变成失败/中断。
            if runtime_status is not None and task.status not in (
                    TaskStatus.COMPLETED, TaskStatus.WAITING_MERGE, TaskStatus.MERGING):
                task.status = runtime_status
        return self.repository.mutate(task_id, change)

    def begin_merge(self, task_id):
        record = self.repository.get(task_id)
        if record is None:
            return None
        self._require_m3u8(record)
        self.check_files(task_id)
        def change(task):
            self._require_m3u8(task)
            if not task.details.segments or task.progress.completed_segments != task.progress.total_segments:
                raise ValueError('分片尚未全部下载，不能合并。')
            if task.details.detect_duration and any(s.duration_status == 'pending' for s in task.details.segments):
                raise ValueError('分片时长尚未检测完成，请稍后合并。')
            if any(output.status == FileStatus.COMPLETED for output in task.outputs):
                raise ValueError('视频文件已经存在。')
            task.status, task.last_error = TaskStatus.MERGING, None
            for output in task.outputs:
                if output.kind == 'merged':
                    output.status, output.size_bytes = FileStatus.PENDING, None
        return self.repository.mutate(task_id, change)

    def finish_merge(self, task_id, filename, success):
        def change(task):
            self._require_m3u8(task)
            output = next((output for output in task.outputs if output.kind == 'merged'
                           and (task.save_dir / output.relative_path).resolve() == Path(filename).resolve()), None)
            if output is None:
                raise ValueError('合并回调的输出文件与任务不匹配。')
            if success and Path(filename).is_file():
                output.status, output.size_bytes = FileStatus.COMPLETED, Path(filename).stat().st_size
                task.status, task.last_error = TaskStatus.COMPLETED, None
            else:
                output.status, output.size_bytes = FileStatus.FAILED, None
                task.status, task.last_error = TaskStatus.FAILED, 'FFmpeg 合并失败，请检查分片或重新合并。'
        return self.repository.mutate(task_id, change)

    def detect_durations(self, task_id, stop=None, *, force=False):
        """后台逐片检测，逐片入库；重启仅补做 pending，失败保留原时长。"""
        task = self.repository.get(task_id)
        if not isinstance(task, M3U8Task) or (not force and not task.details.detect_duration):
            return task
        for segment in task.details.segments:
            if stop is not None and stop.is_set():
                return None
            if segment.status != FileStatus.COMPLETED or (not force and segment.duration_status != 'pending'):
                continue
            path = task.save_dir / segment.relative_path
            duration, error = None, None
            try:
                before = path.stat()
                duration = FFmpegConverter.ProbeDuration(path)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                error = str(exc)
            if stop is not None and stop.is_set():
                return None  # 退出后保留 pending，下次启动继续检测。
            def change(current):
                self._require_m3u8(current)
                target = next((s for s in current.details.segments if s.sequence == segment.sequence), None)
                if (target is None or target.status != FileStatus.COMPLETED
                        or target.duration_status != segment.duration_status or target.relative_path != segment.relative_path):
                    return
                # 检测期间文件可能被删除或重新下载，不把旧结果写到新文件上。
                if duration is not None:
                    try:
                        after = path.stat()
                    except OSError:
                        return
                    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                        return
                if duration is not None:
                    target.duration = duration
                target.duration_status = 'failed' if error else 'detected'
                target.duration_error = error
            if self.repository.mutate(task_id, change) is None:
                return None  # 用户删除的任务不会被检测结果重新创建。
        return self.repository.get(task_id)

    @staticmethod
    def _require_m3u8(task):
        if not isinstance(task, M3U8Task):
            raise ValueError('此操作仅适用于 M3U8 分片任务。')

    def interrupt(self, task_id):
        def change(task):
            if task.status in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.PAUSING, TaskStatus.MERGING):
                task.status, task.last_error = TaskStatus.INTERRUPTED, '程序已退出，请手动继续。'
        return self.repository.mutate(task_id, change)

    def write_playlist(self, task_id):
        """从数据库重建本地清单；用户删除派生清单后仍能继续下载或合并。"""
        task = self.repository.get(task_id)
        if not isinstance(task, M3U8Task):
            raise ValueError('任务不存在或不是分片任务。')
        path = task.save_dir / (task.details.playlist_path or 'download.m3u8')
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             delete=False) as output:
                temporary = Path(output.name)
                output.write(self.local_playlist(task))
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return path
