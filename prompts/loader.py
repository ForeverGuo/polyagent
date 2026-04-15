from pathlib import Path

class PromptLoader:
    def __init__(self):
        # 获取当前文件所在目录的父目录，定位到 prompts 文件夹
        self.base_path = Path(__file__).parent / "system_prompts"

    def load(self, name: str, **kwargs) -> str:
        """
        加载指定名称的提示词文件，并支持变量替换。
        name: 文件名（不含后缀），如 'architect'
        kwargs: 要替换的变量，如 project_structure="src/"
        """
        file_path = self.base_path / f"{name}.txt"
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            # 使用 format 进行变量替换（如果有的话）
            if kwargs:
                return content.format(**kwargs)
            return content
        except FileNotFoundError:
            return f"Error: Prompt file {name}.txt not found."