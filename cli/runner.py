"""Agent 流式执行与终端 UI 渲染。"""
import re
import sys
import subprocess

from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner

console = Console()

# ── UI 工具：静默处理（只打印简短标签） ──────────────────────────────────────
_UI_TOOL_LABELS: dict[str, str] = {
    "browser_get_content":          "📄 已读取页面内容",
    "browser_get_network_requests": "📡 已读取网络请求数据",
    "browser_wait_for":             "⏳ 元素等待完成",
    "browser_get_url":              "🔗 已获取当前 URL",
    "browser_get_form_fields":      "🔍 已分析表单字段语义",
    "browser_scroll":               "↕️  已滚动页面",
    "browser_hover":                "🖱️  已悬停元素",
    "browser_select_option":        "☑️  已选择下拉选项",
    "browser_save_state":           "💾 登录态已保存",
    "browser_load_state":           "🔑 登录态已恢复",
}

# ── Spinner 文本（中间节点） ──────────────────────────────────────────────────
_SPINNER_LABELS: dict[str, str] = {
    "router":           "🔀 分析意图...",
    "architect":        "📐 规划任务...",
    "advance":          "⏩ 进入下一步...",
    "ui_planner":       "🗂️  规划 UI 测试子任务...",
    "ui_advance":       "⏩ 进入下一个子任务...",
    "designer_context": "🔗 整理后端 API 清单，准备生成前端...",
}

# ── 设计节点（工具调用时更新 Spinner） ───────────────────────────────────────
_DESIGN_NODES: dict[str, str] = {
    "designer_plan":     "🔍 调研行业方案...",
    "designer_sql":      "🗄️  生成 SQL DDL...",
    "designer_backend":  "⚙️  生成服务端代码...",
    "designer_frontend": "🎨 生成前端代码...",
    "designer_context":  "🔗 整理后端 API 清单，准备生成前端...",
}


class _Spinner:
    def __init__(self):
        self._live: Live | None = None

    def start(self, text: str = "[bold cyan]🤔 Thinking...[/bold cyan]"):
        if not self._live:
            self._live = Live(Spinner("dots", text=text), console=console, refresh_per_second=10)
            self._live.start()

    def update(self, text: str):
        if self._live:
            self._live.update(Spinner("dots", text=text))

    def stop(self):
        if self._live:
            self._live.stop()
            self._live = None


def _handle_tool_msg(chunk: ToolMessage, node: str):
    """处理 ToolMessage，打印对应节点的工具结果提示。"""
    if node == "test_tools":
        console.print("  [bold magenta]🧪 测试执行完毕[/bold magenta]")
    elif node == "ui_tools":
        tool_name = getattr(chunk, "name", "") or ""
        if tool_name in _UI_TOOL_LABELS:
            console.print(f"  [bold cyan]{_UI_TOOL_LABELS[tool_name]}[/bold cyan]")
        else:
            summary = chunk.content[:120] + ("..." if len(chunk.content) > 120 else "")
            console.print(f"  [bold cyan]🌐 {summary}[/bold cyan]")
    elif node in ("design_sql_tools", "design_backend_tools", "design_frontend_tools"):
        console.print(f"  [bold yellow]💾 {chunk.content}[/bold yellow]")
    else:
        console.print(f"  [bold green]✅ {chunk.content}[/bold green]")


def run_agent_with_ui(user_task: str) -> bool:
    """运行 agent，返回 True 表示经过了 swagger 生成链路。"""
    from graphs.workflow import app

    inputs = {
        "messages": [HumanMessage(content=user_task)],
        "task_plan": [], "current_step": 0, "intent": "",
        "ui_plan": [], "ui_step": 0, "design_context": "", "fix_attempts": 0,
    }

    spinner = _Spinner()
    saw_swagger = False
    in_ai_stream = False
    completed = False

    def write(text: str):
        sys.stdout.write(text)
        sys.stdout.flush()

    spinner.start()

    try:
        for mode, payload in app.stream(inputs, stream_mode=["messages", "updates"]):
            # ── 状态更新事件（非消息流，用于检测子任务切换等纯 state 变更节点）──
            if mode == "updates":
                for node_name, state_update in payload.items():
                    if node_name == "ui_advance":
                        # 子任务步进：重置 AI 流状态，重启 spinner 给用户明确的进度反馈
                        # 此处是子任务 1 结束 → 子任务 2 开始的间隙，原本完全无视觉输出
                        if in_ai_stream:
                            write("\n")
                            in_ai_stream = False
                        next_step = state_update.get("ui_step", 1)
                        spinner.stop()
                        spinner.start(f"[bold cyan]⏩ 子任务 {next_step} 准备中...[/bold cyan]")
                continue

            # ── 消息流事件（LLM token / ToolMessage）──────────────────────────
            chunk, metadata = payload
            node = metadata.get("langgraph_node", "")

            if node in ("swagger_agent", "swagger_tools"):
                saw_swagger = True

            if isinstance(chunk, ToolMessage):
                if in_ai_stream:
                    write("\n")
                    in_ai_stream = False
                spinner.stop()
                _handle_tool_msg(chunk, node)
                spinner.start()
                continue

            if not isinstance(chunk, AIMessageChunk) or not chunk.content:
                continue

            if node in _SPINNER_LABELS:
                spinner.update(f"[bold cyan]{_SPINNER_LABELS[node]}[/bold cyan]")
                continue

            if node in _DESIGN_NODES and chunk.tool_calls and not chunk.content:
                spinner.update(f"[bold cyan]{_DESIGN_NODES[node]}[/bold cyan]")
                continue

            if node == "ui_tester" and chunk.tool_calls and not chunk.content:
                spinner.update("[bold cyan]🌐 UI 测试中...[/bold cyan]")
                continue

            if not in_ai_stream:
                spinner.stop()
                write("\n")
                in_ai_stream = True
            write(chunk.content)

        completed = True

    except KeyboardInterrupt:
        pass

    if in_ai_stream:
        write("\n")
    spinner.stop()

    try:
        subprocess.run(["stty", "sane"], check=False)
    except Exception:
        pass

    if completed:
        console.print("\n[bold reverse green] ✨ Done！ [/bold reverse green]")
    else:
        console.print("\n[bold yellow]⏹  已中断，输入下一个任务[/bold yellow]")

    return saw_swagger and completed


# ─────────────────────────────────────────────────────────────────────────────
# Skill 保存提示
# ─────────────────────────────────────────────────────────────────────────────

def prompt_save_skill(original_task: str) -> None:
    """任务完成后询问用户是否保存为 Skill。"""
    from cli.skills import make_skill, save_skill

    console.print()
    try:
        answer = input("\033[2m  💾 保存为 Skill 以便复用？[y/N] \033[0m").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return

    if answer not in ("y", "yes"):
        return

    try:
        name = input("  Skill 名称（如 my-api-test）: ").strip()
        if not name:
            console.print("[dim]  已取消[/dim]")
            return
        if not re.match(r"^[\w\-]+$", name):
            console.print("[bold red]  名称只允许字母、数字、连字符和下划线[/bold red]")
            return

        description = input("  描述（可选，回车跳过）: ").strip()

        console.print(f"  [dim]Prompt 模板（直接回车使用原始任务；可用 {{变量}} 作为占位符）:[/dim]")
        console.print(f"  [dim]当前：{original_task}[/dim]")
        prompt_input = input("  > ").strip()
        prompt = prompt_input if prompt_input else original_task

        # 收集环境变量
        env: dict[str, str] = {}
        console.print("  [dim]绑定环境变量（如 BASE_URL=http://... ），回车结束：[/dim]")
        console.print("  [dim]⚠️  TOKEN / API_KEY 等敏感值建议留空，运行时从 .env 读取[/dim]")
        while True:
            pair = input("  ENV > ").strip()
            if not pair:
                break
            if "=" in pair:
                k, v = pair.split("=", 1)
                k = k.strip()
                # 敏感键自动置空并提示
                _sensitive = ("token", "key", "secret", "password", "passwd", "pwd")
                if any(s in k.lower() for s in _sensitive) and v.strip():
                    console.print(f"  [bold yellow]  ⚠️  {k} 含敏感信息，已置空（请在 .env 中配置）[/bold yellow]")
                    env[k] = ""
                else:
                    env[k] = v.strip()
            else:
                console.print(f"  [dim]格式错误，跳过：{pair}[/dim]")

    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]  已取消[/dim]")
        return

    skill = make_skill(name, description, prompt, env)
    path = save_skill(skill)
    console.print(f"\n[bold green]  ✅ Skill 已保存：{name}[/bold green]  [dim]{path}[/dim]")
    console.print(f"  [dim]运行：polyagent skill run {name}[/dim]")
