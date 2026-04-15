import subprocess
from langchain_core.tools import tool

@tool
def run_command(command: str, timeout: int = 120) -> str:
    """
    在终端执行命令并返回输出结果。
    适用于：运行测试（pytest）、检查语法（python -m py_compile）、安装依赖等。

    command: 要执行的 shell 命令
    timeout: 超时秒数，默认 120s；慢测试或集成测试可传更大的值（如 300）
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        return output.strip() if output.strip() else "(无输出)"
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT] 命令执行超时（>{timeout}s）：{command}"
