from langchain_core.tools import tool
import os

@tool
def write_file(path: str, content: str):
    """在指定路径创建或覆盖文件。path 是相对路径。
    - 测试用例文件必须保存到 user/tests/ 目录下
    - 测试报告保存到 user/reports/ 目录下
    - 其他生成代码保存到 user/ 目录下
    """
    # 测试文件强制放到 user/tests/
    filename = os.path.basename(path)
    if filename.startswith("test_") or filename.endswith("_test.py"):
        if not path.startswith("user/tests/"):
            path = os.path.join("user/tests", filename)

    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    # 如果文件已存在且内容相同，跳过写入
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()
        if existing.strip() == content.strip():
            return f"⏭️ 文件内容未变化，跳过写入: {path}"

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"✅ 已写入文件: {path}"

@tool
def read_project_structure(path: str = "."):
    """读取当前目录结构，帮助架构师了解项目。"""
    items = os.listdir(path)
    return f"当前目录内容: {', '.join(items)}"