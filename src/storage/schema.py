"""当前数据库表映射；Pydantic 业务模型仍由 schemas/task.py 定义。"""

import sqlalchemy as sa


metadata = sa.MetaData()
tasks = sa.Table(
    'tasks', metadata,
    sa.Column('id', sa.Text, primary_key=True, nullable=False),
    sa.Column('type', sa.Text, nullable=False),
    sa.Column('source_type', sa.Text, nullable=False),
    sa.Column('name', sa.Text, nullable=False),
    sa.Column('source_url', sa.Text),
    sa.Column('save_dir', sa.Text, nullable=False),
    sa.Column('status', sa.Text, nullable=False),
    sa.Column('created_at', sa.Text, nullable=False),
    sa.Column('updated_at', sa.Text, nullable=False),
    sa.Column('last_error', sa.Text),
    sa.Column('progress', sa.Text, nullable=False),
    sa.CheckConstraint("type IN ('m3u8', 'mp4', 'rtmp')"),
)
sa.Index('tasks_created', tasks.c.created_at, tasks.c.id)
sa.Index('tasks_status', tasks.c.status)


def _details(name):
    return sa.Table(
        name, metadata,
        sa.Column('task_id', sa.Text, sa.ForeignKey('tasks.id', ondelete='CASCADE'),
                  primary_key=True, nullable=False),
        sa.Column('data', sa.Text, nullable=False),
    )


detail_tables = {kind: _details('task_' + kind) for kind in ('m3u8', 'mp4', 'rtmp')}
segments = sa.Table(
    'task_segments', metadata,
    sa.Column('task_id', sa.Text, sa.ForeignKey('task_m3u8.task_id', ondelete='CASCADE'),
              primary_key=True, nullable=False),
    sa.Column('sequence', sa.Integer, primary_key=True, nullable=False),
    sa.Column('data', sa.Text, nullable=False),
    sa.CheckConstraint('sequence >= 0'),
)
outputs = sa.Table(
    'task_outputs', metadata,
    sa.Column('task_id', sa.Text, sa.ForeignKey('tasks.id', ondelete='CASCADE'),
              primary_key=True, nullable=False),
    sa.Column('id', sa.Text, primary_key=True, nullable=False),
    sa.Column('position', sa.Integer, nullable=False),
    sa.Column('data', sa.Text, nullable=False),
    sa.UniqueConstraint('task_id', 'position'),
    sa.CheckConstraint('position >= 0'),
)
