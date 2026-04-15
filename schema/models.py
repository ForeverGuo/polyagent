from pydantic import BaseModel, Field
from typing import List

class TaskPlan(BaseModel):
    steps: List[str] = Field(description="详细的任务步骤清单")
    project_summary: str = Field(description="项目核心功能简述")