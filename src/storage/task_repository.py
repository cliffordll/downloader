"""SQLAlchemy Core 任务仓库：只保存数据，不操作下载文件。

每次操作单独建立连接，允许 UI 和工作线程使用同一个仓库实例。
主任务、类型详情、分片和输出在同一事务中写入；读取也使用同一快照。
启动时自动迁移数据库；下载回调只写变化的分片和输出。
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from uuid import UUID
from typing import Callable

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.pool import NullPool

from src.core.app_paths import database_path
from src.schemas.task import TASK_ADAPTER, Task
from src.storage.errors import TaskConflictError, TaskDataError
from src.storage.migrations import upgrade_database
from src.storage.schema import tasks, detail_tables, segments, outputs


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class TaskRepository:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else database_path()
        self.path = self.path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 每次操作独立连接；NullPool 不持有句柄，兼容线程和 Windows 文件管理。
        self.engine = sa.create_engine(
            sa.URL.create('sqlite+pysqlite', database=str(self.path)),
            connect_args={'timeout': 10, 'isolation_level': None}, poolclass=NullPool,
        )

        @sa.event.listens_for(self.engine, 'connect')
        def configure(connection, _):
            cursor = connection.cursor()
            try:
                cursor.execute('PRAGMA foreign_keys = ON')
            finally:
                cursor.close()

        with self._transaction(write=True) as connection:
            upgrade_database(connection, self.path)

    @contextmanager
    def _transaction(self, *, write=False):
        """连接不跨线程复用；失败回滚，成功提交，最后务必关闭句柄。"""
        try:
            with self.engine.connect() as connection:
                # 显式 BEGIN 也覆盖 DDL，避免 SQLite 驱动的隐式事务遗漏迁移回滚。
                connection.exec_driver_sql('BEGIN IMMEDIATE' if write else 'BEGIN')
                try:
                    yield connection
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        except DBAPIError as error:
            # 上层现有的 sqlite3.Error 处理保持有效，不让 ORM 异常漏出界面和线程。
            if isinstance(error.orig, sqlite3.Error):
                raise error.orig from error
            raise TaskDataError(f'数据库操作失败：{error}') from error
        except SQLAlchemyError as error:
            raise TaskDataError(f'数据库操作失败：{error}') from error

    @staticmethod
    def _validated(task: Task) -> Task:
        # 转成字典后重新验证，不能直接验证模型实例：列表原地修改会绕过赋值校验。
        validated = TASK_ADAPTER.validate_python(task.model_dump(mode='json'))
        ids = [output.id for output in validated.outputs]
        if len(ids) != len(set(ids)):
            raise ValueError('同一任务的输出文件 ID 不能重复。')
        return validated

    @staticmethod
    def _values(task: Task) -> dict:
        data = task.model_dump(mode='json')
        data['progress'] = _json(data['progress'])
        return {column.name: data[column.name] for column in tasks.columns}

    @staticmethod
    def _write_children(connection, task: Task):
        data = task.model_dump(mode='json')
        details = data['details']
        segment_data = details.pop('segments', [])
        table = detail_tables[data['type']]
        task_id = str(task.id)
        connection.execute(table.insert().values(task_id=task_id, data=_json(details)))
        if segment_data:
            connection.execute(segments.insert(), [
                {'task_id': task_id, 'sequence': segment['sequence'], 'data': _json(segment)}
                for segment in segment_data])
        if data['outputs']:
            connection.execute(outputs.insert(), [
                {'task_id': task_id, 'id': output['id'], 'position': index, 'data': _json(output)}
                for index, output in enumerate(data['outputs'])])

    def create(self, task: Task) -> Task:
        """新增任务，重复 ID 报错；不会覆盖已有任务或创建下载目录。"""
        task = self._validated(task)
        with self._transaction(write=True) as connection:
            connection.execute(tasks.insert().values(**self._values(task)))
            self._write_children(connection, task)
        return task

    def create_allocated(self, build: Callable[[list[Task]], Task]) -> Task:
        """在写事务内分配文件名并入库，避免多个创建入口同时占用同一路径。"""
        with self._transaction(write=True) as connection:
            existing = [self._read(connection, row)
                        for row in connection.execute(sa.select(tasks)).mappings()]
            task = self._validated(build(existing))
            connection.execute(tasks.insert().values(**self._values(task)))
            self._write_children(connection, task)
        return task

    def update(self, task: Task) -> Task:
        """保存修改并返回新快照；下次更新必须使用返回值中的 updated_at。

        updated_at 同时作为并发校验标记：UI 和后台若读取了同一版任务，
        后保存的一方得到冲突异常，避免旧进度覆盖暂停状态或已完成分片。
        ID、类型和创建时间在持久化后不可变；不存在的任务不会被意外重新创建。
        """
        task = self._validated(task)
        original = task.model_dump(mode='json')
        expected = original['updated_at']
        saved = task.model_copy(update={
            'updated_at': max(datetime.now(timezone.utc), task.updated_at + timedelta(microseconds=1))})
        with self._transaction(write=True) as connection:
            values = self._values(saved)
            values.pop('id')
            cursor = connection.execute(
                tasks.update().where(tasks.c.id == str(task.id), tasks.c.updated_at == expected,
                                     tasks.c.type == task.type.value,
                                     tasks.c.created_at == original['created_at']).values(**values))
            if cursor.rowcount != 1:
                raise TaskConflictError('任务不存在、已更新，或修改了不可变字段；请重新读取。')
            table = detail_tables[task.type.value]
            # 删除类型详情会级联删除其分片；以下插入出错时整个事务回滚。
            connection.execute(table.delete().where(table.c.task_id == str(task.id)))
            connection.execute(outputs.delete().where(outputs.c.task_id == str(task.id)))
            self._write_children(connection, saved)
        return saved

    @staticmethod
    def _read(connection, row) -> Task:
        task_id = row['id']
        try:
            data = dict(row)
            data['progress'] = json.loads(data['progress'])
            table = detail_tables[data['type']]
            detail = connection.execute(sa.select(table.c.data).where(table.c.task_id == task_id)).mappings().first()
            if detail is None:
                raise ValueError('缺少任务类型详情')
            data['details'] = json.loads(detail['data'])
            if data['type'] == 'm3u8':
                data['details']['segments'] = [json.loads(value) for value in connection.execute(
                    sa.select(segments.c.data).where(segments.c.task_id == task_id)
                    .order_by(segments.c.sequence)).scalars()]
            data['outputs'] = [json.loads(value) for value in connection.execute(
                sa.select(outputs.c.data).where(outputs.c.task_id == task_id)
                .order_by(outputs.c.position)).scalars()]
            return TASK_ADAPTER.validate_python(data)
        except (ValueError, KeyError, TypeError) as error:
            raise TaskDataError(f'任务 {task_id} 数据异常，未修改数据库。') from error

    def get(self, task_id: UUID) -> Task | None:
        """按稳定 UUID 读取；不存在返回 None，损坏的数据明确报错。"""
        with self._transaction() as connection:
            row = connection.execute(sa.select(tasks).where(tasks.c.id == str(task_id))).mappings().first()
            return None if row is None else self._read(connection, row)

    def mutate(self, task_id: UUID, change: Callable[[Task], None]) -> Task | None:
        """事务内读取最新值并应用变化，供并发分片结果使用，不覆盖其他分片的结果。

        与编辑完整快照的 update 不同，此入口只更新变化的分片/输出行。
        change 只能修改传入的数据，不能发起网络请求或再次进入数据库。
        记录已删除时返回 None，迟到的回调不会重新创建任务。
        """
        with self._transaction(write=True) as connection:
            row = connection.execute(sa.select(tasks).where(tasks.c.id == str(task_id))).mappings().first()
            if row is None:
                return None
            previous = self._read(connection, row)
            task = previous.model_copy(deep=True)
            change(task)
            task = self._validated(task)
            if (task.id, task.type, task.created_at) != (previous.id, previous.type, previous.created_at):
                raise ValueError('不能修改任务身份、类型或创建时间。')
            if task == previous:
                return previous
            task.updated_at = max(datetime.now(timezone.utc), previous.updated_at + timedelta(microseconds=1))
            values = self._values(task)
            values.pop('id')
            connection.execute(tasks.update().where(tasks.c.id == str(task.id)).values(**values))
            old = previous.model_dump(mode='json')
            new = task.model_dump(mode='json')
            old_segments = old['details'].pop('segments', [])
            new_segments = new['details'].pop('segments', [])
            if old['details'] != new['details']:
                table = detail_tables[new['type']]
                connection.execute(table.update().where(table.c.task_id == str(task.id))
                                   .values(data=_json(new['details'])))
            old_by_sequence = {segment['sequence']: segment for segment in old_segments}
            new_by_sequence = {segment['sequence']: segment for segment in new_segments}
            for sequence in old_by_sequence.keys() - new_by_sequence.keys():
                connection.execute(segments.delete().where(segments.c.task_id == str(task.id),
                                                           segments.c.sequence == sequence))
            for sequence, segment in new_by_sequence.items():
                if old_by_sequence.get(sequence) != segment:
                    statement = sqlite_insert(segments).values(
                        task_id=str(task.id), sequence=sequence, data=_json(segment))
                    connection.execute(statement.on_conflict_do_update(
                        index_elements=['task_id', 'sequence'], set_={'data': statement.excluded.data}))
            if old['outputs'] != new['outputs']:
                connection.execute(outputs.delete().where(outputs.c.task_id == str(task.id)))
                if new['outputs']:
                    connection.execute(outputs.insert(), [
                        {'task_id': str(task.id), 'id': output['id'], 'position': index, 'data': _json(output)}
                        for index, output in enumerate(new['outputs'])])
            return task

    def list_tasks(self) -> list[Task]:
        """按创建时间、ID 稳定排序恢复任务；不依赖当前下载目录。"""
        with self._transaction() as connection:
            rows = connection.execute(sa.select(tasks).order_by(tasks.c.created_at, tasks.c.id)).mappings()
            return [self._read(connection, row) for row in rows]

    def delete(self, task_id: UUID) -> bool:
        """只删除记录及关联详情，返回是否存在；磁盘文件由后续业务层单独处理。"""
        with self._transaction(write=True) as connection:
            return connection.execute(tasks.delete().where(tasks.c.id == str(task_id))).rowcount == 1
