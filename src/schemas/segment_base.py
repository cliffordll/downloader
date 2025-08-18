from pydantic import BaseModel, Field, field_validator
from typing import Union, List, Optional

class SegmentItem(BaseModel):
    name: str               = Field(..., deprecated="文件名称")
    uri: str                = Field(..., description="段uri,也可以是绝对路径")
    absUri: Optional[str]   = Field(default=None, description="base_uri填写时")