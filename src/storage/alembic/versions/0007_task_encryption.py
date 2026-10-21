"""Add encrypted-task marker and per-task wrapped file-key metadata."""

from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tasks', sa.Column('encrypted', sa.Boolean,
                                     nullable=False, server_default='0'))
    op.create_table(
        'task_encryption',
        sa.Column('task_id', sa.Text,
                  sa.ForeignKey('tasks.id', ondelete='CASCADE'),
                  primary_key=True, nullable=False),
        sa.Column('format_version', sa.Integer, nullable=False),
        sa.Column('kdf_salt', sa.LargeBinary, nullable=False),
        sa.Column('wrapped_key_nonce', sa.LargeBinary, nullable=False),
        sa.Column('wrapped_key', sa.LargeBinary, nullable=False),
        sa.CheckConstraint('format_version > 0'),
    )


def downgrade():
    raise RuntimeError('不支持数据库降级，请使用匹配版本的程序和备份。')
