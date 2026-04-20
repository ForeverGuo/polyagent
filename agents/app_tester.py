import os
from agents.llm import get_llm, node_retry
from graphs.state import AgentState
from tools.appium_driver import ALL_APP_TOOLS
from tools.file_ops import write_file, read_file
from prompts.loader import PromptLoader
from langchain_core.messages import ToolMessage, AIMessage

prompt_loader = PromptLoader()
app_tester_llm = get_llm().bind_tools([*ALL_APP_TOOLS, write_file, read_file])


@node_retry()
def app_tester_node(state: AgentState):
    """App 测试 agent：执行当前子任务，有 ui_plan 时按计划逐步执行"""
    system_prompt = prompt_loader.load("app_tester")

    ui_plan = state.get("ui_plan", [])
    ui_step = state.get("ui_step", 0)

    if ui_plan:
        total = len(ui_plan)
        current_task = ui_plan[ui_step]
        _is_last = (ui_step == total - 1)
        _remaining = total - ui_step - 1

        step_prompt = (
            f"\n\n## 当前执行：子任务 [{ui_step + 1}/{total}]\n"
            f"{current_task}\n\n"
            + (
                "✅ **这是最后一个子任务**，完成后输出**完整最终报告**，"
                "调用 write_file 保存报告，最后调用 app_close 关闭 Appium session。"
                if _is_last else
                f"⛔ **禁止调用 app_close** — 后面还有 {_remaining} 个子任务，session 必须保持开启。\n"
                "完成本子任务后输出**子任务报告**（格式：'### 子任务 [N/total] 完成'）。"
            )
        )

        last_msg = state["messages"][-1]
        _window = int(os.environ.get("UI_MSG_WINDOW", "10"))
        recent_msgs = list(state["messages"][-_window:])
        while recent_msgs and isinstance(recent_msgs[0], ToolMessage):
            recent_msgs.pop(0)

        if not isinstance(last_msg, ToolMessage) and ui_step > 0:
            completed_list = "\n".join(
                f"  {i + 1}. {ui_plan[i]}" for i in range(ui_step)
            )
            accomplishments = []
            for msg in reversed(state["messages"][-_window:]):
                if isinstance(msg, AIMessage) and msg.content and not getattr(msg, "tool_calls", None):
                    accomplishments.append(msg.content[:300])
                    if len(accomplishments) >= 3:
                        break
            acc_text = "\n---\n".join(reversed(accomplishments)) if accomplishments else "（无）"

            _context_reminder = (
                f"\n\n📋 **前序进度**：\n{completed_list}\n\n"
                f"📝 **历史成果记录**：\n{acc_text}\n\n"
                f"## ⚠️ 执行前先判断完成度\n"
                f"- 成果记录里有「成功」「已保存」「PASS」→ 视为已完成 → 输出 ⏭️ 跳过\n"
                f"- 无相关证据 → 才需要实际执行\n"
                f"- **禁止**重新启动 App 或调用 app_launch（session 保持中）\n"
            )
        elif isinstance(last_msg, ToolMessage):
            _context_reminder = (
                "\n\n🔁 **续行提醒**：工具刚返回结果，直接继续下一步。"
                "**禁止**重新启动 App / 重新导航回首页。"
            )
        else:
            _context_reminder = ""

        messages = [
            ("system", system_prompt + step_prompt + _context_reminder),
            *recent_msgs,
        ]
    else:
        messages = [("system", system_prompt), *state["messages"]]

    response = app_tester_llm.invoke(messages)
    return {"messages": [response]}
