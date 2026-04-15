from langchain_core.tools import tool
import subprocess

@tool
def run_playwright_test(script_path: str):
    """
    运行 Playwright 测试脚本。
    会返回测试结果、截图路径以及任何报错信息。
    """
    cmd = f"npx playwright test {script_path} --reporter=json"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout # 包含详细的失败原因和 DOM 状态