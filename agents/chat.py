from agents.llm import get_llm, node_retry
from graphs.state import AgentState
from prompts.loader import PromptLoader
from tools.web_search import web_search

prompt_loader = PromptLoader()
chat_llm = get_llm().bind_tools([web_search])

@node_retry()
def chat_node(state: AgentState):
    system_prompt = prompt_loader.load("chat")
    messages = [("system", system_prompt)] + list(state["messages"])
    response = chat_llm.invoke(messages)
    return {"messages": [response]}
