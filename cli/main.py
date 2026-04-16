import os
import sys

# 确保 stdin/stdout 使用 UTF-8
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from cli.runner import run_agent_with_ui, prompt_save_skill
from cli.report import swagger_post_run

# ── Provider 预设 ─────────────────────────────────────────────────────────────
_PROVIDER_PRESETS: dict[str, tuple[str, str]] = {
    "deepseek":    ("https://api.deepseek.com/v1",        "deepseek-chat"),
    "openai":      ("https://api.openai.com/v1",          "gpt-4o"),
    "siliconflow": ("https://api.siliconflow.cn/v1",      "Pro/zai-org/GLM-4.7"),
    "ollama":      ("http://localhost:11434/v1",          "llama3.2"),
    "vllm":        ("http://localhost:8000/v1",           ""),
    "groq":        ("https://api.groq.com/openai/v1",    "llama-3.3-70b-versatile"),
    "together":    ("https://api.together.xyz/v1",        "meta-llama/Llama-3-70b-chat-hf"),
}

console = Console()
__version__ = "0.1.0"

cli = typer.Typer(
    name="polyagent",
    help="Multi-Agent 协作框架，支持代码生成、产品设计、接口测试、UI 自动化等场景。",
    add_completion=False,
    no_args_is_help=False,
)


# ─────────────────────────────────────────────────────────────────────────────
# 交互循环
# ─────────────────────────────────────────────────────────────────────────────

def _interactive_loop():
    import subprocess
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.styles import Style
    from prompt_toolkit.formatted_text import HTML

    try:
        subprocess.run(["stty", "sane"], check=False)
    except Exception:
        pass

    console.print(Panel.fit(
        "🚀 PolyAgent  —  输入任务，Ctrl+C 或 exit 退出", style="bold magenta",
    ))
    console.print("[dim]支持：代码生成 / 产品设计 / 接口测试 / UI 测试 / 对话问答[/dim]")
    console.print("[dim]↑↓ 历史记录  Ctrl+R 搜索历史[/dim]\n")

    history_file = Path.home() / ".polyagent" / "history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    session: PromptSession = PromptSession(
        history=FileHistory(str(history_file)),
        style=Style.from_dict({"prompt": "bold ansicyan"}),
        mouse_support=False,
    )

    while True:
        try:
            task = session.prompt(HTML("<ansicyan><b>❯</b></ansicyan> ")).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Bye![/dim]")
            break

        if not task:
            continue
        if task.lower() in ("exit", "quit", "q", "bye"):
            console.print("[dim]Bye![/dim]")
            break

        completed = run_agent_with_ui(task)
        if completed:
            swagger_post_run()
            prompt_save_skill(task)


# ─────────────────────────────────────────────────────────────────────────────
# CLI 子命令
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("init", help="引导式配置向导，将 LLM 及工具 API Key 保存到 ~/.polyagent/config.env")
def cmd_init():
    config_dir  = Path.home() / ".polyagent"
    config_file = config_dir / "config.env"

    console.print(Panel.fit("🛠️  PolyAgent 配置向导", style="bold cyan"))

    if config_file.exists():
        if not typer.confirm(f"  已存在配置文件 {config_file}，是否覆盖？", default=False):
            console.print("[dim]已取消，保留原有配置。[/dim]")
            raise typer.Exit()

    console.print()
    console.print("[bold]── LLM 配置（必填）──────────────────────────────[/bold]")

    providers = {
        "1": ("SiliconFlow（推荐，国内免费额度）", "https://api.siliconflow.cn/v1", "Pro/zai-org/GLM-4.7"),
        "2": ("OpenAI",                          "https://api.openai.com/v1",       "gpt-4o"),
        "3": ("Azure OpenAI",                    "",                                "gpt-4o"),
        "4": ("Ollama（本地）",                  "http://localhost:11434/v1",        "llama3.2"),
        "5": ("自定义",                          "",                                ""),
    }

    for k, (name, _, _) in providers.items():
        console.print(f"  {k}. {name}")

    choice = typer.prompt("\n  选择服务商", default="1")
    _, default_base, default_model = providers.get(choice, ("", "", ""))

    api_base = typer.prompt("  API_BASE",  default=default_base)
    api_key  = typer.prompt("  API_KEY",   hide_input=True)
    model    = typer.prompt("  MODEL",     default=default_model)

    console.print()
    console.print("[bold]── 工具 API Keys（可选，回车跳过）────────────────[/bold]")
    console.print("  [dim]Tavily 搜索：联网问答功能需要，免费申请 https://tavily.com[/dim]")
    tavily_key = typer.prompt("  TAVILY_API_KEY", default="", show_default=False)

    config_dir.mkdir(parents=True, exist_ok=True)
    content = f"# PolyAgent 配置文件，由 polyagent init 生成\nAPI_KEY={api_key}\nAPI_BASE={api_base}\nMODEL={model}\n"
    if tavily_key:
        content += f"TAVILY_API_KEY={tavily_key}\n"
    config_file.write_text(content, encoding="utf-8")
    config_file.chmod(0o600)

    console.print()
    console.print(f"[bold green]✅ 配置已保存到 {config_file}[/bold green]")
    console.print("[dim]运行 [bold]polyagent[/bold] 开始使用[/dim]")


@cli.command("run", help="执行单条任务（适合脚本调用）")
def cmd_run(
    task:     str           = typer.Argument(..., help="要执行的任务描述"),
    provider: Optional[str] = typer.Option(None, "--provider", "-p", help=f"Provider 快捷名：{', '.join(_PROVIDER_PRESETS)}"),
    model:    Optional[str] = typer.Option(None, "--model",    "-m", help="模型名称，覆盖配置"),
    api_base: Optional[str] = typer.Option(None, "--api-base", "-b", help="API base URL，覆盖配置"),
    api_key:  Optional[str] = typer.Option(None, "--api-key",  "-k", help="API Key，覆盖配置"),
):
    _apply_provider_flags(provider, model, api_base, api_key)
    _check_config()
    completed = run_agent_with_ui(task)
    if completed:
        swagger_post_run()
        prompt_save_skill(task)


# ─────────────────────────────────────────────────────────────────────────────
# skill 子命令组
# ─────────────────────────────────────────────────────────────────────────────

skill_cli = typer.Typer(name="skill", help="管理用户自定义 Skill（可复用的任务模板）。", add_completion=False)
cli.add_typer(skill_cli, name="skill")


@skill_cli.command("list", help="列出所有已保存的 Skills。")
def skill_list():
    from cli.skills import list_skills
    skills = list_skills()
    if not skills:
        console.print("[dim]暂无 Skill，运行任务后可保存。[/dim]")
        return
    console.print(f"\n[bold cyan]📦 已保存 {len(skills)} 个 Skill：[/bold cyan]\n")
    for s in skills:
        vars_hint = (f"  变量: {', '.join('{'+v+'}' for v in s['variables'])}" if s.get("variables") else "")
        env_hint  = (f"  ENV: {', '.join(s['env'])}" if s.get("env") else "")
        console.print(f"  [bold]{s['name']}[/bold]  [dim]{s.get('description', '')}[/dim]")
        console.print(f"    [dim]{s['prompt'][:80]}{'...' if len(s['prompt'])>80 else ''}[/dim]")
        if vars_hint: console.print(f"    [dim]{vars_hint}[/dim]")
        if env_hint:  console.print(f"    [dim]{env_hint}[/dim]")
        console.print()


@skill_cli.command("show", help="查看 Skill 详情。")
def skill_show(name: str = typer.Argument(..., help="Skill 名称")):
    from cli.skills import load_skill
    import json
    skill = load_skill(name)
    if not skill:
        console.print(f"[bold red]Skill '{name}' 不存在[/bold red]")
        raise typer.Exit(code=1)
    console.print_json(json.dumps(skill, ensure_ascii=False, indent=2))


@skill_cli.command("run", help="运行指定 Skill。")
def skill_run(
    name: str = typer.Argument(..., help="Skill 名称"),
    var:  list[str] = typer.Option([], "--var", "-v", help="变量赋值，格式 key=value（可多次）"),
    provider: Optional[str] = typer.Option(None, "--provider", "-p", help=f"Provider 快捷名：{', '.join(_PROVIDER_PRESETS)}"),
    model:    Optional[str] = typer.Option(None, "--model",    "-m", help="模型名称"),
    api_base: Optional[str] = typer.Option(None, "--api-base", "-b", help="API base URL"),
    api_key:  Optional[str] = typer.Option(None, "--api-key",  "-k", help="API Key"),
):
    from cli.skills import load_skill, expand_skill, missing_variables

    skill = load_skill(name)
    if not skill:
        console.print(f"[bold red]Skill '{name}' 不存在，运行 [cyan]polyagent skill list[/cyan] 查看可用列表[/bold red]")
        raise typer.Exit(code=1)

    # 解析 --var key=value
    overrides: dict[str, str] = {}
    for pair in var:
        if "=" in pair:
            k, v = pair.split("=", 1)
            overrides[k.strip()] = v.strip()
        else:
            console.print(f"[bold red]--var 格式错误（应为 key=value）：{pair}[/bold red]")
            raise typer.Exit(code=1)

    # 交互式补充缺失变量
    for missing in missing_variables(skill, overrides):
        try:
            val = input(f"  输入 {{{missing}}}: ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]已取消[/dim]")
            raise typer.Exit()
        overrides[missing] = val

    prompt, env = expand_skill(skill, overrides)

    # 将 skill 绑定的 env 写入环境变量
    for k, v in env.items():
        if v:
            os.environ[k] = v

    _apply_provider_flags(provider, model, api_base, api_key)
    _check_config()

    console.print(f"\n[bold cyan]▶ 运行 Skill：{name}[/bold cyan]")
    if skill.get("description"):
        console.print(f"  [dim]{skill['description']}[/dim]")
    console.print(f"  [dim]Prompt: {prompt}[/dim]\n")

    completed = run_agent_with_ui(prompt)
    if completed:
        swagger_post_run()


@skill_cli.command("new", help="交互式创建新 Skill（无需先运行任务）。")
def skill_new():
    from cli.skills import make_skill, save_skill, load_skill
    import re as _re

    console.print(Panel.fit("✨ 新建 Skill", style="bold cyan"))
    try:
        name = input("  Skill 名称（如 my-api-test）: ").strip()
        if not name:
            raise typer.Exit()
        if not _re.match(r"^[\w\-]+$", name):
            console.print("[bold red]名称只允许字母、数字、连字符和下划线[/bold red]")
            raise typer.Exit(code=1)
        if load_skill(name):
            if not typer.confirm(f"  Skill '{name}' 已存在，覆盖？", default=False):
                raise typer.Exit()

        description = input("  描述（可选）: ").strip()
        console.print("  [dim]Prompt 模板（可用 {变量} 作为占位符，如：解析 {spec_file} 生成测试）：[/dim]")
        prompt = input("  > ").strip()
        if not prompt:
            console.print("[bold red]Prompt 不能为空[/bold red]")
            raise typer.Exit(code=1)

        env: dict[str, str] = {}
        console.print("  [dim]绑定环境变量（格式 KEY=value），回车结束：[/dim]")
        console.print("  [dim]⚠️  TOKEN / API_KEY 等敏感值建议留空，运行时从 .env 读取[/dim]")
        _sensitive = ("token", "key", "secret", "password", "passwd", "pwd")
        while True:
            pair = input("  ENV > ").strip()
            if not pair:
                break
            if "=" in pair:
                k, v = pair.split("=", 1)
                k = k.strip()
                if any(s in k.lower() for s in _sensitive) and v.strip():
                    console.print(f"  [bold yellow]  ⚠️  {k} 含敏感信息，已置空（请在 .env 中配置）[/bold yellow]")
                    env[k] = ""
                else:
                    env[k] = v.strip()

    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]已取消[/dim]")
        raise typer.Exit()

    skill = make_skill(name, description, prompt, env)
    path = save_skill(skill)
    console.print(f"\n[bold green]✅ Skill 已创建：{name}[/bold green]  [dim]{path}[/dim]")
    console.print(f"  [dim]运行：polyagent skill run {name}[/dim]")


@skill_cli.command("delete", help="删除 Skill。")
def skill_delete(name: str = typer.Argument(..., help="Skill 名称")):
    from cli.skills import delete_skill, load_skill
    if not load_skill(name):
        console.print(f"[bold red]Skill '{name}' 不存在[/bold red]")
        raise typer.Exit(code=1)
    if typer.confirm(f"  确认删除 Skill '{name}'？", default=False):
        delete_skill(name)
        console.print(f"[bold green]✅ 已删除：{name}[/bold green]")
    else:
        console.print("[dim]已取消[/dim]")


@cli.callback(invoke_without_command=True)
def cmd_default(
    ctx:      typer.Context,
    version:  bool          = typer.Option(False, "--version", "-v", help="显示版本号", is_eager=True),
    provider: Optional[str] = typer.Option(None, "--provider", "-p", help=f"Provider 快捷名：{', '.join(_PROVIDER_PRESETS)}"),
    model:    Optional[str] = typer.Option(None, "--model",    "-m", help="模型名称，覆盖配置"),
    api_base: Optional[str] = typer.Option(None, "--api-base", "-b", help="API base URL，覆盖配置"),
    api_key:  Optional[str] = typer.Option(None, "--api-key",  "-k", help="API Key，覆盖配置"),
):
    if version:
        console.print(f"PolyAgent {__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        _apply_provider_flags(provider, model, api_base, api_key)
        _check_config()
        _interactive_loop()


# ─────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────────────────────

def _apply_provider_flags(provider, model, api_base, api_key):
    """将 CLI flags 写入 os.environ，在 llm.py 延迟导入前生效。"""
    if provider:
        provider = provider.lower()
        if provider not in _PROVIDER_PRESETS:
            console.print(f"[bold red]未知 provider: {provider}[/bold red]\n  可选值: {', '.join(_PROVIDER_PRESETS)}")
            raise typer.Exit(code=1)
        preset_base, preset_model = _PROVIDER_PRESETS[provider]
        if not api_base and preset_base: os.environ["API_BASE"] = preset_base
        if not model and preset_model:   os.environ["MODEL"]    = preset_model

    if api_base: os.environ["API_BASE"] = api_base
    if model:    os.environ["MODEL"]    = model
    if api_key:  os.environ["API_KEY"]  = api_key

    if any([provider, api_base, model, api_key]):
        console.print(f"[dim]LLM: {os.environ.get('MODEL', '?')}  @  {os.environ.get('API_BASE', '?')}[/dim]")


def _check_config():
    """未配置时引导用户运行 init。"""
    config_file = Path.home() / ".polyagent" / "config.env"
    if not config_file.exists() and not Path(".env").exists() and not os.getenv("API_KEY"):
        console.print("[bold yellow]⚠️  未检测到 LLM 配置[/bold yellow]")
        console.print("  请先运行 [bold cyan]polyagent init[/bold cyan] 完成配置，或在当前目录创建 .env 文件。")
        raise typer.Exit(code=1)


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

def main_cli():
    cli()


if __name__ == "__main__":
    main_cli()
