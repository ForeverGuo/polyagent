from typing import Annotated, TypedDict, List
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    # 所有的对话历史
    messages: Annotated[list[BaseMessage], add_messages]
    # 架构师生成的任务清单
    task_plan: List[str]
    # 当前处理任务的索引
    current_step: int
    # 用户意图：'code' 或 'chat' 或 'tester'
    intent: str
    # UI 测试专用：planner 拆解的子任务列表
    ui_plan: List[str]
    # UI 测试专用：当前执行的子任务索引
    ui_step: int
    # design 链路专用：后端 API 清单摘要，用于压缩传给前端节点的上下文
    design_context: str
    # 测试修复循环计数，防止无限循环（超过上限强制结束）
    fix_attempts: int