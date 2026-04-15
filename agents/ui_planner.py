import json
import re
from agents.llm import get_llm, node_retry
from graphs.state import AgentState
from prompts.loader import PromptLoader

prompt_loader = PromptLoader()
_planner_llm = get_llm()

_JSON_SUFFIX = """

## 输出格式

严格输出 JSON，不要有任何多余文字：
```json
{
  "summary": "整体测试流程简述",
  "sub_tasks": ["子任务1完整描述", "子任务2完整描述", ...]
}
```"""


@node_retry()
def ui_planner_node(state: AgentState):
    """UI 测试规划节点：将复杂测试任务拆解为有序子任务列表"""
    system_prompt = prompt_loader.load("ui_planner") + _JSON_SUFFIX
    messages = [("system", system_prompt), *state["messages"]]
    response = _planner_llm.invoke(messages)
    content = response.content

    # 提取 JSON 块（兼容有无 ```json 包裹）
    match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
    if not match:
        # 尝试直接找 { ... } 块
        match = re.search(r"(\{.*\})", content, re.DOTALL)
    json_str = match.group(1) if match else content.strip()

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        # LLM 输出格式不规范时，降级为单任务模式
        data = {"summary": "直接执行", "sub_tasks": [content.strip()]}

    sub_tasks: list = data.get("sub_tasks", [])
    summary: str = data.get("summary", "")
    plan_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(sub_tasks))
    return {
        "ui_plan": sub_tasks,
        "ui_step": 0,
        "messages": [("assistant", f"UI 测试规划完成：{summary}\n\n子任务：\n{plan_text}")],
    }
