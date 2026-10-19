"""衔接曾运行过“仅拆分片和输出”试用版 v3 的数据库。"""

from alembic import context
from alembic.script import ScriptDirectory


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    # 完整拆列实现在当前未提交的 v3 中；旧试用版 v3 仅需补齐剩余字段。
    ScriptDirectory.from_config(context.config).get_revision('0003').module.upgrade_remaining()


def downgrade():
    raise RuntimeError('不支持数据库降级，请使用匹配版本的程序和备份。')
