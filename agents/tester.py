from agents.llm import get_llm, node_retry
from graphs.state import AgentState
from tools.terminal import run_command
from prompts.loader import PromptLoader

prompt_loader = PromptLoader()
tester_llm = get_llm().bind_tools([run_command])

MAX_FIX_ATTEMPTS = 3


@node_retry()
def tester_node(state: AgentState):
    fix_attempts = state.get("fix_attempts", 0)
    intent = state.get("intent", "")

    if intent == "swagger":
        # swagger 链路：只做语法检查，不实际执行 HTTP 请求（服务不一定在运行）
        system_prompt = prompt_loader.load("tester_swagger")
    else:
        system_prompt = prompt_loader.load("tester")
        # 将当前重试次数注入 system_prompt，让 LLM 感知剩余机会
        remaining = MAX_FIX_ATTEMPTS - fix_attempts
        retry_hint = (
            f"\n\n# 修复次数限制\n"
            f"已修复 {fix_attempts} 次，还剩 {remaining} 次机会。"
            + ("\n**这是最后一次机会，若仍失败请输出 ##FAIL## 并说明无法修复的原因。**" if remaining <= 1 else "")
        )
        system_prompt += retry_hint

    messages = [
        ("system", system_prompt),
        *state["messages"],
    ]
    response = tester_llm.invoke(messages)
    return {"messages": [response]}
