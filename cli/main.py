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

from cli.runner import run_agent_with_ui
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
    try:
        subprocess.run(["stty", "sane"], check=False)
    except Exception:
        pass

    console.print(Panel.fit(
        "🚀 PolyAgent  —  输入任务，Ctrl+C 或 exit 退出", style="bold magenta",
    ))
    console.print("[dim]支持：代码生成 / 产品设计 / 接口测试 / UI 测试 / 对话问答[/dim]\n")

    while True:
        try:
            task = input("\033[1;36m>\033[0m ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Bye![/dim]")
            break

        if not task:
            continue
        if task.lower() in ("exit", "quit", "q", "bye"):
            console.print("[dim]Bye![/dim]")
            break

        if run_agent_with_ui(task):
            swagger_post_run()


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
    if run_agent_with_ui(task):
        swagger_post_run()


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
