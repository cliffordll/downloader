import time
from pydantic import BaseModel, Field, field_validator
from typing import Union, List, Optional

class FileItem(BaseModel):
    fileName: str
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
    parent: Optional[FileItem]  = None
    childs: List[FileItem]      = []
    download: int               = 0     # 已下载个数

class TreeData(BaseModel):
    items: List[TreeItem]       = []