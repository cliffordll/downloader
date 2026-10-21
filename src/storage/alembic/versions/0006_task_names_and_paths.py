"""Separate task names from output paths and make M3U8 paths work-dir relative."""

from pathlib import PurePosixPath

from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def _without_prefix(value, prefix):
    if not value or not prefix:
        return value
    normalized = PurePosixPath(value).as_posix()
    prefix = PurePosixPath(prefix).as_posix().rstrip('/')
    if normalized == prefix:
        return PurePosixPath(normalized).name
    if normalized.startswith(prefix + '/'):
        return normalized[len(prefix) + 1:]
    return normalized


def upgrade():
    connection = op.get_bind()

    # Existing task names doubled as MP4 filenames; use the M3U8 work folder as
    # the canonical name when available so the physical folder need not move.
    rows = connection.execute(sa.text(
        "SELECT t.id, t.name, d.playlist_path FROM tasks t "
        "JOIN task_m3u8 d ON d.task_id=t.id")).mappings().all()
    for row in rows:
        old_name = row['name']
        playlist = row['playlist_path']
        parent = PurePosixPath(playlist).parent if playlist else PurePosixPath('.')
        if len(parent.parts) > 1:
            raise RuntimeError(f"任务 {row['id']} 的播放列表路径包含多级工作目录，停止自动迁移。")
        old_work_dir = '' if str(parent) == '.' else parent.as_posix()
        name = parent.name if old_work_dir else PurePosixPath(old_name).stem
        if not name:
            name = PurePosixPath(old_name).stem or 'video'
        new_playlist = _without_prefix(playlist, old_work_dir)
        connection.execute(sa.text(
            "UPDATE tasks SET name=:name WHERE id=:id"), {'name': name, 'id': row['id']})
        connection.execute(sa.text(
            "UPDATE task_m3u8 SET playlist_path=:path WHERE task_id=:id"),
            {'path': new_playlist, 'id': row['id']})
        segments = connection.execute(sa.text(
            "SELECT sequence, relative_path FROM task_segments WHERE task_id=:id"),
            {'id': row['id']}).mappings().all()
        for segment in segments:
            relative = _without_prefix(segment['relative_path'], old_work_dir)
            if relative != segment['relative_path']:
                connection.execute(sa.text(
                    "UPDATE task_segments SET relative_path=:path "
                    "WHERE task_id=:id AND sequence=:sequence"),
                    {'path': relative, 'id': row['id'], 'sequence': segment['sequence']})

    # The prior details tables duplicated final output paths already stored in
    # task_outputs. Preserve the extensionless logical base in tasks.name.
    for table in ('task_mp4', 'task_rtmp'):
        rows = connection.execute(sa.text(
            f"SELECT t.id, t.name, d.target_path FROM tasks t JOIN {table} d ON d.task_id=t.id")).mappings().all()
        for row in rows:
            target = row['target_path']
            name = PurePosixPath(target).stem if target else PurePosixPath(row['name']).stem
            connection.execute(sa.text("UPDATE tasks SET name=:name WHERE id=:id"),
                               {'name': name or PurePosixPath(row['name']).stem, 'id': row['id']})

    op.add_column('task_m3u8', sa.Column('detecting', sa.Boolean,
                                         nullable=False, server_default='0'))
    op.drop_column('task_mp4', 'target_path')
    op.drop_column('task_rtmp', 'target_path')
    op.alter_column('task_segments', 'relative_path', new_column_name='segment_path')
    op.alter_column('task_outputs', 'relative_path', new_column_name='output_path')


def downgrade():
    raise RuntimeError('不支持数据库降级，请使用匹配版本的程序和备份。')
