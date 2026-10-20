"""Add playlist key entries and per-segment encryption metadata."""

from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    extras = connection.execute(sa.text(
        "SELECT name FROM sqlite_master WHERE tbl_name='task_segments' "
        "AND (type='trigger' OR (type='index' AND sql IS NOT NULL))")).first()
    referenced = any(fk['referred_table'] == 'task_segments'
                     for table in sa.inspect(connection).get_table_names()
                     for fk in sa.inspect(connection).get_foreign_keys(table))
    if extras or referenced:
        raise RuntimeError('分片表存在额外索引、触发器或外键引用，停止升级以保留自定义结构。')

    op.create_table(
        '_task_segments_v4',
        sa.Column('task_id', sa.Text, sa.ForeignKey('task_m3u8.task_id', ondelete='CASCADE'),
                  primary_key=True, nullable=False),
        sa.Column('sequence', sa.Integer, primary_key=True, nullable=False),
        sa.Column('entry_type', sa.Text, nullable=False, server_default='segment'),
        sa.Column('encryption', sa.JSON),
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
        sa.CheckConstraint("entry_type IN ('segment', 'key')"),
        sa.CheckConstraint("status IN ('pending', 'waiting_key', 'downloading', 'decrypting', 'completed', 'failed', 'missing')"),
    )
    connection.execute(sa.text(
        "INSERT INTO _task_segments_v4 "
        "(task_id, sequence, entry_type, encryption, relative_path, source_url, duration, "
        "duration_status, duration_error, status, size_bytes, last_error) "
        "SELECT task_id, sequence, 'segment', NULL, relative_path, source_url, duration, "
        "duration_status, duration_error, status, size_bytes, last_error FROM task_segments"))
    op.drop_table('task_segments')
    op.rename_table('_task_segments_v4', 'task_segments')


def downgrade():
    raise RuntimeError('不支持数据库降级，请使用匹配版本的程序和备份。')
