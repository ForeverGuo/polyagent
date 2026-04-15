from agents.llm import get_llm, node_retry
from graphs.state import AgentState
from tools.file_ops import write_file
from tools.web_search import web_search
from langgraph.prebuilt import ToolNode
from prompts.loader import PromptLoader

prompt_loader = PromptLoader()
coder_llm = get_llm().bind_tools([write_file, web_search])

@node_retry()
def coder_node(state: AgentState):
    task_index = state["current_step"]
    task_plan = state.get("task_plan", [])

    # task_plan 为空说明是 tester 发现 Bug 后直接回到 coder 修复
    # 此时从 messages 中取 tester 的 FAIL 报告作为任务描述
    if task_plan and task_index < len(task_plan):
        task_content = task_plan[task_index]
    else:
        last_content = state["messages"][-1].content
        task_content = f"根据以下测试报告修复 Bug：\n{last_content}"

    # 加载并注入任务
    system_prompt = prompt_loader.load(
        "coder",
        current_task=task_content
    )

    prompt = f"当前任务: {task_content}。请直接调用工具生成代码。"
    messages = [
        ("system", system_prompt),
        *state["messages"],
        ("user", prompt)
    ]

    # response = coder_llm.invoke([("system", "你是顶级程序员。")] + state["messages"] + [("user", prompt)])
    response = coder_llm.invoke(messages)
    return {"messages": [response]}