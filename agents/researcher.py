from agents.llm import get_llm
from tools.web_search import web_search
from graphs.state import AgentState

# 调研员绑定搜索工具
llm = get_llm().bind_tools([web_search])

def researcher_node(state: AgentState):
    # 从 prompts/system_prompts/researcher.txt 加载提示词
    # 提示词内容：你是技术调研专家，请根据需求搜索最新的实现方案并总结。
    system_prompt = "你是技术调研专家。请通过搜索提供详尽的技术背景和代码示例。"
    
    # 获取最后一条指令（通常是架构师发出的调研请求）
    response = llm.invoke([("system", system_prompt)] + state["messages"])
    
    return {"messages": [response]}