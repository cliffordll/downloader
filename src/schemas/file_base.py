import time
from pydantic import BaseModel, Field, field_validator
from typing import Union, List, Optional
from uuid import UUID
from pathlib import Path
from src.schemas.task import FileStatus, TaskStatus, TaskType, TaskProgress

class FileItem(BaseModel):
    fileName: str
    displayName: str = ''  # 界面名称与内部绝对路径分离，切换默认目录不影响已有任务。
    sequence: Optional[int] = None  # 分片稳定序号，不包含输出文件造成的界面行偏移。
    status: FileStatus = FileStatus.PENDING
    fileSize: Union[str, int]           = Field(default='-')
    modifyAt: Union[str, int, float]    = Field(default="----:--:-- --:--")
    absUri: str                         = Field(default="")

    @field_validator('fileSize')
    def filesizeCheck(cls, v, values):
        """自动计算文件大小，并转换为 KB/MB/GB/TB 格式"""
        if isinstance(v, int):
            sizeBytes = v
            # 定义单位
            units = ['B', 'KB', 'MB', 'GB', 'TB']
            index = 0
            # 循环计算合适的单位
            while sizeBytes >= 1024 and index < len(units)-1:
                sizeBytes /= 1024.0
                index += 1
            # 保留 2 位小数
            return f"{sizeBytes:.2f} {units[index]}"
        return v
    
    @field_validator('modifyAt')
    def modifyAtCheck(cls, v, values):
        if isinstance(v, (int, float)):
            localTime = time.localtime(v)
            # 自定义格式（例如：YYYY-MM-DD HH:MM:SS）
            return time.strftime("%Y-%m-%d %H:%M", localTime)
        return v

class TreeItem(BaseModel):
    task_type: TaskType = TaskType.M3U8
    save_dir: Optional[Path] = None
    progress: TaskProgress = Field(default_factory=TaskProgress)
    task_id: Optional[UUID] = None  # 数据库身份，不使用行号作为持久化标识。
    task_status: Optional[TaskStatus] = None
    last_error: Optional[str] = None
    parent: Optional[FileItem]  = None
    outputs: List[FileItem]     = []    # 输出文件，mp4，支持多个
    childs: List[FileItem]      = []
    # total: int                  = 0     # 总TS个数
    download: int               = 0     # 已下载TS个数

class TreeData(BaseModel):
    items: List[TreeItem]       = []
