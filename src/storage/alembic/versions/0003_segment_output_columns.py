"""将任务进度、类型详情、分片和输出 JSON 展开成列，保留关联数据。

以下校验契约是 v3 的固定快照，不能引用将来可能变化的业务模型。
"""

from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, ClassVar
from urllib.parse import urlsplit
from uuid import UUID

from alembic import op
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator, AwareDatetime
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


class _File(BaseModel):
    model_config = ConfigDict(extra='forbid')
    relative_path: str
    status: Literal['pending', 'completed', 'failed', 'missing'] = 'pending'
    size_bytes: int | None = Field(default=None, strict=True, ge=0)

    @field_validator('relative_path')
    @classmethod
    def relative_file(cls, value):
        value = value.replace('\\', '/')
        if (not value or '\x00' in value or ':' in value or value.endswith('/')
                or PureWindowsPath(value).drive or PurePosixPath(value).is_absolute()
                or any(part in ('', '.', '..') for part in value.split('/'))):
            raise ValueError('必须是任务目录内的相对文件路径')
        return value


class _Segment(_File):
    sequence: int = Field(strict=True, ge=0)
    source_url: str | None = None
    duration: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    duration_status: Literal['pending', 'detected', 'failed'] = 'pending'
    duration_error: str | None = None
    last_error: str | None = None

    @field_validator('source_url')
    @classmethod
    def http_url(cls, value):
        if value is not None:
            parsed = urlsplit(value)
            if (parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname
                    or any(character.isspace() for character in value)):
                raise ValueError('来源地址必须是完整 HTTP/HTTPS 网址')
        return value


class _Output(_File):
    id: UUID
    kind: Literal['download', 'merged', 'recording']


def _copy(connection, old, new, model, identity):
    rows = connection.execute(sa.text(f'SELECT * FROM {old}')).mappings()
    try:
        for batch in rows.partitions(500):
            values = []
            for row in batch:
                try:
                    data = model.model_validate_json(row['data']).model_dump(mode='json')
                    if data[identity] != row[identity]:
                        raise ValueError('JSON 身份与记录主键不一致')
                except (ValidationError, ValueError) as error:
                    raise RuntimeError(f'{old} 的任务 {row["task_id"]}、记录 {row[identity]} 数据异常，停止升级。') from error
                values.append({**data, **{key: row[key] for key in row if key != 'data'}})
            if values:
                connection.execute(new.insert(), values)
    finally:
        rows.close()


def upgrade():
    connection = op.get_bind()
    # 当前版本中这两张表为叶子表。外加的触发器、索引或引用不能在重建时静默丢失。
    extras = connection.execute(sa.text(
        "SELECT name FROM sqlite_master WHERE tbl_name IN ('task_segments', 'task_outputs') "
        "AND (type='trigger' OR (type='index' AND sql IS NOT NULL))")).first()
    inspector = sa.inspect(connection)
    referenced = any(fk['referred_table'] in ('task_segments', 'task_outputs')
                     for table in inspector.get_table_names() for fk in inspector.get_foreign_keys(table))
    if extras or referenced:
        raise RuntimeError('分片或输出表存在额外索引、触发器或外键引用，停止升级以保留自定义结构。')
    segment_table = op.create_table(
        '_task_segments_v3',
        sa.Column('task_id', sa.Text, sa.ForeignKey('task_m3u8.task_id', ondelete='CASCADE'),
                  primary_key=True, nullable=False),
        sa.Column('sequence', sa.Integer, primary_key=True, nullable=False),
        sa.Column('relative_path', sa.Text, nullable=False),
        sa.Column('source_url', sa.Text),
        sa.Column('duration', sa.Float),
        sa.Column('duration_status', sa.Text, nullable=False),
        sa.Column('duration_error', sa.Text),
        sa.Column('status', sa.Text, nullable=False),
        sa.Column('size_bytes', sa.Integer),
        sa.Column('last_error', sa.Text),
        sa.CheckConstraint('sequence >= 0'),
        sa.CheckConstraint('duration >= 0'),
        sa.CheckConstraint('size_bytes >= 0'),
        sa.CheckConstraint("duration_status IN ('pending', 'detected', 'failed')"),
        sa.CheckConstraint("status IN ('pending', 'completed', 'failed', 'missing')"),
    )
    output_table = op.create_table(
        '_task_outputs_v3',
        sa.Column('task_id', sa.Text, sa.ForeignKey('tasks.id', ondelete='CASCADE'),
                  primary_key=True, nullable=False),
        sa.Column('id', sa.Text, primary_key=True, nullable=False),
        sa.Column('position', sa.Integer, nullable=False),
        sa.Column('relative_path', sa.Text, nullable=False),
        sa.Column('kind', sa.Text, nullable=False),
        sa.Column('status', sa.Text, nullable=False),
        sa.Column('size_bytes', sa.Integer),
        sa.UniqueConstraint('task_id', 'position'),
        sa.CheckConstraint('position >= 0'),
        sa.CheckConstraint('size_bytes >= 0'),
        sa.CheckConstraint("kind IN ('download', 'merged', 'recording')"),
        sa.CheckConstraint("status IN ('pending', 'completed', 'failed', 'missing')"),
    )
    _copy(connection, 'task_segments', segment_table, _Segment, 'sequence')
    _copy(connection, 'task_outputs', output_table, _Output, 'id')
    op.drop_table('task_segments')
    op.rename_table('_task_segments_v3', 'task_segments')
    op.drop_table('task_outputs')
    op.rename_table('_task_outputs_v3', 'task_outputs')
    upgrade_remaining()


class _Progress(BaseModel):
    model_config = ConfigDict(extra='forbid')
    completed_segments: int = Field(default=0, strict=True, ge=0)
    total_segments: int = Field(default=0, strict=True, ge=0)
    downloaded_bytes: int = Field(default=0, strict=True, ge=0)
    total_bytes: int | None = Field(default=None, strict=True, ge=0)
    recorded_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)

    @model_validator(mode='after')
    def totals(self):
        if self.completed_segments > self.total_segments or (
                self.total_bytes is not None and self.downloaded_bytes > self.total_bytes):
            raise ValueError('进度超过总量')
        return self


class _Headers(BaseModel):
    model_config = ConfigDict(extra='forbid')
    allowed_headers: ClassVar[tuple] = ('Referer', 'Cookie')
    request_headers: dict[str, str] = Field(default_factory=dict)

    @field_validator('request_headers')
    @classmethod
    def headers(cls, values):
        for name, value in values.items():
            if name not in cls.allowed_headers or any(char in value for char in '\r\n\x00'):
                raise ValueError('请求头非法')
            value.encode('latin-1')
        return values


class _M3U8(_Headers):
    detect_duration: bool = False
    playlist_path: str | None = None
    work_dir_missing: bool = False
    ts_pattern: str | None = None

    @field_validator('playlist_path')
    @classmethod
    def path(cls, value):
        return _File.relative_file(value) if value is not None else None


class _MP4(_Headers):
    allowed_headers: ClassVar[tuple] = ('Referer',)
    target_path: str
    temporary_path: str
    file_missing: bool = False
    etag: str | None = None
    last_modified: str | None = None
    supports_ranges: bool | None = None
    verified_bytes: int | None = Field(default=None, strict=True, ge=0)

    @field_validator('target_path', 'temporary_path')
    @classmethod
    def path(cls, value):
        return _File.relative_file(value)

    @model_validator(mode='after')
    def distinct_paths(self):
        if self.target_path.casefold() == self.temporary_path.casefold():
            raise ValueError('目标文件和临时文件不能相同')
        return self


class _RTMP(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target_path: str
    recording_started_at: AwareDatetime | None = None

    @field_validator('target_path')
    @classmethod
    def path(cls, value):
        return _File.relative_file(value)


def _expand_json(connection, name, old_column, model, columns):
    names = {column['name'] for column in sa.inspect(connection).get_columns(name)}
    if old_column not in names:
        if not {column.name for column in columns} <= names:
            raise RuntimeError(f'{name} 缺少迁移所需字段，停止升级。')
        return  # 已经执行过完整版 v3；v4 衔接不重复修改。
    if names & {column.name for column in columns}:
        raise RuntimeError(f'{name} 同时存在旧 JSON 和新字段，停止升级。')
    if connection.execute(sa.text("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=:name"),
                          {'name': name}).first():
        raise RuntimeError(f'{name} 存在自定义触发器，停止升级以避免回填改变其他记录。')
    for column in columns:
        op.add_column(name, column)
    primary_key = 'id' if name == 'tasks' else 'task_id'
    table = sa.table(name, sa.column(primary_key, sa.Text), sa.column(old_column, sa.Text),
                     *(sa.column(column.name, column.type) for column in columns))
    statement = table.update().where(table.c[primary_key] == sa.bindparam('_record_id')).values(
        {column.name: sa.bindparam(column.name) for column in columns})
    rows = connection.execute(sa.select(table.c[primary_key], table.c[old_column])).mappings()
    try:
        for batch in rows.partitions(500):
            values = []
            for row in batch:
                try:
                    parsed = model.model_validate_json(row[old_column])
                except ValueError as error:
                    raise RuntimeError(f'{name} 的记录 {row[primary_key]} 数据异常，停止升级。') from error
                data = parsed.model_dump(mode='json')
                for flag in ('work_dir_missing', 'file_missing'):
                    if flag in data and flag not in parsed.model_fields_set:
                        data[flag] = None  # 保留“旧记录尚未核对文件”的含义。
                values.append({'_record_id': row[primary_key], **data})
            if values:
                connection.execute(statement, values)
    finally:
        rows.close()
    # 原地删列，父表不重建，避免触发级联删除分片和输出。
    op.drop_column(name, old_column)


def upgrade_remaining():
    connection = op.get_bind()
    version = connection.exec_driver_sql('SELECT sqlite_version()').scalar_one()
    pending = any(old in {column['name'] for column in sa.inspect(connection).get_columns(name)}
                  for name, old in (('tasks', 'progress'), ('task_m3u8', 'data'),
                                    ('task_mp4', 'data'), ('task_rtmp', 'data')))
    if pending and tuple(map(int, version.split('.'))) < (3, 35, 0):
        raise RuntimeError('数据库拆列需要 SQLite 3.35 或更新版本，请更新 Python 的 SQLite 运行库后重试。')
    _expand_json(connection, 'tasks', 'progress', _Progress, [
        sa.Column('total_segments', sa.Integer, sa.CheckConstraint('total_segments >= 0'),
                  nullable=False, server_default='0'),
        sa.Column('completed_segments', sa.Integer,
                  sa.CheckConstraint('completed_segments >= 0 AND completed_segments <= total_segments'),
                  nullable=False, server_default='0'),
        sa.Column('total_bytes', sa.Integer, sa.CheckConstraint('total_bytes >= 0')),
        sa.Column('downloaded_bytes', sa.Integer,
                  sa.CheckConstraint('downloaded_bytes >= 0 AND (total_bytes IS NULL OR downloaded_bytes <= total_bytes)'),
                  nullable=False, server_default='0'),
        sa.Column('recorded_seconds', sa.Float, sa.CheckConstraint('recorded_seconds >= 0'),
                  nullable=False, server_default='0'),
    ])
    _expand_json(connection, 'task_m3u8', 'data', _M3U8, [
        sa.Column('request_headers', sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column('detect_duration', sa.Boolean, nullable=False, server_default='0'),
        sa.Column('playlist_path', sa.Text),
        sa.Column('work_dir_missing', sa.Boolean),
        sa.Column('ts_pattern', sa.Text),
    ])
    _expand_json(connection, 'task_mp4', 'data', _MP4, [
        sa.Column('request_headers', sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column('target_path', sa.Text, nullable=False, server_default=''),
        sa.Column('temporary_path', sa.Text, nullable=False, server_default=''),
        sa.Column('file_missing', sa.Boolean),
        sa.Column('etag', sa.Text),
        sa.Column('last_modified', sa.Text),
        sa.Column('supports_ranges', sa.Boolean),
        sa.Column('verified_bytes', sa.Integer, sa.CheckConstraint('verified_bytes >= 0')),
    ])
    _expand_json(connection, 'task_rtmp', 'data', _RTMP, [
        sa.Column('target_path', sa.Text, nullable=False, server_default=''),
        sa.Column('recording_started_at', sa.Text),
    ])


def downgrade():
    raise RuntimeError('不支持数据库降级，请使用匹配版本的程序和备份。')
