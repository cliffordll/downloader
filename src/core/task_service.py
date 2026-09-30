"""管理任务创建、持久化状态和恢复，不依赖窗口或列表模型。"""

from pathlib import Path
import math
import os
import tempfile
import subprocess
from src.core.duration_probe import probe_duration

from src.core.parsers.m3u8_parser import M3U8Parser
from src.storage.task_repository import TaskRepository
from src.schemas.task import M3U8Task, M3U8Details, SourceType, TaskOutput, TaskProgress, TaskSegment, TaskStatus, FileStatus


class TaskService:
    def __init__(self, repository=None):
        self._repository = repository
        self._recovered = False

    @property
    def repository(self):
        # 延迟打开数据库，便于界面测试注入仓库，也避免导入模块就写用户目录。
        if self._repository is None:
            self._repository = TaskRepository()
        return self._repository

    def create_m3u8(self, save_dir, source_url, content, base_path='', *,
                    source_type=SourceType.M3U8, ts_pattern=None, detect_duration=False, request_headers=None):
        """两种添加入口统一入库。目录必须是新目录，避免同名任务覆盖文件。

        分片按序号保存，不把带查询参数的 URL 当作文件名；重复 URL 或不同
        服务器上的同名分片各有独立路径。download.m3u8 是本地播放/合并用的
        派生文件，不承担任务持久化职责，也不会用于重新发现任务。
        """
        directory = Path(save_dir).expanduser()
        if not directory.is_absolute():
            raise ValueError('任务保存目录必须是绝对路径。')
        directory = directory.resolve()
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
        task = M3U8Task(name=directory.name, save_dir=directory, source_type=source_type,
                        source_url=source_url or None,
                        details=M3U8Details(playlist_path='download.m3u8', ts_pattern=ts_pattern,
                                            detect_duration=detect_duration,
                                            request_headers=request_headers or {},
                                            segments=segments),
                        progress=TaskProgress(total_segments=len(segments)),
                        outputs=[TaskOutput(relative_path='output.mp4', kind='merged')])
        # 已入库任务即使目录被手动删除，也不能被新任务复用。
        for existing in self.repository.list_tasks():
            saved = existing.save_dir.resolve()
            if saved == directory or saved in directory.parents or directory in saved.parents:
                raise ValueError('保存目录与已有任务重叠，请换一个独立的任务子目录。')
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError as error:
            raise ValueError('保存目录已存在，请换一个新的任务子目录，避免覆盖文件。') from error
        playlist = directory / task.details.playlist_path
        try:
            playlist.write_text(self.local_playlist(task), encoding='utf-8')
            return self.repository.create(task)
        except Exception:
            # 只清理此次新建的派生文件/空目录，不递归删除任何下载文件。
            playlist.unlink(missing_ok=True)
            try:
                directory.rmdir()
            except OSError:
                pass
            raise

    @staticmethod
    def local_playlist(task):
        maximum = max((segment.duration or 0 for segment in task.details.segments), default=0)
        lines = ['#EXTM3U', '#EXT-X-VERSION:3', f'#EXT-X-TARGETDURATION:{max(1, math.ceil(maximum))}',
                 '#EXT-X-MEDIA-SEQUENCE:0', '#EXT-X-PLAYLIST-TYPE:VOD']
        for segment in sorted(task.details.segments, key=lambda segment: segment.sequence):
            lines.extend([f'#EXTINF:{segment.duration or 0},', segment.relative_path])
        return '\n'.join(lines + ['#EXT-X-ENDLIST', ''])




    def load_tasks(self):
        """首次读取恢复中断状态，普通刷新只核对已登记文件，返回领域任务记录。"""
        recovering = not self._recovered
        records = []
        for original in self.repository.list_tasks():
            task = self.repository.mutate(original.id, lambda task: self._reconcile(task, recovering))
            if task is not None:
                records.append(task)
        self._recovered = True
        return records

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
            task.status = TaskStatus.FAILED if failed else TaskStatus.INTERRUPTED
            task.last_error = failed.last_error if failed else task.last_error

    @classmethod
    def _reconcile(cls, task, recovering):
        if not isinstance(task, M3U8Task):
            # 尚未接入单文件引擎：保留已登记的字节/时长，不凭文件存在推断完成。
            if recovering and task.status in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING,
                    TaskStatus.PAUSING, TaskStatus.RECORDING, TaskStatus.STOPPING):
                task.status, task.last_error = TaskStatus.INTERRUPTED, '上次任务未正常结束。'
            if task.status == TaskStatus.COMPLETED and not (task.save_dir / task.details.target_path).is_file():
                task.status, task.last_error = TaskStatus.INTERRUPTED, '已完成的视频文件不存在。'
                for output in task.outputs:
                    if not (task.save_dir / output.relative_path).is_file():
                        output.status, output.size_bytes = FileStatus.MISSING, None
            return
        previous = task.status
        previous_error = task.last_error
        active = previous in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.PAUSING, TaskStatus.MERGING)
        missing = False
        for segment in task.details.segments:
            path = task.save_dir / segment.relative_path
            if path.is_file():
                segment.status, segment.size_bytes, segment.last_error = FileStatus.COMPLETED, path.stat().st_size, None
            elif segment.status == FileStatus.COMPLETED:
                segment.status, segment.size_bytes, segment.last_error = FileStatus.MISSING, None, '已下载分片不存在'
                segment.duration_status, segment.duration_error = 'pending', None
                missing = True
        for output in task.outputs:
            path = task.save_dir / output.relative_path
            if path.is_file():
                # FFmpeg 现在只在成功后发布正式文件；临时文件不能作为完成依据。
                output.status, output.size_bytes = FileStatus.COMPLETED, path.stat().st_size
            elif output.status == FileStatus.COMPLETED:
                output.status, output.size_bytes = FileStatus.MISSING, None
                missing = True
        cls._progress(task)
        if active and not recovering:
            return  # 普通刷新不能把本次会话的活动状态改成中断。
        if active or missing:
            task.status = TaskStatus.INTERRUPTED
            task.last_error = '上次任务未正常结束，请手动继续。' if active else '部分已下载文件不存在，请继续下载。'
        if task.status != TaskStatus.NEW or task.progress.completed_segments or any(
                output.status == FileStatus.COMPLETED for output in task.outputs):
            cls._settle(task)
        if recovering and previous == TaskStatus.MERGING and task.status != TaskStatus.COMPLETED:
            task.status, task.last_error = TaskStatus.INTERRUPTED, '上次合并未完成，请重新转 MP4。'
        elif recovering and active and task.status not in (TaskStatus.COMPLETED, TaskStatus.WAITING_MERGE):
            task.status, task.last_error = TaskStatus.INTERRUPTED, '上次任务未正常结束，请手动继续。'
        elif previous == TaskStatus.INTERRUPTED and task.status != TaskStatus.COMPLETED:
            task.status, task.last_error = TaskStatus.INTERRUPTED, previous_error

    def begin_download(self, task_id, sequences):
        """先登记排队，再提交网络请求；只清除本次重试分片的错误。"""
        def change(task):
            self._require_m3u8(task)
            self._reconcile(task, False)
            selected = set(sequences)
            if not selected or not selected <= {segment.sequence for segment in task.details.segments}:
                raise ValueError('分片序号无效，请刷新后重试。')
            for segment in task.details.segments:
                if segment.sequence in selected and segment.status != FileStatus.COMPLETED:
                    segment.status, segment.last_error, segment.size_bytes = FileStatus.PENDING, None, None
                    segment.duration_status, segment.duration_error = 'pending', None
            task.status, task.last_error = TaskStatus.QUEUED, None
            self._progress(task)
        return self.repository.mutate(task_id, change)

    def runtime_status(self, task_id, status):
        def change(task):
            task.status = status
        return self.repository.mutate(task_id, change)

    def finish_segment(self, task_id, sequence, filename, success, error=None):
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
        return self.repository.mutate(task_id, change)

    def begin_merge(self, task_id):
        def change(task):
            self._require_m3u8(task)
            self._reconcile(task, False)
            if task.details.detect_duration and any(s.duration_status == 'pending' for s in task.details.segments):
                raise ValueError('分片时长尚未检测完成，请稍后合并。')
            if not task.details.segments or task.progress.completed_segments != task.progress.total_segments:
                raise ValueError('分片尚未全部下载，不能合并。')
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

    def detect_durations(self, task_id, stop=None):
        """后台逐片检测，逐片入库；重启仅补做 pending，失败保留原时长。"""
        task = self.repository.get(task_id)
        if not isinstance(task, M3U8Task) or not task.details.detect_duration:
            return task
        for segment in task.details.segments:
            if stop is not None and stop.is_set():
                return None
            if segment.status != FileStatus.COMPLETED or segment.duration_status != 'pending':
                continue
            path = task.save_dir / segment.relative_path
            duration, error = None, None
            try:
                before = path.stat()
                duration = probe_duration(path)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                error = str(exc)
            if stop is not None and stop.is_set():
                return None  # 退出后保留 pending，下次启动继续检测。
            def change(current):
                self._require_m3u8(current)
                target = next((s for s in current.details.segments if s.sequence == segment.sequence), None)
                if (target is None or target.status != FileStatus.COMPLETED
                        or target.duration_status != 'pending' or target.relative_path != segment.relative_path):
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
