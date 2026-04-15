from agents.llm import get_llm, node_retry
from graphs.state import AgentState
from tools.swagger_parser import fetch_swagger
from tools.file_reader import read_spec_file
from tools.file_ops import write_file
from prompts.loader import PromptLoader

prompt_loader = PromptLoader()
swagger_llm = get_llm().bind_tools([fetch_swagger, read_spec_file, write_file])


@node_retry()
def swagger_agent_node(state: AgentState):
    """解析 Swagger / txt / md 规范文档并生成 pytest 测试用例"""
    system_prompt = prompt_loader.load("swagger")
    messages = [("system", system_prompt), *state["messages"]]
    response = swagger_llm.invoke(messages)
    return {"messages": [response]}
