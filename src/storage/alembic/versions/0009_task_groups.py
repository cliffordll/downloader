"""Add task groups and nullable task membership."""

from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'task_groups',
        sa.Column('id', sa.Text, primary_key=True, nullable=False),
        sa.Column('name', sa.Text, nullable=False),
        sa.Column('folder_path', sa.Text, nullable=False, unique=True),
        sa.Column('position', sa.Integer, nullable=False, server_default='0'),
        sa.Column('created_at', sa.Text, nullable=False),
        sa.Column('updated_at', sa.Text, nullable=False),
        sa.CheckConstraint('length(trim(name)) > 0'),
        sa.CheckConstraint('position >= 0'),
    )
    # SQLite accepts a nullable REFERENCES clause in ADD COLUMN, while Alembic
    # otherwise tries to ALTER a constraint (unsupported) or rebuilds tasks,
    # which can cascade-delete task detail rows.
    op.execute(sa.text(
        'ALTER TABLE tasks ADD COLUMN group_id TEXT '
        'REFERENCES task_groups(id) ON DELETE SET NULL'))
    op.create_index('tasks_group_created', 'tasks', ['group_id', 'created_at', 'id'])


def downgrade():
    raise RuntimeError('不支持数据库降级，请使用匹配版本的程序和备份。')
