"""SQLite 任务仓库：只保存数据，不扫描目录、不启动下载、不删除下载文件。

每次操作单独建立连接，允许 UI 和工作线程使用同一个仓库实例。
主任务、类型详情、分片和输出在同一事务中写入；读取也使用同一快照。
目前保存完整任务快照；下载调度接入后应合并进度更新，避免逐字节写库。
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from uuid import UUID
from typing import Callable

from src.managers.app_paths import database_path
from src.schemas.task import TASK_ADAPTER, Task


class TaskConflictError(RuntimeError):
    """任务已更新或删除；调用方须重新读取，不能用旧快照覆盖新数据。"""


class TaskDataError(RuntimeError):
    """数据库版本或任务内容异常；不清空数据库，也不静默跳过任务。"""


_DETAIL_TABLES = {'m3u8': 'task_m3u8', 'mp4': 'task_mp4', 'rtmp': 'task_rtmp'}
_COLUMNS = ('id', 'type', 'source_type', 'name', 'source_url', 'save_dir',
            'status', 'created_at', 'updated_at', 'last_error', 'progress')
_SCHEMA = [
    """CREATE TABLE tasks (
        id TEXT PRIMARY KEY NOT NULL,
        type TEXT NOT NULL CHECK(type IN ('m3u8', 'mp4', 'rtmp')),
        source_type TEXT NOT NULL, name TEXT NOT NULL, source_url TEXT,
        save_dir TEXT NOT NULL, status TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        last_error TEXT, progress TEXT NOT NULL
    )""",
    'CREATE INDEX tasks_created ON tasks(created_at, id)',
    'CREATE INDEX tasks_status ON tasks(status)',
    *[f'''CREATE TABLE {table} (
        task_id TEXT PRIMARY KEY NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        data TEXT NOT NULL
    )''' for table in _DETAIL_TABLES.values()],
    """CREATE TABLE task_segments (
        task_id TEXT NOT NULL REFERENCES task_m3u8(task_id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL CHECK(sequence >= 0), data TEXT NOT NULL,
        PRIMARY KEY(task_id, sequence)
    )""",
    """CREATE TABLE task_outputs (
        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        id TEXT NOT NULL, position INTEGER NOT NULL CHECK(position >= 0),
        data TEXT NOT NULL, PRIMARY KEY(task_id, id), UNIQUE(task_id, position)
    )""",
]


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class TaskRepository:
    SCHEMA_VERSION = 1

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else database_path()
        self.path = self.path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # BEGIN IMMEDIATE 使并发首次初始化串行，版本号与建表一同提交。
        with self._transaction(write=True) as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version == 0:
                existing = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if existing:
                    raise TaskDataError('未标记版本的数据库已有数据，拒绝覆盖。')
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute(f'PRAGMA user_version = {self.SCHEMA_VERSION}')
            elif version != self.SCHEMA_VERSION:
                raise TaskDataError(f'不支持任务数据库版本 {version}，需要版本 {self.SCHEMA_VERSION}。')

    @contextmanager
    def _transaction(self, *, write=False):
        """连接不跨线程复用；失败回滚，成功提交，最后务必关闭句柄。"""
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA foreign_keys = ON')
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _validated(task: Task) -> Task:
        # 转成字典后重新验证，不能直接验证模型实例：列表原地修改会绕过赋值校验。
        validated = TASK_ADAPTER.validate_python(task.model_dump(mode='json'))
        ids = [output.id for output in validated.outputs]
        if len(ids) != len(set(ids)):
            raise ValueError('同一任务的输出文件 ID 不能重复。')
        return validated

    @staticmethod
    def _values(task: Task) -> tuple:
        data = task.model_dump(mode='json')
        data['progress'] = _json(data['progress'])
        return tuple(data[column] for column in _COLUMNS)

    @staticmethod
    def _write_children(connection, task: Task):
        data = task.model_dump(mode='json')
        details = data['details']
        segments = details.pop('segments', [])
        table = _DETAIL_TABLES[data['type']]  # 表名只从内部白名单获取。
        task_id = str(task.id)
        connection.execute(f'INSERT INTO {table}(task_id, data) VALUES (?, ?)',
                           (task_id, _json(details)))
        connection.executemany(
            'INSERT INTO task_segments(task_id, sequence, data) VALUES (?, ?, ?)',
            [(task_id, segment['sequence'], _json(segment)) for segment in segments])
        connection.executemany(
            'INSERT INTO task_outputs(task_id, id, position, data) VALUES (?, ?, ?, ?)',
            [(task_id, output['id'], index, _json(output)) for index, output in enumerate(data['outputs'])])

    def create(self, task: Task) -> Task:
        """新增任务，重复 ID 报错；不会覆盖已有任务或创建下载目录。"""
        task = self._validated(task)
        with self._transaction(write=True) as connection:
            placeholders = ','.join('?' for _ in _COLUMNS)
            connection.execute(f"INSERT INTO tasks({','.join(_COLUMNS)}) VALUES ({placeholders})",
                               self._values(task))
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
            assignments = ','.join(f'{column}=?' for column in _COLUMNS[1:])
            cursor = connection.execute(
                f'UPDATE tasks SET {assignments} WHERE id=? AND updated_at=? AND type=? AND created_at=?',
                self._values(saved)[1:] + (str(task.id), expected, task.type.value, original['created_at']))
            if cursor.rowcount != 1:
                raise TaskConflictError('任务不存在、已更新，或修改了不可变字段；请重新读取。')
            table = _DETAIL_TABLES[task.type.value]
            # 删除类型详情会级联删除其分片；以下插入出错时整个事务回滚。
            connection.execute(f'DELETE FROM {table} WHERE task_id=?', (str(task.id),))
            connection.execute('DELETE FROM task_outputs WHERE task_id=?', (str(task.id),))
            self._write_children(connection, saved)
        return saved

    @staticmethod
    def _read(connection, row) -> Task:
        task_id = row['id']
        try:
            data = dict(row)
            data['progress'] = json.loads(data['progress'])
            table = _DETAIL_TABLES[data['type']]
            detail = connection.execute(f'SELECT data FROM {table} WHERE task_id=?', (task_id,)).fetchone()
            if detail is None:
                raise ValueError('缺少任务类型详情')
            data['details'] = json.loads(detail['data'])
            if data['type'] == 'm3u8':
                data['details']['segments'] = [json.loads(item['data']) for item in connection.execute(
                    'SELECT data FROM task_segments WHERE task_id=? ORDER BY sequence', (task_id,))]
            data['outputs'] = [json.loads(item['data']) for item in connection.execute(
                'SELECT data FROM task_outputs WHERE task_id=? ORDER BY position', (task_id,))]
            return TASK_ADAPTER.validate_python(data)
        except (ValueError, KeyError, TypeError) as error:
            raise TaskDataError(f'任务 {task_id} 数据异常，未修改数据库。') from error

    def get(self, task_id: UUID) -> Task | None:
        """按稳定 UUID 读取；不存在返回 None，损坏的数据明确报错。"""
        with self._transaction() as connection:
            row = connection.execute('SELECT * FROM tasks WHERE id=?', (str(task_id),)).fetchone()
            return None if row is None else self._read(connection, row)

    def mutate(self, task_id: UUID, change: Callable[[Task], None]) -> Task | None:
        """事务内读取最新值并应用变化，供并发分片结果使用，不覆盖其他分片的结果。

        与编辑完整快照的 update 不同，此入口只更新变化的分片/输出行。
        change 只能修改传入的数据，不能发起网络请求或再次进入数据库。
        记录已删除时返回 None，迟到的回调不会重新创建任务。
        """
        with self._transaction(write=True) as connection:
            row = connection.execute('SELECT * FROM tasks WHERE id=?', (str(task_id),)).fetchone()
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
            assignments = ','.join(f'{column}=?' for column in _COLUMNS[1:])
            connection.execute(f'UPDATE tasks SET {assignments} WHERE id=?',
                               self._values(task)[1:] + (str(task.id),))
            old = previous.model_dump(mode='json')
            new = task.model_dump(mode='json')
            old_segments = old['details'].pop('segments', [])
            new_segments = new['details'].pop('segments', [])
            if old['details'] != new['details']:
                table = _DETAIL_TABLES[new['type']]
                connection.execute(f'UPDATE {table} SET data=? WHERE task_id=?',
                                   (_json(new['details']), str(task.id)))
            old_by_sequence = {segment['sequence']: segment for segment in old_segments}
            new_by_sequence = {segment['sequence']: segment for segment in new_segments}
            for sequence in old_by_sequence.keys() - new_by_sequence.keys():
                connection.execute('DELETE FROM task_segments WHERE task_id=? AND sequence=?',
                                   (str(task.id), sequence))
            for sequence, segment in new_by_sequence.items():
                if old_by_sequence.get(sequence) != segment:
                    connection.execute('INSERT INTO task_segments(task_id, sequence, data) VALUES (?, ?, ?) '
                                       'ON CONFLICT(task_id, sequence) DO UPDATE SET data=excluded.data',
                                       (str(task.id), sequence, _json(segment)))
            if old['outputs'] != new['outputs']:
                connection.execute('DELETE FROM task_outputs WHERE task_id=?', (str(task.id),))
                connection.executemany('INSERT INTO task_outputs(task_id, id, position, data) VALUES (?, ?, ?, ?)',
                                       [(str(task.id), output['id'], index, _json(output))
                                        for index, output in enumerate(new['outputs'])])
            return task

    def list_tasks(self) -> list[Task]:
        """按创建时间、ID 稳定排序恢复任务；不依赖当前下载目录。"""
        with self._transaction() as connection:
            rows = connection.execute('SELECT * FROM tasks ORDER BY created_at, id').fetchall()
            return [self._read(connection, row) for row in rows]

    def delete(self, task_id: UUID) -> bool:
        """只删除记录及关联详情，返回是否存在；磁盘文件由后续业务层单独处理。"""
        with self._transaction(write=True) as connection:
            return connection.execute('DELETE FROM tasks WHERE id=?', (str(task_id),)).rowcount == 1
