from agents.llm import get_llm, node_retry
from schema.models import TaskPlan
from graphs.state import AgentState
from prompts.loader import PromptLoader
import os

prompt_loader = PromptLoader()
llm = get_llm()
@node_retry()
def architect_node(state: AgentState):
    # 1. 准备动态信息（例如当前目录结构）
    current_structure = ", ".join(os.listdir("."))
    
    # 2. 从文件加载 Prompt，并注入变量
    system_prompt = prompt_loader.load(
        "architect", 
        project_structure=current_structure
    )
    
    # 3. 构建消息发送给 LLM
    messages = [
        ("system", system_prompt),
        *state["messages"] # 加上之前的对话历史
    ]
    planner = llm.with_structured_output(TaskPlan)
    # 这里可以从 prompts/ 目录读取系统提示词
    # result = planner.invoke([("system", "你是首席架构师，请拆解用户需求。")] + state["messages"])
    result = planner.invoke(messages)
    return {
        "task_plan": result.steps,
        "current_step": 0,
        "messages": [("assistant", f"规划完成：{result.project_summary}")]
    }