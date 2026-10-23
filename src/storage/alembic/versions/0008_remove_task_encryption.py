"""Remove the unused database task-key table; encrypted containers carry key wraps."""

from alembic import op


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table('task_encryption')


def downgrade():
    raise RuntimeError('不支持数据库降级，请使用匹配版本的程序和备份。')
