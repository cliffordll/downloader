"""新版任务数据契约；不依赖 wx、下载线程或 SQLite。

任务 ID 与列表行号、文件名分离。具体类型用 Pydantic 判别联合解析，
只定义 MP4/RTMP 的数据结构，并不代表已经实现相应下载能力。
"""

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, Union
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class TaskType(str, Enum):
    M3U8 = 'm3u8'  # 同时包含播放列表和 TS 命名规则两种入口。
    MP4 = 'mp4'
    RTMP = 'rtmp'


class SourceType(str, Enum):
    M3U8 = 'm3u8'
    TS_PATTERN = 'ts_pattern'
    DIRECT_URL = 'direct_url'
    LIVE_URL = 'live_url'


class TaskStatus(str, Enum):
    NEW = 'new'
    QUEUED = 'queued'
    DOWNLOADING = 'downloading'
    PAUSING = 'pausing'
    PAUSED = 'paused'
    INTERRUPTED = 'interrupted'
    WAITING_MERGE = 'waiting_merge'
    MERGING = 'merging'
    RECORDING = 'recording'
    STOPPING = 'stopping'
    COMPLETED = 'completed'
    FAILED = 'failed'


class FileStatus(str, Enum):
    PENDING = 'pending'
    COMPLETED = 'completed'
    FAILED = 'failed'
    MISSING = 'missing'


def _relative_file(value: str) -> str:
    """统一保存相对路径，拒绝越界、盘符、UNC 和空文件路径。"""
    value = value.replace('\\', '/')
    if (not value or '\x00' in value or ':' in value or value.endswith('/')
            or PureWindowsPath(value).drive or PurePosixPath(value).is_absolute()
            or any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError('文件路径必须是任务目录内的非空相对路径')
    return value


RelativeFile = Annotated[str, AfterValidator(_relative_file)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
NonNegativeFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]


def _http_url(value: str | None) -> str | None:
    if value is not None:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname or any(c.isspace() for c in value):
            raise ValueError('下载地址必须是完整的 HTTP/HTTPS 网址')
    return value


HttpUrl = Annotated[str | None, AfterValidator(_http_url)]


class TaskModel(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_assignment=True)


class TaskProgress(TaskModel):
    completed_segments: NonNegativeInt = 0
    total_segments: NonNegativeInt = 0
    downloaded_bytes: NonNegativeInt = 0
    total_bytes: NonNegativeInt | None = None  # None 是未知，不能用 0 代替。
    recorded_seconds: NonNegativeFloat = 0

    @model_validator(mode='after')
    def check_totals(self):
        if self.completed_segments > self.total_segments:
            raise ValueError('已完成分片数不能大于总分片数')
        if self.total_bytes is not None and self.downloaded_bytes > self.total_bytes:
            raise ValueError('已下载字节数不能大于总字节数')
        return self


class TaskSegment(TaskModel):
    sequence: NonNegativeInt
    relative_path: RelativeFile
    source_url: HttpUrl = None
    duration: NonNegativeFloat | None = None
    duration_status: Literal['pending', 'detected', 'failed'] = 'pending'
    duration_error: str | None = None
    status: FileStatus = FileStatus.PENDING
    size_bytes: NonNegativeInt | None = None
    last_error: str | None = None


class TaskOutput(TaskModel):
    id: UUID = Field(default_factory=uuid4, frozen=True)
    relative_path: RelativeFile
    kind: Literal['download', 'merged', 'recording']
    status: FileStatus = FileStatus.PENDING
    size_bytes: NonNegativeInt | None = None


class M3U8Details(TaskModel):
    request_headers: dict[str, str] = Field(default_factory=dict)
    detect_duration: bool = False
    playlist_path: RelativeFile | None = None
    ts_pattern: str | None = None
    segments: list[TaskSegment] = Field(default_factory=list)

    @model_validator(mode='after')
    def unique_sequences(self):
        # 只接受表单支持的请求头，禁止换行，避免生成无效 HTTP 请求。
        for name, value in self.request_headers.items():
            if name not in ('Referer', 'Cookie') or any(c in value for c in '\r\n\x00'):
                raise ValueError('请求头仅支持 Referer、Cookie，且不能包含换行')
            try:
                value.encode('latin-1')
            except UnicodeEncodeError:
                raise ValueError('请求头请使用浏览器中的原始值；网址中的中文需进行 URL 编码')
        sequences = [segment.sequence for segment in self.segments]
        if len(sequences) != len(set(sequences)):
            raise ValueError('同一任务内分片序号不能重复；网址允许重复')
        return self


class MP4Details(TaskModel):
    target_path: RelativeFile
    temporary_path: RelativeFile
    etag: str | None = None
    last_modified: str | None = None
    supports_ranges: bool | None = None
    verified_bytes: NonNegativeInt | None = None  # 完整内容已落盘；用于恢复重命名与写库之间的退出。

    @model_validator(mode='after')
    def distinct_paths(self):
        if self.target_path.casefold() == self.temporary_path.casefold():
            raise ValueError('目标文件和临时文件不能相同')
        return self


class RTMPDetails(TaskModel):
    target_path: RelativeFile
    recording_started_at: AwareDatetime | None = None


class TaskBase(TaskModel):
    id: UUID = Field(default_factory=uuid4, frozen=True)
    name: str = Field(min_length=1)
    save_dir: Path
    status: TaskStatus = TaskStatus.NEW
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_error: str | None = None
    progress: TaskProgress = Field(default_factory=TaskProgress)
    outputs: list[TaskOutput] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_common(self):
        if not self.name.strip():
            raise ValueError('任务名称不能为空白')
        if not self.save_dir.is_absolute():
            raise ValueError('任务保存目录必须是绝对路径')
        if self.updated_at < self.created_at:
            raise ValueError('更新时间不能早于创建时间')
        return self


class M3U8Task(TaskBase):
    type: Literal[TaskType.M3U8] = Field(default=TaskType.M3U8, frozen=True)
    source_type: Literal[SourceType.M3U8, SourceType.TS_PATTERN]
    source_url: HttpUrl = None
    details: M3U8Details = Field(default_factory=M3U8Details)

    @model_validator(mode='after')
    def validate_download(self):
        if self.source_type == SourceType.M3U8 and self.source_url is None:
            raise ValueError('从网址创建的 M3U8 任务必须保存来源网址')
        if self.status in (TaskStatus.RECORDING, TaskStatus.STOPPING):
            raise ValueError('分片下载不能使用直播录制状态')
        return self

    @property
    def percent(self) -> int | None:
        return (self.progress.completed_segments * 100 // self.progress.total_segments
                if self.progress.total_segments else None)


class MP4Task(TaskBase):
    type: Literal[TaskType.MP4] = Field(default=TaskType.MP4, frozen=True)
    source_type: Literal[SourceType.DIRECT_URL] = SourceType.DIRECT_URL
    source_url: Annotated[str, AfterValidator(_http_url)]
    details: MP4Details

    @model_validator(mode='after')
    def validate_download(self):
        if self.status in (TaskStatus.WAITING_MERGE, TaskStatus.MERGING, TaskStatus.RECORDING, TaskStatus.STOPPING):
            raise ValueError('MP4 直链任务不能使用合并或直播录制状态')
        return self

    @property
    def percent(self) -> int | None:
        return (self.progress.downloaded_bytes * 100 // self.progress.total_bytes
                if self.progress.total_bytes else None)


class RTMPTask(TaskBase):
    type: Literal[TaskType.RTMP] = Field(default=TaskType.RTMP, frozen=True)
    source_type: Literal[SourceType.LIVE_URL] = SourceType.LIVE_URL
    source_url: str
    details: RTMPDetails

    @model_validator(mode='after')
    def validate_stream_url(self):
        parsed = urlsplit(self.source_url)
        if (parsed.scheme.lower() not in ('rtmp', 'rtmps') or not parsed.hostname
                or any(c.isspace() for c in self.source_url)):
            raise ValueError('直播地址必须是完整的 RTMP/RTMPS 网址')
        if self.status in (TaskStatus.DOWNLOADING, TaskStatus.PAUSING, TaskStatus.PAUSED,
                           TaskStatus.WAITING_MERGE, TaskStatus.MERGING):
            raise ValueError('直播使用录制/停止状态，不使用分片下载、合并或暂停状态')
        return self

    @property
    def percent(self) -> None:
        return None  # 直播没有确定终点，界面应显示录制时长和文件大小。


Task = Annotated[Union[M3U8Task, MP4Task, RTMPTask], Field(discriminator='type')]
TASK_ADAPTER = TypeAdapter(Task)
