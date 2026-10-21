"""当前数据库表映射；Pydantic 业务模型仍由 schemas/task.py 定义。"""

import sqlalchemy as sa


metadata = sa.MetaData()
PROGRESS_COLUMNS = ('completed_segments', 'total_segments', 'downloaded_bytes', 'total_bytes', 'recorded_seconds')
tasks = sa.Table(
    'tasks', metadata,
    sa.Column('id', sa.Text, primary_key=True, nullable=False),
    sa.Column('type', sa.Text, nullable=False),
    sa.Column('source_type', sa.Text, nullable=False),
    sa.Column('name', sa.Text, nullable=False),
    sa.Column('encrypted', sa.Boolean, nullable=False, server_default='0'),
    sa.Column('source_url', sa.Text),
    sa.Column('save_dir', sa.Text, nullable=False),
    sa.Column('status', sa.Text, nullable=False),
    sa.Column('created_at', sa.Text, nullable=False),
    sa.Column('updated_at', sa.Text, nullable=False),
    sa.Column('last_error', sa.Text),
    sa.Column('total_segments', sa.Integer, nullable=False, server_default='0'),
    sa.Column('completed_segments', sa.Integer, nullable=False, server_default='0'),
    sa.Column('total_bytes', sa.Integer),
    sa.Column('downloaded_bytes', sa.Integer, nullable=False, server_default='0'),
    sa.Column('recorded_seconds', sa.Float, nullable=False, server_default='0'),
    sa.CheckConstraint("type IN ('m3u8', 'mp4', 'rtmp')"),
    sa.CheckConstraint('total_segments >= 0'),
    sa.CheckConstraint('completed_segments >= 0 AND completed_segments <= total_segments'),
    sa.CheckConstraint('total_bytes >= 0'),
    sa.CheckConstraint('downloaded_bytes >= 0 AND (total_bytes IS NULL OR downloaded_bytes <= total_bytes)'),
    sa.CheckConstraint('recorded_seconds >= 0'),
)
sa.Index('tasks_created', tasks.c.created_at, tasks.c.id)
sa.Index('tasks_status', tasks.c.status)


def _details(name, *columns):
    return sa.Table(
        name, metadata,
        sa.Column('task_id', sa.Text, sa.ForeignKey('tasks.id', ondelete='CASCADE'),
                  primary_key=True, nullable=False),
        *columns,
    )


detail_tables = {
    'm3u8': _details(
        'task_m3u8',
        sa.Column('request_headers', sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column('detect_duration', sa.Boolean, nullable=False, server_default='0'),
        sa.Column('playlist_path', sa.Text),
        sa.Column('detecting', sa.Boolean, nullable=False, server_default='0'),
        sa.Column('work_dir_missing', sa.Boolean),  # NULL 为旧记录尚未核对，读取时使用模型默认值。
        sa.Column('ts_pattern', sa.Text),
    ),
    'mp4': _details(
        'task_mp4',
        sa.Column('request_headers', sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column('temporary_path', sa.Text, nullable=False, server_default=''),
        sa.Column('file_missing', sa.Boolean),
        sa.Column('etag', sa.Text),
        sa.Column('last_modified', sa.Text),
        sa.Column('supports_ranges', sa.Boolean),
        sa.Column('verified_bytes', sa.Integer),
        sa.CheckConstraint('verified_bytes >= 0'),
    ),
    'rtmp': _details(
        'task_rtmp',
        sa.Column('recording_started_at', sa.Text),
    ),
}
segments = sa.Table(
    'task_segments', metadata,
    sa.Column('task_id', sa.Text, sa.ForeignKey('task_m3u8.task_id', ondelete='CASCADE'),
              primary_key=True, nullable=False),
    sa.Column('sequence', sa.Integer, primary_key=True, nullable=False),
    sa.Column('entry_type', sa.Text, nullable=False, server_default='segment'),
    sa.Column('encryption', sa.JSON),
    sa.Column('segment_path', sa.Text, nullable=False),
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
    sa.CheckConstraint("entry_type IN ('segment', 'key')"),
    sa.CheckConstraint("status IN ('pending', 'waiting_key', 'downloading', 'decrypting', 'completed', 'failed', 'missing')"),
)
outputs = sa.Table(
    'task_outputs', metadata,
    sa.Column('task_id', sa.Text, sa.ForeignKey('tasks.id', ondelete='CASCADE'),
              primary_key=True, nullable=False),
    sa.Column('id', sa.Text, primary_key=True, nullable=False),
    sa.Column('position', sa.Integer, nullable=False),
    sa.Column('output_path', sa.Text, nullable=False),
    sa.Column('kind', sa.Text, nullable=False),
    sa.Column('status', sa.Text, nullable=False),
    sa.Column('size_bytes', sa.Integer),
    sa.UniqueConstraint('task_id', 'position'),
    sa.CheckConstraint('position >= 0'),
    sa.CheckConstraint('size_bytes >= 0'),
    sa.CheckConstraint("kind IN ('download', 'merged', 'recording')"),
    sa.CheckConstraint("status IN ('pending', 'completed', 'failed', 'missing')"),
)
task_encryption = sa.Table(
    'task_encryption', metadata,
    sa.Column('task_id', sa.Text, sa.ForeignKey('tasks.id', ondelete='CASCADE'),
              primary_key=True, nullable=False),
    sa.Column('format_version', sa.Integer, nullable=False),
    sa.Column('kdf_salt', sa.LargeBinary, nullable=False),
    sa.Column('wrapped_key_nonce', sa.LargeBinary, nullable=False),
    sa.Column('wrapped_key', sa.LargeBinary, nullable=False),
    sa.CheckConstraint('format_version > 0'),
)
