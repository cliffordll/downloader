"""连接新建任务、SQLite 和现有树形列表，不通过扫描目录发现任务。"""

from pathlib import Path
import math
import os
import tempfile

from src.managers.m3m8_parser import M3U8Parser
from src.managers.task_repository import TaskRepository
from src.schemas.file_base import FileItem, TreeData, TreeItem
from src.schemas.task import M3U8Task, M3U8Details, SourceType, TaskOutput, TaskProgress, TaskSegment


class TaskService:
    def __init__(self, repository=None):
        self._repository = repository

    @property
    def repository(self):
        # 延迟打开数据库，便于界面测试注入仓库，也避免导入模块就写用户目录。
        if self._repository is None:
            self._repository = TaskRepository()
        return self._repository

    def create_m3u8(self, save_dir, source_url, content, base_path='', *,
                    source_type=SourceType.M3U8, ts_pattern=None):
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

    @staticmethod
    def _file(path, display_name, source_url=''):
        """只检查已记录的确切路径；内部用绝对路径，显示名称单独保存。"""
        item = FileItem(fileName=str(path), displayName=display_name, absUri=source_url or '')
        if path.is_file():
            stat = path.stat()
            item = FileItem(fileName=str(path), displayName=display_name, absUri=source_url or '',
                            fileSize=stat.st_size, modifyAt=stat.st_mtime)
        return item

    def load_tree(self):
        """任务来源只有数据库；文件检查用于显示实际进度，不会导入旧目录。"""
        tree = TreeData()
        for task in self.repository.list_tasks():
            if not isinstance(task, M3U8Task):
                raise ValueError('当前列表尚未接入 MP4/RTMP 任务。')
            parent = self._file(task.save_dir / (task.details.playlist_path or 'download.m3u8'), task.name)
            parent.modifyAt = task.updated_at.astimezone().strftime('%Y-%m-%d %H:%M')
            children = [self._file(task.save_dir / segment.relative_path, segment.relative_path, segment.source_url)
                        for segment in sorted(task.details.segments, key=lambda segment: segment.sequence)]
            outputs = [self._file(task.save_dir / output.relative_path, output.relative_path)
                       for output in task.outputs if (task.save_dir / output.relative_path).is_file()]
            tree.items.append(TreeItem(task_id=task.id, parent=parent, childs=children, outputs=outputs,
                                       download=sum(child.fileSize != '-' for child in children)))
        return tree

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
