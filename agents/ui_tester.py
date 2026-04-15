from agents.llm import get_llm, node_retry
from graphs.state import AgentState
from tools.browser import ALL_BROWSER_TOOLS
from tools.file_ops import write_file
from prompts.loader import PromptLoader
from langchain_core.messages import ToolMessage

prompt_loader = PromptLoader()
ui_tester_llm = get_llm().bind_tools([*ALL_BROWSER_TOOLS, write_file])


@node_retry()
def ui_tester_node(state: AgentState):
    """UI 测试 agent：执行当前子任务，有 ui_plan 时按计划逐步执行"""
    system_prompt = prompt_loader.load("ui_tester")

    ui_plan = state.get("ui_plan", [])
    ui_step = state.get("ui_step", 0)

    if ui_plan:
        # 多子任务模式：注入当前子任务指令
        total = len(ui_plan)
        current_task = ui_plan[ui_step]

        # 始终注入提交提醒——无论子任务描述是否含表单关键词
        # 原因：planner 写的子任务描述可能用"录入"/"添加"等词，关键词匹配会漏判
        _submit_reminder = (
            "\n\n⚠️ **核心提醒**：如果本子任务涉及任何表单填写，"
            "填写完所有字段后**必须**调用 `browser_submit_and_check` 点击提交/保存按钮，"
            "等到成功提示出现后才能输出子报告。"
            "填写表单 ≠ 完成任务，未点击保存视为子任务未完成。"
        )

        _is_last = (ui_step == total - 1)
        step_prompt = (
            f"\n\n## 当前执行：子任务 [{ui_step + 1}/{total}]\n"
            f"{current_task}\n\n"
            f"{_submit_reminder}"
            + (
                "✅ **这是最后一个子任务**，完成后输出**完整最终报告**（含所有子任务汇总），"
                "调用 write_file 保存报告，最后调用 browser_close 关闭浏览器。"
                if _is_last else
                "完成本子任务后输出**子任务报告**（格式：'### 子任务 [N/total] 完成'），"
                "**不要**输出'整体评估'等最终语气，**不要**调用 browser_close。"
            )
        )
        # 始终用滑动窗口传递近期消息，保证 agent 无论处于哪个子任务边界都能感知当前浏览器状态
        # 原因：子任务边界的"硬重置"会导致 agent 不知道当前页面，重新规划、重新导航
        last_msg = state["messages"][-1]
        recent_msgs = state["messages"][-30:]

        # 子任务切换时（last_msg 是 AI 消息，非工具结果）：注入前序进度 + 历史成果摘要
        if not isinstance(last_msg, ToolMessage) and ui_step > 0:
            completed_list = "\n".join(
                f"  {i + 1}. {ui_plan[i]}" for i in range(ui_step)
            )
            # 从近期消息中提取 AI 报告文本作为成果证据
            # 只取 AI 消息（非工具消息），截取前 300 字，让 agent 看到实际完成了什么
            from langchain_core.messages import AIMessage
            accomplishments = []
            for msg in reversed(state["messages"][-30:]):
                if isinstance(msg, AIMessage) and msg.content and not getattr(msg, "tool_calls", None):
                    accomplishments.append(msg.content[:300])
                    if len(accomplishments) >= 3:  # 最多取最近3条报告
                        break
            accomplishments_text = "\n---\n".join(reversed(accomplishments)) if accomplishments else "（无）"

            _context_reminder = (
                f"\n\n📋 **前序进度**（以下子任务计划已完成）：\n"
                f"{completed_list}\n\n"
                f"📝 **历史成果记录**（上方消息中已记录的实际完成情况）：\n"
                f"{accomplishments_text}\n\n"
                f"## ⚠️ 执行前必须先做完成度判断（禁止跳过此步骤）\n"
                f"1. 阅读上方「历史成果记录」，判断当前子任务要求的功能是否已经完成\n"
                f"2. **判断标准：看成果，不看页面位置**\n"
                f"   - 成果记录里有「XXX成功」「已保存」「PASS」「已创建」→ 视为已完成 → 输出 ⏭️ 跳过\n"
                f"   - 成果记录里没有相关证据 → 才需要实际执行\n"
                f"3. 确实需要执行时：浏览器仍在运行，登录态已存在，**禁止**重新调用 browser_load_state\n"
            )
        elif isinstance(last_msg, ToolMessage):
            # 工具结果回来后：提醒直接继续，不要重新规划
            _context_reminder = (
                "\n\n🔁 **续行提醒**：工具刚返回结果，直接继续下一步。"
                "**禁止**重新调用 browser_load_state / 重新导航回列表页 / 重新打开表单。"
            )
        else:
            _context_reminder = ""

        messages = [
            ("system", system_prompt + step_prompt + _context_reminder),
            *recent_msgs,
        ]
    else:
        # 单任务模式：直接带完整 messages
        messages = [("system", system_prompt), *state["messages"]]

    response = ui_tester_llm.invoke(messages)
    return {"messages": [response]}
