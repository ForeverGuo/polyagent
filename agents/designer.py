from langchain_core.messages import HumanMessage, AIMessage
from agents.llm import get_llm, node_retry
from graphs.state import AgentState
from tools.web_search import web_search
from tools.file_ops import write_file
from prompts.loader import PromptLoader

prompt_loader = PromptLoader()

# 阶段一-三：分析为主，token 适中
_plan_llm = get_llm(max_tokens=8192).bind_tools([web_search])

# 阶段四：SQL DDL 生成，token 适中
_sql_llm = get_llm(max_tokens=8192).bind_tools([write_file])

# 阶段五：后端代码，token 需要更大（多文件）
_backend_llm = get_llm(max_tokens=16384).bind_tools([write_file])

# 阶段五→六衔接：提取 API 清单，压缩上下文，token 小即可
_context_llm = get_llm(max_tokens=4096)

# 阶段六：前端代码，token 最大（文件最多）
_frontend_llm = get_llm(max_tokens=16384).bind_tools([write_file])


@node_retry()
def designer_plan_node(state: AgentState):
    """
    设计阶段一-三：产品方案 + 技术架构 + 数据库设计。
    可使用 web_search 搜索行业最佳实践，输出完整的文字方案。
    """
    system_prompt = prompt_loader.load("designer_plan")
    messages = [("system", system_prompt), *state["messages"]]
    response = _plan_llm.invoke(messages)
    return {"messages": [response]}


@node_retry()
def designer_sql_node(state: AgentState):
    """
    设计阶段四：根据上文数据库设计生成 SQL DDL，调用 write_file 保存 .sql 和 .md。
    """
    system_prompt = prompt_loader.load("designer_sql")
    messages = [("system", system_prompt), *state["messages"]]
    response = _sql_llm.invoke(messages)
    return {"messages": [response]}


@node_retry()
def designer_backend_node(state: AgentState):
    """
    设计阶段五：根据产品方案和数据库设计生成服务端代码，逐文件调用 write_file 保存。
    """
    system_prompt = prompt_loader.load("designer_backend")
    messages = [("system", system_prompt), *state["messages"]]
    response = _backend_llm.invoke(messages)
    return {"messages": [response]}


@node_retry()
def designer_context_node(state: AgentState):
    """
    阶段五→六衔接节点：提取后端 API 清单，压缩上下文。

    到此阶段 messages 已积累大量 plan/sql/backend 内容，直接传给 designer_frontend
    会导致 context 溢出。此节点只取最近若干条消息，请 LLM 输出精简的 API 清单，
    结果存入 state["design_context"]，designer_frontend 读取该字段而非完整历史。
    """
    # 取最近 12 条消息（覆盖后端生成的主要内容，避免全量历史过长）
    recent = state["messages"][-12:]
    summary_prompt = (
        "请根据上文已生成的服务端代码，整理出完整的 API 接口清单和服务端目录结构，格式如下：\n\n"
        "## API 接口清单\n"
        "| 接口名 | 方法 | 路径 | 请求参数类型 | 响应数据类型 |\n"
        "|--------|------|------|------------|------------|\n"
        "| ...    | ...  | ...  | ...        | ...        |\n\n"
        "## 服务端目录结构\n"
        "```\n"
        "backend/\n"
        "├── ...\n"
        "```\n\n"
        "只输出上面两个内容，不要其他解释。"
    )
    messages = [*recent, ("user", summary_prompt)]
    response = _context_llm.invoke(messages)
    return {"design_context": response.content}


@node_retry()
def designer_frontend_node(state: AgentState):
    """
    设计阶段六：根据后端 API 生成对应的前端代码，逐文件调用 write_file 保存。

    使用 design_context（压缩后的 API 清单）替代完整 messages，避免 context 溢出。
    """
    system_prompt = prompt_loader.load("designer_frontend")

    # 取原始用户需求（第一条 HumanMessage）
    original_request = next(
        (m for m in state["messages"] if isinstance(m, HumanMessage)),
        state["messages"][0],
    )

    design_context = state.get("design_context", "")
    if design_context:
        # 压缩上下文：只传用户需求 + API 清单摘要
        messages = [
            ("system", system_prompt),
            original_request,
            AIMessage(content=f"以下是服务端已生成的 API 清单和目录结构，请据此生成前端代码：\n\n{design_context}"),
        ]
    else:
        # 降级：完整历史（兜底，理论上不会走到这里）
        messages = [("system", system_prompt), *state["messages"]]

    response = _frontend_llm.invoke(messages)
    return {"messages": [response]}
