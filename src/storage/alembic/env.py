"""升级连接和事务由应用提供，禁止另开连接或自行提交。"""

from alembic import context

from src.storage.schema import metadata


context.configure(
    connection=context.config.attributes['connection'],
    target_metadata=metadata,
    transactional_ddl=True,
)
with context.begin_transaction():
    context.run_migrations()
