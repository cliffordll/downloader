-- 已发布的 sqlite3 仓库结构，独立于当前 SQLAlchemy 映射和迁移文件。
CREATE TABLE tasks (
    id TEXT PRIMARY KEY NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('m3u8', 'mp4', 'rtmp')),
    source_type TEXT NOT NULL, name TEXT NOT NULL, source_url TEXT,
    save_dir TEXT NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    last_error TEXT, progress TEXT NOT NULL
);
CREATE INDEX tasks_created ON tasks(created_at, id);
CREATE INDEX tasks_status ON tasks(status);
CREATE TABLE task_m3u8 (
    task_id TEXT PRIMARY KEY NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    data TEXT NOT NULL
);
CREATE TABLE task_mp4 (
    task_id TEXT PRIMARY KEY NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    data TEXT NOT NULL
);
CREATE TABLE task_rtmp (
    task_id TEXT PRIMARY KEY NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    data TEXT NOT NULL
);
CREATE TABLE task_segments (
    task_id TEXT NOT NULL REFERENCES task_m3u8(task_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK(sequence >= 0), data TEXT NOT NULL,
    PRIMARY KEY(task_id, sequence)
);
CREATE TABLE task_outputs (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    id TEXT NOT NULL, position INTEGER NOT NULL CHECK(position >= 0),
    data TEXT NOT NULL, PRIMARY KEY(task_id, id), UNIQUE(task_id, position)
);
PRAGMA user_version = 1;
