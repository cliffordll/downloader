"""接管原有 user_version=1 数据库，或创建同结构的新数据库。

这是发布后的历史快照，不应随当前 schema.py 修改。
"""

from alembic import context, op
import sqlalchemy as sa


revision = '0002'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    if context.config.attributes.get('legacy_v1'):
        return  # 应用已核验原表；只由 Alembic 增加版本记录。
    op.create_table(
        'tasks',
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
    op.create_index('tasks_created', 'tasks', ['created_at', 'id'])
    op.create_index('tasks_status', 'tasks', ['status'])
    for name in ('task_m3u8', 'task_mp4', 'task_rtmp'):
        op.create_table(
            name,
            sa.Column('task_id', sa.Text, sa.ForeignKey('tasks.id', ondelete='CASCADE'),
                      primary_key=True, nullable=False),
            sa.Column('data', sa.Text, nullable=False),
        )
    op.create_table(
        'task_segments',
        sa.Column('task_id', sa.Text, sa.ForeignKey('task_m3u8.task_id', ondelete='CASCADE'),
                  primary_key=True, nullable=False),
        sa.Column('sequence', sa.Integer, primary_key=True, nullable=False),
        sa.Column('data', sa.Text, nullable=False),
        sa.CheckConstraint('sequence >= 0'),
    )
    op.create_table(
        'task_outputs',
        sa.Column('task_id', sa.Text, sa.ForeignKey('tasks.id', ondelete='CASCADE'),
                  primary_key=True, nullable=False),
        sa.Column('id', sa.Text, primary_key=True, nullable=False),
        sa.Column('position', sa.Integer, nullable=False),
        sa.Column('data', sa.Text, nullable=False),
        sa.UniqueConstraint('task_id', 'position'),
        sa.CheckConstraint('position >= 0'),
    )


def downgrade():
    raise RuntimeError('应用不支持数据库降级，请使用匹配版本的程序和备份。')
