"""
Multi-Agent 工作流定义。

整体架构：
  用户输入 → router（意图识别）→ 按意图分派到对应 Agent 链路

支持五条独立链路：
  ┌─ code    → architect → coder ⇄ tools ⇄ search_tools → tester → [coder 修复循环]
  ├─ chat    → chat ⇄ search_tools
  ├─ test    → tester ⇄ test_tools → [coder 修复循环]
  ├─ swagger → swagger_agent ⇄ swagger_tools → tester
  ├─ ui      → ui_planner → ui_tester ⇄ ui_tools → [ui_advance 子任务循环]
  └─ design  → designer_plan ⇄ search_tools
              → designer_sql ⇄ design_sql_tools
              → designer_backend ⇄ design_backend_tools
              → designer_frontend ⇄ design_frontend_tools → END

节点类型说明：
  - Agent 节点：调用 LLM 生成回复或工具调用指令
  - ToolNode：执行工具调用，返回 ToolMessage，不调用 LLM
  - 辅助节点：纯 state 变更（如 advance、ui_advance），不调用 LLM
"""
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from agents.architect import architect_node
from agents.coder import coder_node
from agents.chat import chat_node
from agents.tester import tester_node
from agents.swagger_agent import swagger_agent_node
from agents.ui_tester import ui_tester_node
from agents.ui_planner import ui_planner_node
from agents.designer import (
    designer_plan_node,
    designer_sql_node,
    designer_backend_node,
    designer_context_node,
    designer_frontend_node,
)
from tools.file_ops import write_file
from tools.browser import ALL_BROWSER_TOOLS
from tools.web_search import web_search
from tools.terminal import run_command
from tools.swagger_parser import fetch_swagger
from tools.file_reader import read_spec_file
from graphs.state import AgentState
from agents.llm import get_llm, node_retry


# ─────────────────────────────────────────────────────────────────────────────
# 辅助节点函数
# 这些节点不调用 LLM，只做简单的 state 变更或意图识别
# ─────────────────────────────────────────────────────────────────────────────

# router 使用独立的 LLM 实例，避免与其他 agent 共享上下文
_router_llm = get_llm()


@node_retry()
def router_node(state: AgentState):
    """
    入口路由节点：识别用户意图，决定进入哪条处理链路。

    意图优先级（从高到低）：
      ui > swagger > design > test > code > chat

    优先级原因：
      - ui/swagger 是专用词，不易误判，放前面
      - test 可能在代码需求描述中出现（如"写完后要测试"），放在 code 之前避免误入 test 链路
      - chat 是兜底选项
    """
    user_msg = state["messages"][-1].content
    prompt = (
        "判断用户的意图，只返回一个单词：\n"
        "- 如果用户想要测试网站UI、测试网页功能、浏览器自动化测试、打开某个网址进行测试，返回：ui\n"
        "- 如果用户提供了 Swagger/OpenAPI 文档地址、txt 文件、md 文件，或想根据接口描述/文档生成测试用例，返回：swagger\n"
        "- 如果用户想要进行产品设计、系统设计、架构设计、数据库设计、出方案、出表结构、出SQL，返回：design\n"
        "- 如果用户想要测试代码、检测文件、运行测试用例、检查 Bug，返回：test\n"
        "- 如果用户想要生成代码、写程序、创建文件、开发项目，返回：code\n"
        "- 其他情况（聊天、提问、讨论等），返回：chat\n"
        f"用户输入：{user_msg}"
    )
    result = _router_llm.invoke([("user", prompt)])
    content = result.content.lower()
    if "ui" in content:
        intent = "ui"
    elif "swagger" in content:
        intent = "swagger"
    elif "design" in content:
        intent = "design"
    elif "test" in content:
        intent = "test"
    elif "code" in content:
        intent = "code"
    else:
        intent = "chat"
    return {"intent": intent}


def advance_step_node(state: AgentState):
    """
    代码任务步进节点：将 current_step +1，驱动 coder 处理 task_plan 的下一步。

    用于 code 链路的多步骤循环：
      coder 完成当前步骤 → tools 写文件 → advance(+1) → coder 处理下一步
    """
    return {"current_step": state["current_step"] + 1}


def increment_fix_attempts_node(state: AgentState):
    """
    修复计数步进节点：tester 判定 FAIL 后、进入 coder 修复前执行。
    将 fix_attempts +1，用于在 route_after_tester 中判断是否超出上限。
    """
    return {"fix_attempts": state.get("fix_attempts", 0) + 1}


def reset_fix_attempts_node(state: AgentState):
    """
    重置修复计数节点：首次进入 tester 验收时执行，清零 fix_attempts。
    确保每轮完整的写代码→测试→修复流程计数独立。
    """
    return {"fix_attempts": 0}


def ui_advance_node(state: AgentState):
    """
    UI 子任务步进节点：将 ui_step +1，驱动 ui_tester 执行下一个子任务。

    用于 ui 链路的多子任务循环：
      ui_tester 完成当前子任务 → ui_advance(+1) → ui_tester 执行下一子任务
    """
    return {"ui_step": state.get("ui_step", 0) + 1}


# ─────────────────────────────────────────────────────────────────────────────
# 路由函数
# 每个路由函数对应一个 Agent 节点的出口，决定下一步去哪里
# 路由函数只读取 state，不修改 state，不调用 LLM
# ─────────────────────────────────────────────────────────────────────────────

def route_by_intent(state: AgentState):
    """
    router 节点出口：按 intent 字段分流到对应链路。

    返回值对应 add_conditional_edges 的目标节点 key：
      "code"    → architect（先规划再写代码）
      "chat"    → chat（对话 / 问答）
      "test"    → tester（直接运行测试）
      "swagger" → swagger_agent（解析接口文档生成测试用例）
      "ui"      → ui_planner（先规划 UI 子任务再执行）
      "design"  → designer（产品方案 → 架构 → 数据库设计 → SQL DDL）
    """
    return state["intent"]


def route_after_chat(state: AgentState):
    """
    chat 节点出口：判断是否需要联网搜索。

    chat agent 绑定了 web_search 工具，当回答需要实时信息时会产生 tool_calls。
      有 tool_calls → search_tools（执行搜索）→ 回 chat 生成最终回复
      无 tool_calls → END（直接结束，回复已完成）
    """
    if state["messages"][-1].tool_calls:
        return "search_tools"
    return END


def route_after_search(state: AgentState):
    """
    search_tools 节点出口：搜索结果交回原始调用方。

    search_tools 被 chat、coder、designer_plan 三条链路共用，通过 intent 区分回哪里：
      intent == "code"   → coder（搜索是为了辅助写代码）
      intent == "design" → designer_plan（搜索是为了参考行业方案，只有 plan 阶段会搜）
      其他               → chat（搜索是为了回答问题）
    """
    if state["intent"] == "code":
        return "coder"
    if state["intent"] == "design":
        return "designer_plan"
    return "chat"


def route_after_coder(state: AgentState):
    """
    coder 节点出口：根据 coder 的行为决定下一步。

    三种情况：
      1. coder 调用了 web_search   → search_tools（先去搜索，结果回来后继续写代码）
      2. coder 调用了 write_file   → tools（执行写文件操作）
      3. coder 没有工具调用：
         a. 还有未完成的步骤       → advance（步进索引，继续写下一步）
         b. 所有步骤已完成         → END（写代码流程结束）

    注意：tester 验收失败后会回到 coder 修复，修复完成后同样走这里的逻辑。
    """
    last_msg = state["messages"][-1]
    if last_msg.tool_calls:
        tool_name = last_msg.tool_calls[0]["name"]
        return "search_tools" if tool_name == "web_search" else "tools"
    if state["current_step"] < len(state["task_plan"]) - 1:
        return "advance"
    return END


def route_after_tools(state: AgentState):
    """
    tools 节点出口（write_file 执行后）：判断代码任务是否全部完成。

    task_plan 是 architect 拆解的步骤列表，current_step 是当前索引：
      还有下一步 → advance（步进后继续让 coder 写下一个文件）
      全部完成   → reset_fix_attempts（重置修复计数后交 tester 验收）
    """
    if state["current_step"] < len(state["task_plan"]) - 1:
        return "advance"
    return "reset_fix_attempts"


_MAX_FIX_ATTEMPTS = 3


def route_after_tester(state: AgentState):
    """
    tester 节点出口：处理测试结果，支持自动修复循环（最多 3 次）。

    五种情况：
      1. tester 调用了 run_command  → test_tools（执行终端命令跑测试）
      2. swagger 链路               → END（只做语法检查，不进入 coder 修复循环）
      3. 已达修复上限               → END（强制结束，避免死循环）
      4. tester 判定 ##FAIL##       → increment_fix_attempts → coder 修复
      5. tester 判定 ##PASS## 或无结论 → END
    """
    last_msg = state["messages"][-1]
    if last_msg.tool_calls:
        return "test_tools"
    # swagger 链路只做语法检查，不进入 coder 修复循环
    if state.get("intent") == "swagger":
        return END
    if state.get("fix_attempts", 0) >= _MAX_FIX_ATTEMPTS:
        return END
    if "##FAIL##" in last_msg.content:
        return "increment_fix_attempts"
    return END


def route_after_swagger(state: AgentState):
    """
    swagger_agent 节点出口：区分"还在读文档"和"已生成测试代码"两个阶段。

    swagger_agent 绑定了三个工具：
      - fetch_swagger   （获取 Swagger/OpenAPI JSON）
      - read_spec_file  （读取 txt/md 接口描述文件）
      - write_file      （写出生成的测试代码）

    有 tool_calls → swagger_tools（继续执行工具，可能是读文档或写文件）
    无 tool_calls → tester（测试代码已生成完毕，交给 tester 运行验收）
    """
    last_msg = state["messages"][-1]
    if last_msg.tool_calls:
        return "swagger_tools"
    return "tester"


def route_after_designer_plan(state: AgentState):
    """
    designer_plan 节点出口（阶段一-三）。

    两种情况：
      1. 调用了 web_search → search_tools（搜索行业最佳实践）→ 回 designer_plan 继续
      2. 无 tool_calls     → designer_sql（进入 SQL DDL 生成阶段）
    """
    last_msg = state["messages"][-1]
    if last_msg.tool_calls:
        return "search_tools"
    return "designer_sql"


def route_after_designer_sql(state: AgentState):
    """
    designer_sql 节点出口（阶段四）。

    两种情况：
      1. 调用了 write_file → design_sql_tools（保存 .sql / .md 文件）→ 回 designer_sql
      2. 无 tool_calls     → designer_backend（进入服务端代码生成阶段）
    """
    last_msg = state["messages"][-1]
    if last_msg.tool_calls:
        return "design_sql_tools"
    return "designer_backend"


def route_after_designer_backend(state: AgentState):
    """
    designer_backend 节点出口（阶段五）。

    两种情况：
      1. 调用了 write_file → design_backend_tools（逐文件保存后端代码）→ 回 designer_backend
      2. 无 tool_calls     → designer_context（提取 API 清单，压缩上下文）
    """
    last_msg = state["messages"][-1]
    if last_msg.tool_calls:
        return "design_backend_tools"
    return "designer_context"


def route_after_designer_frontend(state: AgentState):
    """
    designer_frontend 节点出口（阶段六）。

    两种情况：
      1. 调用了 write_file → design_frontend_tools（逐文件保存前端代码）→ 回 designer_frontend
      2. 无 tool_calls     → END（全部完成）
    """
    last_msg = state["messages"][-1]
    if last_msg.tool_calls:
        return "design_frontend_tools"
    return END


def route_after_ui_tools(state: AgentState):
    """
    ui_tools 节点出口：区分"浏览器操作"和"任务收尾"两类工具的后续动作。

    三种情况：
      1. 最后执行的是 browser_close          → END（浏览器已关闭，任务彻底结束）
      2. 最后执行的是 write_file 且已是最后一个子任务（或无子任务） → END
         避免写完报告后又重新进 ui_tester 导致重复测试
      3. 其他（浏览器操作完成）              → ui_tester（继续分析结果）
    """
    from langchain_core.messages import ToolMessage
    last_msg = state["messages"][-1]
    if isinstance(last_msg, ToolMessage):
        tool_name = getattr(last_msg, "name", "")
        if tool_name == "browser_close":
            return END
        if tool_name == "write_file":
            ui_plan = state.get("ui_plan", [])
            ui_step = state.get("ui_step", 0)
            if not ui_plan or ui_step >= len(ui_plan) - 1:
                return END
    return "ui_tester"


def route_after_ui_tester(state: AgentState):
    """
    ui_tester 节点出口：处理浏览器操作循环和多子任务步进。

    三种情况：
      1. ui_tester 有 tool_calls    → ui_tools（执行浏览器操作）
         → 操作结果返回后再次进入 ui_tester 分析（每个操作都是一轮 LLM 调用）
      2. 无 tool_calls 且还有子任务 → ui_advance（当前子任务完成，步进到下一个）
         → ui_advance(+1) → ui_tester 执行下一子任务
      3. 无 tool_calls 且无子任务   → END（所有子任务完成，测试结束）

    多子任务模式由 ui_planner 拆解任务后激活（ui_plan 非空）。
    单任务模式下 ui_plan 为空，直接在此节点完成后 END。
    """
    last_msg = state["messages"][-1]
    if last_msg.tool_calls:
        return "ui_tools"
    # 多子任务模式：检查是否还有下一个子任务
    ui_plan = state.get("ui_plan", [])
    ui_step = state.get("ui_step", 0)
    if ui_plan and ui_step < len(ui_plan) - 1:
        return "ui_advance"
    return END


# ─────────────────────────────────────────────────────────────────────────────
# 构建 StateGraph
# ─────────────────────────────────────────────────────────────────────────────

workflow = StateGraph(AgentState)

# ── 注册所有节点 ──────────────────────────────────────────────────────────────
#
# Agent 节点（调用 LLM）：
workflow.add_node("router",        router_node)        # 意图识别，分派入口
workflow.add_node("chat",          chat_node)          # 通用对话 / 问答
workflow.add_node("architect",     architect_node)     # 代码任务规划，输出 task_plan
workflow.add_node("coder",         coder_node)         # 逐步骤写代码
workflow.add_node("tester",        tester_node)        # 验收测试，分析结果，判断 PASS/FAIL
workflow.add_node("swagger_agent", swagger_agent_node) # 解析接口文档，生成 pytest 测试用例
workflow.add_node("ui_planner",    ui_planner_node)    # UI 测试任务规划，拆解为子任务列表
workflow.add_node("ui_tester",     ui_tester_node)     # UI 测试执行，控制浏览器逐步操作
workflow.add_node("designer_plan",     designer_plan_node)     # 设计阶段一-三：产品方案 + 架构 + DB 设计
workflow.add_node("designer_sql",      designer_sql_node)      # 设计阶段四：SQL DDL
workflow.add_node("designer_backend",  designer_backend_node)  # 设计阶段五：服务端代码
workflow.add_node("designer_context",  designer_context_node)  # 阶段五→六衔接：提取 API 清单，压缩上下文
workflow.add_node("designer_frontend", designer_frontend_node) # 设计阶段六：前端代码

# ToolNode（执行工具调用，不调用 LLM）：
workflow.add_node("search_tools",  ToolNode([web_search]))                              # 联网搜索
workflow.add_node("tools",         ToolNode([write_file]))                              # 写代码文件
workflow.add_node("test_tools",    ToolNode([run_command]))                             # 执行终端命令
workflow.add_node("swagger_tools", ToolNode([fetch_swagger, read_spec_file, write_file])) # Swagger 相关操作
workflow.add_node("ui_tools",      ToolNode([*ALL_BROWSER_TOOLS, write_file]))          # 浏览器操作 + 保存报告
workflow.add_node("design_sql_tools",      ToolNode([write_file]))  # 保存 .sql 和 .md 文件
workflow.add_node("design_backend_tools",  ToolNode([write_file]))  # 保存服务端代码文件
workflow.add_node("design_frontend_tools", ToolNode([write_file]))  # 保存前端代码文件

# 辅助节点（纯 state 变更，不调用 LLM）：
workflow.add_node("advance",               advance_step_node)          # 代码任务步进：current_step + 1
workflow.add_node("ui_advance",            ui_advance_node)            # UI 子任务步进：ui_step + 1
workflow.add_node("increment_fix_attempts", increment_fix_attempts_node)  # 修复计数 +1
workflow.add_node("reset_fix_attempts",    reset_fix_attempts_node)   # 修复计数清零


# ── 固定边（无条件跳转）────────────────────────────────────────────────────────

# 全局入口
workflow.add_edge(START,                    "router")

# code 链路
workflow.add_edge("architect",              "coder")    # 规划完成 → 开始写第一步代码
workflow.add_edge("advance",               "coder")    # 步进后 → 写下一步代码
workflow.add_edge("reset_fix_attempts",    "tester")   # 计数清零 → tester 验收
workflow.add_edge("increment_fix_attempts", "coder")   # 计数递增 → coder 修复
workflow.add_edge("test_tools",            "tester")   # 测试命令执行完 → 回 tester 分析输出

# swagger 链路
workflow.add_edge("swagger_tools", "swagger_agent") # 工具执行完 → 回 swagger_agent 继续决策

# design 链路：各阶段 ToolNode 完成后回到对应节点
workflow.add_edge("design_sql_tools",      "designer_sql")      # SQL 文件写完 → 回 designer_sql 确认
workflow.add_edge("design_backend_tools",  "designer_backend")  # 后端文件写完 → 回 designer_backend 继续
workflow.add_edge("design_frontend_tools", "designer_frontend") # 前端文件写完 → 回 designer_frontend 继续
workflow.add_edge("designer_context",      "designer_frontend") # API 清单提取完 → 进入前端生成

# ui 链路
workflow.add_edge("ui_planner",    "ui_tester")     # 子任务规划完成 → 开始执行第一个子任务
workflow.add_edge("ui_advance",    "ui_tester")     # 子任务步进完成 → 执行下一个子任务


# ── 条件边（由路由函数决定跳转目标）──────────────────────────────────────────────

# 入口分流：按意图路由到对应链路的起点
workflow.add_conditional_edges(
    "router", route_by_intent,
    {
        "code":    "architect",    # 写代码 → 先规划
        "chat":    "chat",         # 对话   → 直接聊
        "test":    "tester",       # 测试   → 直接跑
        "swagger": "swagger_agent",# 接口文档 → 解析生成测试
        "ui":      "ui_planner",   # UI 测试  → 先规划子任务
        "design":  "designer_plan", # 产品设计 → 阶段一-三（方案+架构+DB设计）
    }
)

# chat 链路：有搜索需求则去搜，否则结束
workflow.add_conditional_edges("chat",         route_after_chat)    # → search_tools | END

# search_tools 出口：结果交回原调用方（chat 或 coder）
workflow.add_conditional_edges("search_tools", route_after_search)  # → coder | chat

# coder 出口：搜索 / 写文件 / 步进 / 结束
workflow.add_conditional_edges("coder",        route_after_coder)   # → search_tools | tools | advance | END

# tools 出口（写文件后）：步进继续 或 先清零计数再交 tester 验收
workflow.add_conditional_edges("tools",        route_after_tools)   # → advance | reset_fix_attempts

# tester 出口：运行命令 / 超限强制结束 / 计数递增后回 coder 修复 / 通过结束
workflow.add_conditional_edges("tester",       route_after_tester)  # → test_tools | increment_fix_attempts | END

# swagger_agent 出口：继续调用工具 或 交 tester 验收
workflow.add_conditional_edges("swagger_agent", route_after_swagger) # → swagger_tools | tester

# design 链路各阶段出口
workflow.add_conditional_edges("designer_plan",     route_after_designer_plan)     # → search_tools | designer_sql
workflow.add_conditional_edges("designer_sql",      route_after_designer_sql)      # → design_sql_tools | designer_backend
workflow.add_conditional_edges("designer_backend",  route_after_designer_backend)  # → design_backend_tools | designer_frontend
workflow.add_conditional_edges("designer_frontend", route_after_designer_frontend) # → design_frontend_tools | END

# ui_tools 出口：浏览器操作完成回 ui_tester，写报告完成后直接结束
workflow.add_conditional_edges("ui_tools",     route_after_ui_tools)  # → ui_tester | END

# ui_tester 出口：操作浏览器 / 步进子任务 / 结束
workflow.add_conditional_edges("ui_tester",    route_after_ui_tester) # → ui_tools | ui_advance | END


# ── 编译为可执行应用 ──────────────────────────────────────────────────────────
app = workflow.compile()
