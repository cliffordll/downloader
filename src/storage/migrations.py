"""程序启动时在同一写事务中升级；仅升级已有数据库时生成一致性备份。"""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from threading import Lock
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util import CommandError
import sqlalchemy as sa

from src.storage.errors import TaskDataError


SCRIPT_LOCATION = Path(__file__).with_name('alembic')
_ALEMBIC_LOCK = Lock()  # Alembic 的 context/op 模块代理不能在同一进程中并行切换。

# 已发布的 v1 结构快照；不能引用会随业务变动的当前 metadata。
_V1_COLUMNS = {
    'tasks': ('id', 'type', 'source_type', 'name', 'source_url', 'save_dir',
              'status', 'created_at', 'updated_at', 'last_error', 'progress'),
    **{name: ('task_id', 'data') for name in ('task_m3u8', 'task_mp4', 'task_rtmp')},
    'task_segments': ('task_id', 'sequence', 'data'),
    'task_outputs': ('task_id', 'id', 'position', 'data'),
}


def _validate_legacy(connection):
    inspector = sa.inspect(connection)
    if set(inspector.get_table_names()) != set(_V1_COLUMNS):
        raise TaskDataError('旧数据库表结构不匹配，未执行升级。')
    for table, names in _V1_COLUMNS.items():
        columns = inspector.get_columns(table)
        if tuple(column['name'] for column in columns) != names:
            raise TaskDataError(f'旧数据库 {table} 字段不匹配，未执行升级。')
        for column in columns:
            integer = column['name'] in ('sequence', 'position')
            expected = sa.Integer if integer else sa.Text
            nullable = table == 'tasks' and column['name'] in ('source_url', 'last_error')
            if not isinstance(column['type'], expected) or column['nullable'] != nullable:
                raise TaskDataError(f'旧数据库 {table} 字段类型或约束不匹配。')
        primary_key = ('id',) if table == 'tasks' else (
            ('task_id', 'sequence') if table == 'task_segments' else (
                ('task_id', 'id') if table == 'task_outputs' else ('task_id',)))
        if tuple(inspector.get_pk_constraint(table)['constrained_columns']) != primary_key:
            raise TaskDataError(f'旧数据库 {table} 主键不匹配。')
        if table != 'tasks':
            parent = 'task_m3u8' if table == 'task_segments' else 'tasks'
            parent_key = 'task_id' if table == 'task_segments' else 'id'
            # 旧表使用列内 REFERENCES；SQLite 反射器可能遗漏其中的 ON DELETE。
            foreign_keys = connection.exec_driver_sql(f'PRAGMA foreign_key_list({table})').mappings().all()
            if len(foreign_keys) != 1 or not any(
                    fk['from'] == 'task_id' and fk['table'] == parent and fk['to'] == parent_key
                    and fk['on_delete'] == 'CASCADE' for fk in foreign_keys):
                raise TaskDataError(f'旧数据库 {table} 外键不匹配。')


def _check_integrity(connection):
    if connection.exec_driver_sql('PRAGMA quick_check').scalars().all() != ['ok']:
        raise TaskDataError('数据库完整性检查失败，未完成升级。')
    if connection.exec_driver_sql('PRAGMA foreign_key_check').first() is not None:
        raise TaskDataError('数据库存在失效的关联记录，未完成升级。')


def _backup(path, version):
    """写锁已由升级连接持有；独立读连接备份已提交快照，包含 WAL 中的数据。"""
    directory = path.parent / 'backups'
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = directory / f'{path.stem}-v{version}-{timestamp}-{uuid4().hex[:8]}.db'
    try:
        with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def upgrade_database(connection, path, *, script_location=None):
    """调用方必须先 BEGIN IMMEDIATE，保证检查、迁移和版本号修改不会交错。"""
    config = Config()
    config.set_main_option('script_location', str(script_location or SCRIPT_LOCATION).replace('%', '%%'))
    config.attributes['connection'] = connection
    try:
        script = ScriptDirectory.from_config(config)
        heads = script.get_heads()
        if len(heads) != 1 or not heads[0].isdigit():
            raise TaskDataError('数据库迁移必须是单条、使用递增数字版本号的链。')
        head = heads[0]
        previous = None
        for revision in reversed(list(script.walk_revisions())):
            if (not revision.revision.isdigit() or revision.revision != f'{int(revision.revision):04d}'
                    or revision.down_revision != previous
                    or (previous is None and revision.revision != '0002')
                    or (previous is not None and int(revision.revision) <= int(previous))):
                raise TaskDataError('数据库迁移链必须从 0002 开始，按数字版本单线递增。')
            previous = revision.revision
        version = connection.exec_driver_sql('PRAGMA user_version').scalar_one()
        if version > int(head):
            raise TaskDataError(f'数据库版本 {version} 高于程序支持版本 {int(head)}，请使用新版程序。')
        tables = set(sa.inspect(connection).get_table_names())
        if version == 0:
            if tables:
                raise TaskDataError('未标记版本的数据库已有数据，拒绝覆盖。')
        elif version == 1:
            _validate_legacy(connection)
            config.attributes['legacy_v1'] = True
        else:
            if 'alembic_version' not in tables:
                raise TaskDataError('数据库缺少迁移版本记录，拒绝升级。')
            revisions = MigrationContext.configure(connection).get_current_heads()
            if len(revisions) != 1 or not revisions[0].isdigit() or int(revisions[0]) != version:
                raise TaskDataError('数据库版本记录不一致，拒绝升级。')
            # 验证当前版本属于此程序的迁移链，未知分支不能自动接管。
            list(script.iterate_revisions(head, revisions[0]))
            script.get_revision(revisions[0])
            if revisions[0] == head:
                return
        if version:
            _check_integrity(connection)
            _backup(path, version)
        with _ALEMBIC_LOCK:
            command.upgrade(config, head)
        _check_integrity(connection)
        connection.exec_driver_sql(f'PRAGMA user_version = {int(head)}')
    except (CommandError, RevisionError) as error:
        raise TaskDataError(f'数据库迁移失败：{error}') from error
    except (TaskDataError, OSError, sqlite3.Error, sa.exc.SQLAlchemyError):
        raise
    except Exception as error:
        raise TaskDataError(f'数据库迁移失败：{error}') from error
