"""接口测试报告生成与 pytest 执行。"""
import os
import sys
import json as _json
import glob as _glob
import datetime
import re
import subprocess
import platform

from rich.console import Console

console = Console()

# 产物输出根目录，可通过环境变量 POLYAGENT_OUTPUT_DIR 覆盖，默认 "user"
_OUT = os.environ.get("POLYAGENT_OUTPUT_DIR", "user")


def _parse_req_log(stdout: str) -> dict:
    """从测试 stdout 中解析 _req() 打印的结构化日志。"""
    result: dict = {}
    for line in (stdout or "").splitlines():
        line = line.strip()
        if line.startswith("[REQ] "):
            parts = line[6:].split(" ", 1)
            result["method"] = parts[0]
            result["url"]    = parts[1] if len(parts) > 1 else ""
        elif line.startswith("[HEADERS] "):
            result["headers"] = line[10:]
        elif line.startswith("[PARAMS] "):
            result["params"] = line[9:]
        elif line.startswith("[BODY] "):
            result["body"] = line[7:]
        elif line.startswith("[RSP] "):
            m = re.match(r"^(\d+)\s*(.*)", line[6:], re.DOTALL)
            if m:
                result["status"]   = m.group(1)
                result["response"] = m.group(2)[:600]
    return result


def _generate_md_report(json_path: str, md_path: str, base_url: str, token_set: bool) -> None:
    """将 pytest-json-report 生成的 JSON 结果转换为 Markdown 报告。"""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
    except Exception:
        return

    summary  = data.get("summary", {})
    tests    = data.get("tests", [])
    total, passed, failed = summary.get("total", 0), summary.get("passed", 0), summary.get("failed", 0)
    errors, skipped       = summary.get("errors", 0), summary.get("skipped", 0)
    duration   = summary.get("duration", 0.0)
    now_str    = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status_icon = "✅ 全部通过" if (failed == 0 and errors == 0) else "❌ 存在失败"
    auth_desc   = "Bearer Token + Token Header" if token_set else "无（未配置 Token）"

    lines: list[str] = [
        "# API 接口测试报告", "",
        f"> **生成时间**：{now_str}  ",
        f"> **测试地址**：`{base_url}`  ",
        f"> **认证方式**：{auth_desc}  ",
        f"> **总耗时**：{duration:.2f}s  ",
        f"> **结果**：{status_icon}", "",
        "## 汇总", "",
        "| 总用例 | ✅ 通过 | ❌ 失败 | ⚠️ 错误 | ⏭️ 跳过 |",
        "|:------:|:------:|:------:|:------:|:------:|",
        f"| {total} | {passed} | {failed} | {errors} | {skipped} |", "",
    ]

    # 失败 / 错误详情
    bad_tests = [t for t in tests if t.get("outcome") in ("failed", "error")]
    if bad_tests:
        lines += ["## ❌ 失败 / 错误详情", ""]
        for t in bad_tests:
            lines.append(f"### `{t['nodeid']}`")
            longrepr = (t.get("call") or t.get("setup") or {}).get("longrepr", "")
            if longrepr:
                snippet = longrepr[:1200] + ("\n...(已截断)" if len(longrepr) > 1200 else "")
                lines += ["```", snippet, "```"]
            lines.append("")

    # 全部用例明细
    _ICONS = {"passed": "✅", "failed": "❌", "error": "⚠️", "skipped": "⏭️"}
    lines += ["## 用例明细", ""]

    for t in tests:
        outcome  = t.get("outcome", "unknown")
        icon     = _ICONS.get(outcome, "❓")
        call     = t.get("call") or {}
        dur      = call.get("duration", 0.0)
        short_id = t.get("nodeid", "").split("/")[-1]

        lines.append(f"### {icon} `{short_id}`")
        lines.append("")

        req = _parse_req_log(call.get("stdout", ""))
        if req:
            lines += ["| 项目 | 内容 |", "|:-----|:-----|"]
            if req.get("method"):
                lines.append(f"| 请求方法 | `{req['method']}` |")
            if req.get("url"):
                lines.append(f"| 请求地址 | `{req['url']}` |")
            if req.get("headers"):
                lines.append(f"| 请求头 | `{req['headers']}` |")
            if req.get("params"):
                lines.append(f"| 请求参数 | `{req['params']}` |")
            if req.get("body"):
                lines.append(f"| 请求体 | `{req['body']}` |")
            if req.get("status"):
                lines.append(f"| 响应状态 | `{req['status']}` |")
            if req.get("response"):
                lines.append(f"| 响应数据 | `{req['response']}` |")
            lines.append(f"| 测试结果 | {icon} **{outcome}**（{dur:.3f}s）|")
        else:
            lines.append(f"- **结果**：{icon} {outcome}（{dur:.3f}s）")

        if outcome in ("failed", "error"):
            longrepr = call.get("longrepr", "")
            if longrepr:
                lines += ["", "**错误信息**：", "```", longrepr[:600], "```"]

        lines.append("")

    lines += ["---", "*由 PolyAgent 自动生成*", ""]

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def swagger_post_run():
    """swagger 生成完成后，询问是否运行测试，收集凭证后执行 pytest 并生成 MD 报告。"""
    test_files = sorted(_glob.glob(os.path.join(_OUT, "tests", "test_api_*.py")))
    if not test_files:
        return

    console.print()
    console.print("[bold cyan]┌──────────────────────────────────────────────────┐[/bold cyan]")
    console.print("[bold cyan]│  📋 测试文件已生成，是否立即运行接口测试？       │[/bold cyan]")
    for f in test_files:
        console.print(f"[bold cyan]│    · {f:<44}│[/bold cyan]")
    console.print("[bold cyan]│  直接回车跳过                                    │[/bold cyan]")
    console.print("[bold cyan]└──────────────────────────────────────────────────┘[/bold cyan]")
    console.print()

    try:
        base_url = input("  BASE_URL（如 http://localhost:8000，回车跳过）: ").strip()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]已跳过测试[/dim]")
        return

    if not base_url:
        console.print("[dim]  已跳过测试[/dim]")
        return

    try:
        token      = input("  TOKEN（Bearer Token，无则回车跳过）: ").strip()
        login_user = input("  LOGIN_USERNAME（自动登录用户名，无则回车跳过）: ").strip()
        login_pass = input("  LOGIN_PASSWORD: ").strip() if login_user else ""
    except (KeyboardInterrupt, EOFError):
        token = login_user = login_pass = ""

    # 构建环境变量
    env = os.environ.copy()
    env["BASE_URL"] = base_url
    if token:
        env["TOKEN"] = token
    elif "TOKEN" in env:
        del env["TOKEN"]
    if login_user:
        env["LOGIN_USERNAME"] = login_user
        env["LOGIN_PASSWORD"] = login_pass

    config_parts = [f"BASE_URL={base_url}"]
    if token:      config_parts.append("TOKEN=***")
    if login_user: config_parts.append(f"LOGIN_USERNAME={login_user}")
    console.print(f"\n[dim]  {'  '.join(config_parts)}[/dim]")

    # 独立测试 venv，不污染主项目依赖
    venv_dir = os.path.join(_OUT, ".venv")
    test_py  = os.path.join(venv_dir, "Scripts" if platform.system() == "Windows" else "bin", "python")

    if not os.path.exists(test_py):
        console.print(f"[dim]  初始化测试环境（{venv_dir}）...[/dim]")
        subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)

    subprocess.run(
        [test_py, "-m", "pip", "install", "pytest", "requests", "pytest-json-report", "-q"],
        env=env, check=False,
    )

    json_report_ok = subprocess.run(
        [test_py, "-c", "import pytest_jsonreport"], env=env, capture_output=True,
    ).returncode == 0

    reports_dir = os.path.join(_OUT, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    ts          = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_json = os.path.join(reports_dir, f"report_{ts}.json")
    report_md   = os.path.join(reports_dir, f"report_{ts}.md")

    console.print()
    console.print("[bold cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]")
    console.print("[bold cyan]  🧪 正在运行接口测试...                          [/bold cyan]")
    console.print("[bold cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]")
    console.print()

    pytest_cmd = [test_py, "-m", "pytest", os.path.join(_OUT, "tests"), "-v", "--tb=line"]
    if json_report_ok:
        pytest_cmd += ["--json-report", f"--json-report-file={report_json}"]

    result = subprocess.run(pytest_cmd, env=env)

    console.print()
    console.print("[bold cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]")
    if result.returncode == 0:
        console.print("[bold reverse green]  ✅ 所有接口测试通过！  [/bold reverse green]")
    else:
        console.print("[bold reverse red]  ❌ 部分测试失败，请查看下方报告  [/bold reverse red]")
    console.print()

    if json_report_ok and os.path.exists(report_json):
        _generate_md_report(report_json, report_md, base_url, bool(token))
        console.print(f"[bold green]📄 测试报告已保存：{report_md}[/bold green]")
        try:
            with open(report_md, "r", encoding="utf-8") as f:
                preview = "\n".join(f.read().split("\n")[:40])
            console.print()
            console.print(f"[dim]{preview}[/dim]")
        except Exception:
            pass
    elif not json_report_ok:
        console.print("[dim]  pytest-json-report 未能安装，已跳过 MD 报告生成。[/dim]")
    console.print()
