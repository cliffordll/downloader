"""存储层异常；保持仓库对外的异常契约。"""


class TaskConflictError(RuntimeError):
    """任务已更新或删除，不能用旧快照覆盖新数据。"""


class TaskDataError(RuntimeError):
    """数据库版本、结构或任务内容异常；不清空数据库。"""
