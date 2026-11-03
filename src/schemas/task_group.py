"""Persisted metadata for a download task group."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TaskGroup(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    folder_path: Path
    position: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value or any(char in value for char in '\\/:*?"<>|\x00'):
            raise ValueError('分组名称不能为空或包含文件夹名称不支持的字符。')
        return value

    @model_validator(mode='after')
    def validate_paths_and_times(self):
        if not self.folder_path.is_absolute():
            raise ValueError('分组目录必须是绝对路径。')
        if self.updated_at < self.created_at:
            raise ValueError('更新时间不能早于创建时间。')
        return self
